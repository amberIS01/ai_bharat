"""Per (criterion × bidder) fact extraction via Gemini 2.5 Flash.

For each criterion the criteria_extractor produced and each bidder we have in
the DB, ask Gemini to extract the single value the criterion is about
(turnover figure / GSTIN / DSC validity / list of works / ISO cert number),
the evidence block_ids backing that value, and a self-elicited confidence.

Confidence aggregation on persist:
    Fact.ocr_confidence = min(
        gemini_self_confidence,
        min(b.confidence for b in cited evidence Blocks)  if any cited
    )
This is what protects the demo's ABSTAIN trigger: even if Gemini reads the
GSTIN at 0.90, if the cited evidence chain includes a 0.65-confidence block
nearby, the Fact's effective confidence drops below the 0.85 threshold and
the Day-4 Rego rule emits ABSTAIN.
"""

from __future__ import annotations

import logging
import re
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.db import transaction

from core.extraction.gemini_client import call_gemini_structured
from core.extraction.schemas import FactExtraction
from core.models import (
    Block,
    Criterion,
    Document,
    DocumentType,
    Fact,
)
from core.parsing.gemini_vision_ocr import rasterize_pdf_page

log = logging.getLogger(__name__)


# Per-block manifest text truncation
MANIFEST_TEXT_MAX = 320


# Heuristic: which bidder document types are most likely to contain evidence
# for a given criterion. Used to scope the page images we ship; the text
# manifest always contains every bidder block regardless.
DOC_HINTS_BY_CRITERION_TITLE: list[tuple[re.Pattern[str], list[str]]] = [
    (re.compile(r"turnover|annual.*financial|profit.*loss", re.I),
     [DocumentType.BIDDER_AUDIT.value]),
    (re.compile(r"\bGST\b|goods.*services.*tax", re.I),
     [DocumentType.BIDDER_GST.value]),
    (re.compile(r"\bDSC\b|digital.*signature|class[ -]?3", re.I),
     [DocumentType.BIDDER_DSC.value]),
    (re.compile(r"experience|past.*performance|similar.*work|completed.*work", re.I),
     [DocumentType.BIDDER_EXPERIENCE.value]),
    (re.compile(r"\bISO\b|9001|quality.*management", re.I),
     [DocumentType.BIDDER_ISO.value]),
]


FACT_PROMPT = """\
You are extracting evidence for a single eligibility criterion from one \
bidder's submission. Your output must be the literal value the criterion is \
about (a number, a registration ID, a date, a list — whatever the criterion \
asks for), plus block_ids citing where you found it.

CRITERION TO EVALUATE:

  Code:         {code}
  Title:        {title}
  Type:         {type}
  Mandatory:    {mandatory}
  Requirement:  {requirement}

You have BOTH the bidder's parsed text blocks (manifest below) AND any \
attached page images for the documents most likely to contain the answer.

Each manifest entry has a globally unique `block_id` (Django PK), plus \
`document_id`, `document_type`, `page`, `source` (DOCLING vs GEMINI_VISION), \
and `confidence`. Cite blocks by their `block_id` — never by `block_index` \
which only counts within a single document.

Return:
  * found             — true if the value is present in the bidder's submission.
  * value             — the extracted value as a literal string. For amounts, \
keep the original Indian formatting (e.g. "Rs. 6,80,00,000" / "Six Crore Eighty Lakh"). \
For lists of completed works, join with " | " AND include the work value in \
parentheses after each work name, e.g. "Construction of X (Rs. 1,85,00,000) \
| Renovation of Y (Rs. 2,40,00,000)". The downstream policy engine relies \
on the rupee figure being present after every work.
  * confidence        — your confidence (0.0–1.0). Use < 0.85 whenever OCR \
noise, ambiguity, missing data, or partial information makes you unsure.
  * reason_if_low     — one-line explanation when confidence < 0.85, otherwise empty.
  * source_block_ids  — list of `block_id` integers from the manifest backing \
the value. Cite enough blocks to triangulate: include the primary block AND \
adjacent context blocks if they share the same noise / legibility characteristics. \
If any cited block has confidence < 0.85, your final `confidence` MUST also be \
< 0.85 — the document quality is part of the answer's reliability.
  * notes             — one-line note for the human reviewer (optional).

Be honest about confidence. The downstream policy engine ABSTAINS rather \
than failing the bidder when confidence < 0.85, so never silently round up.

If the value is genuinely absent from the bidder's submission, set \
found=false, value="", and confidence reflecting how sure you are it's missing.
"""


# ---------------------------------------------------------------------------
# Manifest + image helpers
# ---------------------------------------------------------------------------

def _build_bidder_manifest(blocks_qs) -> list[dict]:
    out: list[dict] = []
    for b in blocks_qs:
        text = (b.text or "").strip()
        if len(text) > MANIFEST_TEXT_MAX:
            text = text[:MANIFEST_TEXT_MAX].rstrip() + "..."
        out.append({
            "block_id": b.id,                # globally-unique Django PK — cite this
            "block_index": b.block_index,    # within-document index, for human readability only
            "document_id": b.document_id,
            "document_type": b.document.type,
            "page": b.page_no,
            "source": b.source,
            "confidence": round(float(b.confidence), 2),
            "reason_if_low": b.reason_if_low or "",
            "text": text,
        })
    return out


def _likely_doc_types(criterion: Criterion) -> list[str]:
    """Heuristic: pick the bidder doc types most likely to contain evidence."""
    blob = f"{criterion.code} {criterion.title} {criterion.requirement_text}"
    for rx, types_ in DOC_HINTS_BY_CRITERION_TITLE:
        if rx.search(blob):
            return types_
    # Fallback: include cover + audit so the model has *some* image context
    return [DocumentType.BIDDER_COVER.value, DocumentType.BIDDER_AUDIT.value]


