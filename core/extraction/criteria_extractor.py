"""Extract eligibility criteria from a parsed RFP via Gemini 2.5 Pro.

Inputs:
    * a `Document` of type RFP that already has Block rows (from Day-2 Docling parse).

Outputs:
    * one Criterion row per extracted criterion, with `source_clause_block` FK
      pointing at the first cited Block.

Every Gemini call goes through `core.extraction.gemini_client.call_gemini_structured()`
so it is cached on `(prompt, manifest, model)`. Re-running this command is free.
"""

from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path
from typing import Iterable

from django.conf import settings
from django.db import transaction

from core.extraction.gemini_client import call_gemini_structured
from core.extraction.schemas import CriteriaResult, CriterionExtraction
from core.models import Block, Criterion, Document, DocumentType
from core.parsing.gemini_vision_ocr import rasterize_pdf_page

log = logging.getLogger(__name__)


# Truncate per-block text to keep the manifest token budget reasonable.
MANIFEST_TEXT_MAX = 280


CRITERIA_PROMPT = """\
You are a careful tender-document analyst. The attached page images show an \
Indian Government tender (CRPF / similar central-government format). The text \
of the document has also been pre-parsed into BLOCKS — each with a globally \
unique `block_id`, a page number, and the literal text. The block manifest \
is provided below as JSON.

Your task: extract every distinct ELIGIBILITY CRITERION the bidder must \
satisfy to qualify for technical evaluation. For EACH criterion, return:

  * code              — short stable identifier within this RFP, e.g. "C-1", "C-2".
  * title             — plain-English label (e.g. "Minimum Annual Turnover").
  * requirement_text  — the exact requirement as stated, kept faithful.
  * type              — one of "financial", "compliance", "technical".
  * mandatory         — true if the criterion is MANDATORY, false if PREFERRED / OPTIONAL.
  * source_block_ids  — list of `block_id` integers (NOT page numbers, NOT \
indexes — the `block_id` field of the manifest entry) whose text contains \
the criterion. Cite at least one. Prefer the most specific block (the \
clause itself) over surrounding headings.

Eligibility criteria typically appear in a section titled "ELIGIBILITY \
CRITERIA", "Pre-Qualification Criteria", "Technical Eligibility", or under \
clauses about "Registered Contractors", "Turnover", "GST", "DSC", "ISO", \
"Past Experience", "Similar Works". Each row in such sections is one criterion.

DO NOT include:
  * scope of work, BOQ, drawings, technical specifications
  * generic terms & conditions, EMD payment instructions, bid-validity clauses
  * canvassing rules, cancellation rights, force-majeure clauses

Return the criteria in document order. If you find none, return an empty list.

Be conservative on `mandatory`: if the RFP language uses "shall", "must", \
"required to", treat as mandatory. If it uses "preferred", "desirable", \
treat as not mandatory.
"""


# ---------------------------------------------------------------------------
# Manifest building
# ---------------------------------------------------------------------------

def build_block_manifest(blocks: Iterable[Block], *, max_text: int = MANIFEST_TEXT_MAX) -> list[dict]:
    """Serialize Block rows into the Gemini-facing manifest shape.

    `block_id` is the Django PK — globally unique across all documents.
    `block_index` is kept as a hint but the model is told to cite block_id.
    """
    manifest = []
    for b in blocks:
        text = (b.text or "").strip()
        if len(text) > max_text:
            text = text[:max_text].rstrip() + "..."
        manifest.append({
            "block_id": b.id,
            "block_index": b.block_index,
            "page": b.page_no,
            "text": text,
        })
    return manifest


def rasterize_all_pages(pdf_path: Path, dpi: int = 150) -> list[bytes]:
    """Return PNG-encoded bytes of every page in the PDF."""
    import fitz
    with fitz.open(str(pdf_path)) as pdf:
        n = pdf.page_count
    out: list[bytes] = []
    for page_no in range(1, n + 1):
        img = rasterize_pdf_page(pdf_path, page_no, dpi=dpi)
        buf = BytesIO()
        img.save(buf, format="PNG")
        out.append(buf.getvalue())
    return out


# ---------------------------------------------------------------------------
# Block-id lookup with fuzzy fallback
# ---------------------------------------------------------------------------

def resolve_source_block(rfp: Document, source_block_ids: list[int]) -> Block | None:
    """Pick the canonical source-clause Block for a criterion.

    Strategy: take the first cited `block_id` (Django PK) that exists for this RFP.
    If none of the cited IDs are valid, return None and let the Criterion row keep
    `source_clause_block=NULL`.
    """
    for bid in source_block_ids:
        b = Block.objects.filter(document=rfp, id=bid).first()
        if b is not None:
            return b
    return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

@transaction.atomic
def extract_criteria(rfp: Document, *, replace: bool = False) -> list[Criterion]:
    """Run the criteria extraction pass on `rfp` and persist Criterion rows.

    Args:
        rfp: a Document with type=RFP that already has Block rows.
        replace: if True, delete existing Criterion rows for this RFP first.

    Returns:
        The list of newly created Criterion rows (in document / code order).
    """
    if rfp.type != DocumentType.RFP:
        raise ValueError(f"extract_criteria requires a Document with type=RFP, got {rfp.type}")

    blocks = list(Block.objects.filter(document=rfp).order_by("block_index"))
    if not blocks:
        raise RuntimeError(
            f"RFP doc_id={rfp.id} has no parsed Blocks. Run `python manage.py ingest_bundle` first."
        )

    if replace:
        n_deleted, _ = Criterion.objects.filter(rfp=rfp).delete()
        if n_deleted:
            log.info("extract_criteria: deleted %d existing Criterion rows for RFP %s", n_deleted, rfp.id)

    manifest = build_block_manifest(blocks)
    image_blobs = rasterize_all_pages(Path(rfp.file.path))
    image_mimes = ["image/png"] * len(image_blobs)

    parsed, cache_hit = call_gemini_structured(
        prompt=CRITERIA_PROMPT,
        manifest_obj=manifest,
        image_blobs=image_blobs,
        image_mime_types=image_mimes,
        schema_cls=CriteriaResult,
        model=settings.GEMINI_MODEL_PRO,
    )
    log.info(
        "Criteria extraction: %d criteria found (cache=%s)",
        len(parsed.criteria), "HIT" if cache_hit else "MISS",
    )

    rows: list[Criterion] = []
    for ext in parsed.criteria:
        src_block = resolve_source_block(rfp, ext.source_block_ids or [])
        c, created = Criterion.objects.update_or_create(
            rfp=rfp,
            code=ext.code,
            defaults={
                "title": ext.title,
                "requirement_text": ext.requirement_text,
                "type": ext.type,
                "mandatory": ext.mandatory,
                "source_clause_block": src_block,
            },
        )
        if created:
            log.info("  + new Criterion %s: %s", c.code, c.title)
        else:
            log.info("  ~ updated Criterion %s: %s", c.code, c.title)
        rows.append(c)
    return rows


# ---------------------------------------------------------------------------
# Convenience helper for one-line invocation in scripts / shell
# ---------------------------------------------------------------------------

def extract_for_only_rfp() -> list[Criterion]:
    rfps = list(Document.objects.filter(type=DocumentType.RFP).order_by("-created_at"))
    if not rfps:
        raise RuntimeError("No RFP Document in DB; ingest one first.")
    if len(rfps) > 1:
        log.warning("Multiple RFPs found; using the most recent (id=%s).", rfps[0].id)
    return extract_criteria(rfps[0])