def _rasterize_documents(documents: list[Document], dpi: int = 150) -> list[bytes]:
    """Return PNG bytes for every page of every document, in document order."""
    import fitz
    blobs: list[bytes] = []
    for doc in documents:
        pdf_path = Path(doc.file.path)
        with fitz.open(str(pdf_path)) as pdf:
            n = pdf.page_count
        for page_no in range(1, n + 1):
            img = rasterize_pdf_page(pdf_path, page_no, dpi=dpi)
            buf = BytesIO()
            img.save(buf, format="PNG")
            blobs.append(buf.getvalue())
    return blobs


def _aggregate_confidence(gemini_confidence: float, evidence_blocks: list[Block]) -> float:
    """Combine Gemini's self-elicited confidence with the cited blocks' OCR confidence.

    Day-12 fix: Gemini is conservative on partial extractions and reports
    sub-threshold self-confidence (e.g. 0.8) even when the cited blocks
    are 1.0 from Docling. Pure `min()` was pulling clean facts below the
    ABSTAIN floor, producing demo-breaking false abstains. New rule: if
    every cited block is ≥ threshold, trust the blocks and only let
    Gemini's self-doubt LOWER the result if it's still ≥ threshold; if
    blocks themselves are noisy (any < threshold), keep the conservative
    floor so genuinely low-confidence OCR (Bidder C's distorted GST)
    still abstains.
    """
    if not evidence_blocks:
        return float(gemini_confidence)
    block_confs = [float(b.confidence) for b in evidence_blocks]
    min_block = min(block_confs)
    threshold = 0.85
    if min_block >= threshold:
        # Blocks are clean; never let Gemini's prompt-elicited self-doubt
        # pull the fact below the threshold. Take the higher of the two.
        return max(float(gemini_confidence), min_block)
    # Some block was genuinely noisy; keep the conservative floor.
    return min(float(gemini_confidence), min_block)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

@transaction.atomic
def extract_fact(criterion: Criterion, bidder_code: str) -> Fact:
    """Extract one Fact for (criterion, bidder_code). Idempotent via update_or_create."""
    bidder_code = bidder_code.upper().strip()
    if not bidder_code:
        raise ValueError("bidder_code must be non-empty (A / B / C).")

    bidder_docs = list(
        Document.objects.filter(bidder_code=bidder_code).order_by("type")
    )
    if not bidder_docs:
        raise RuntimeError(f"No bidder documents for bidder_code={bidder_code}. Ingest first.")

    # Manifest — every block from every bidder doc, in deterministic order.
    blocks_qs = (
        Block.objects.filter(document__bidder_code=bidder_code)
        .select_related("document")
        .order_by("document__type", "block_index")
    )
    manifest = _build_bidder_manifest(blocks_qs)

    # Images — only the documents the heuristic flags as relevant.
    likely_types = _likely_doc_types(criterion)
    relevant_docs = [d for d in bidder_docs if d.type in likely_types]
    if not relevant_docs:
        relevant_docs = bidder_docs  # fall back to "everything"
    image_blobs = _rasterize_documents(relevant_docs)
    image_mimes = ["image/png"] * len(image_blobs)

    prompt = FACT_PROMPT.format(
        code=criterion.code,
        title=criterion.title,
        type=criterion.type,
        mandatory="YES" if criterion.mandatory else "NO (optional / preferred)",
        requirement=criterion.requirement_text,
    )

    parsed, cache_hit = call_gemini_structured(
        prompt=prompt,
        manifest_obj={
            "criterion": {
                "code": criterion.code,
                "title": criterion.title,
                "type": criterion.type,
                "mandatory": criterion.mandatory,
                "requirement": criterion.requirement_text,
            },
            "bidder_code": bidder_code,
            "blocks": manifest,
            "relevant_doc_types": likely_types,
        },
        image_blobs=image_blobs,
        image_mime_types=image_mimes,
        schema_cls=FactExtraction,
        model=settings.GEMINI_MODEL_FLASH,
    )
    log.info(
        "Fact extraction (%s × %s): found=%s value=%r confidence=%.2f cache=%s",
        criterion.code, bidder_code, parsed.found,
        (parsed.value or "")[:60], parsed.confidence,
        "HIT" if cache_hit else "MISS",
    )

    # Resolve cited evidence Block rows by their globally-unique PK.
    cited_blocks = list(
        Block.objects.filter(
            document__bidder_code=bidder_code,
            id__in=(parsed.source_block_ids or []),
        )
    )
    aggregated = _aggregate_confidence(parsed.confidence, cited_blocks)

    fact, created = Fact.objects.update_or_create(
        criterion=criterion,
        bidder_code=bidder_code,
        defaults={
            "value": parsed.value or "",
            "ocr_confidence": aggregated,
            "raw_extraction_json": parsed.model_dump(),
        },
    )
    fact.evidence_blocks.set(cited_blocks)

    log.info(
        "    -> Fact persisted (id=%s, %s); aggregated_conf=%.2f over %d evidence blocks",
        fact.id, "created" if created else "updated", aggregated, len(cited_blocks),
    )
    return fact


def extract_facts_for_bidder(bidder_code: str) -> list[Fact]:
    """Extract every (criterion × bidder_code) fact for the only RFP in DB."""
    rfps = list(Document.objects.filter(type=DocumentType.RFP).order_by("-created_at"))
    if not rfps:
        raise RuntimeError("No RFP in DB.")
    rfp = rfps[0]
    criteria = list(Criterion.objects.filter(rfp=rfp).order_by("code"))
    if not criteria:
        raise RuntimeError("No Criteria extracted yet. Run extract_criteria first.")
    return [extract_fact(c, bidder_code) for c in criteria]
