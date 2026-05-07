"""Ingest a bidder bundle (zip or directory of PDFs) into Django.

Strategy per document:
    1. Compute SHA-256, create / fetch Document row.
    2. Try Docling first (fast, deterministic, free).
    3. If Docling returns < 5 blocks per page average (heuristic for "this is
       almost certainly a scanned/raster PDF"), fall back to Gemini Vision.
    4. Print a per-document summary including confidence histogram.

Usage:
    python manage.py ingest_bundle synthetic/bidder_a_pass.zip
    python manage.py ingest_bundle synthetic/bidder_c          # directory works too
    python manage.py ingest_bundle synthetic/rfp_synthetic_construction_001.pdf  # single PDF
    python manage.py ingest_bundle synthetic/bidder_c --force  # re-ingest

The command is idempotent: if a Document with the same SHA-256 already exists
it skips parsing unless --force is passed. This makes the demo flow safe to
re-run without burning Gemini API credit.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import (
    Block,
    BlockSource,
    Document,
    DocumentType,
    Fact,
    Verdict,
)
from core.parsing.docling_parser import parse_document
from core.parsing.gemini_vision_ocr import ocr_pdf_via_gemini


# Heuristic: documents that average fewer than this many Docling text blocks
# per page are treated as "scanned / image" and routed to Gemini Vision.
DOCLING_MIN_BLOCKS_PER_PAGE = 5


# Filename → DocumentType inference. Order matters (more specific first).
_FILENAME_TO_TYPE = [
    (re.compile(r"^rfp_", re.I),                DocumentType.RFP),
    (re.compile(r"cover", re.I),                DocumentType.BIDDER_COVER),
    (re.compile(r"audit", re.I),                DocumentType.BIDDER_AUDIT),
    (re.compile(r"gst",   re.I),                DocumentType.BIDDER_GST),
    (re.compile(r"dsc",   re.I),                DocumentType.BIDDER_DSC),
    (re.compile(r"(experience|past|performance)", re.I),
                                                DocumentType.BIDDER_EXPERIENCE),
    (re.compile(r"iso",   re.I),                DocumentType.BIDDER_ISO),
]


def _infer_type(filename: str) -> str:
    for rx, t in _FILENAME_TO_TYPE:
        if rx.search(filename):
            return t.value
    return DocumentType.BIDDER_OTHER.value


def _infer_bidder_code(path: Path) -> str:
    """Infer bidder A/B/C from path: 'bidder_a/...' or 'bidder_a_pass.zip'."""
    parts_to_inspect = [p.lower() for p in path.parts] + [path.name.lower()]
    for chunk in parts_to_inspect:
        m = re.search(r"bidder[_\-\s]?([abc])\b", chunk)
        if m:
            return m.group(1).upper()
    return ""


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _gather_pdfs(target: Path) -> tuple[list[Path], Path | None]:
    """Resolve `target` to a list of PDFs. Extracts zips into a temp dir.

    Returns `(pdf_paths, temp_dir_to_clean)`. Caller must `shutil.rmtree(temp_dir_to_clean)`.
    """
    if target.is_file() and target.suffix.lower() == ".pdf":
        return [target], None
    if target.is_file() and target.suffix.lower() == ".zip":
        tmp = Path(tempfile.mkdtemp(prefix="praman_ingest_"))
        with zipfile.ZipFile(target) as zf:
            zf.extractall(tmp)
        # Mirror the bidder directory structure if the zip lacked one.
        pdfs = sorted(tmp.rglob("*.pdf"))
        return pdfs, tmp
    if target.is_dir():
        return sorted(target.rglob("*.pdf")), None
    raise FileNotFoundError(f"Not a PDF, ZIP, or directory: {target}")


class Command(BaseCommand):
    help = "Ingest a bundle (zip / dir / single PDF) into Documents + Blocks."

    def add_arguments(self, parser):
        parser.add_argument("target", type=str, help="Path to a PDF, ZIP, or directory.")
        parser.add_argument(
            "--force", action="store_true",
            help="Re-ingest documents already present (matched by SHA-256). "
                 "Refused if downstream Facts/Verdicts cite the existing blocks; "
                 "use --force-cascade to opt in to wiping those too.",
        )
        parser.add_argument(
            "--force-cascade", action="store_true",
            help="With --force, allow CASCADE-deletion even when downstream "
                 "Facts/Verdicts cite the existing Document's blocks. The "
                 "operator must re-run extract_facts + evaluate_verdicts after.",
        )
        parser.add_argument(
            "--bidder-code", type=str, default=None,
            help="Override bidder-code inference (single letter A / B / C).",
        )
        parser.add_argument(
            "--no-vision-fallback", action="store_true",
            help="Skip the Gemini Vision OCR fallback (Docling-only ingest).",
        )

    def handle(self, *args, **opts):
        target = Path(opts["target"]).resolve()
        bidder_override = (opts.get("bidder_code") or "").upper().strip()
        force = opts["force"]
        force_cascade = opts.get("force_cascade", False)
        if force_cascade and not force:
            raise CommandError("--force-cascade requires --force.")

        pdfs, tmp = _gather_pdfs(target)
        try:
            self.stdout.write(self.style.MIGRATE_HEADING(
                f"Ingesting {len(pdfs)} PDF(s) from {target}"
            ))

            for pdf_path in pdfs:
                self.stdout.write("")
                self._ingest_one(
                    pdf_path,
                    target_root=target,
                    bidder_override=bidder_override,
                    force=force,
                    force_cascade=force_cascade,
                    use_vision=not opts["no_vision_fallback"],
                )
        finally:
            if tmp is not None:
                shutil.rmtree(tmp, ignore_errors=True)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=== Ingest complete ==="))
        self.stdout.write(f"Documents in DB: {Document.objects.count()}")
        self.stdout.write(f"Blocks in DB:    {Block.objects.count()} "
                          f"(DOCLING={Block.objects.filter(source=BlockSource.DOCLING).count()}, "
                          f"GEMINI_VISION={Block.objects.filter(source=BlockSource.GEMINI_VISION).count()})")

    # ---------------------------------------------------------------------

    def _ingest_one(
        self,
        pdf_path: Path,
        *,
        target_root: Path,
        bidder_override: str,
        force: bool,
        force_cascade: bool,
        use_vision: bool,
    ) -> None:
        sha = _sha256(pdf_path)
        bidder_code = bidder_override or _infer_bidder_code(pdf_path) or _infer_bidder_code(target_root)
        doc_type = _infer_type(pdf_path.name)

        existing = Document.objects.filter(sha256=sha).first()
        if existing and not force:
            self.stdout.write(self.style.NOTICE(
                f"  - {pdf_path.name}: already ingested as Document #{existing.id} "
                f"({existing.blocks.count()} blocks). Use --force to re-ingest."
            ))
            return

        if existing and force:
            # CASCADE on Document → Block wipes the M2M rows in
            # Fact.evidence_blocks and Verdict.evidence_refs silently.
            # Refuse the delete unless the operator explicitly opts in.
            fact_count = (
                Fact.objects.filter(evidence_blocks__document=existing)
                .distinct().count()
            )
            verdict_count = (
                Verdict.objects.filter(evidence_refs__document=existing)
                .distinct().count()
            )
            if (fact_count or verdict_count) and not force_cascade:
                raise CommandError(
                    f"Re-ingest blocked for Document #{existing.id} "
                    f"({existing.original_filename}): "
                    f"{fact_count} Fact(s) and {verdict_count} Verdict(s) "
                    f"cite its blocks. Re-running --force would orphan those "
                    f"citations. Either pass --force-cascade to wipe them "
                    f"(then re-run extract_facts + evaluate_verdicts), or "
                    f"skip the re-ingest."
                )
            self.stdout.write(self.style.WARNING(
                f"  - {pdf_path.name}: --force{' --force-cascade' if force_cascade else ''} "
                f"-> deleting Document #{existing.id} "
                f"(cascading {fact_count} Fact / {verdict_count} Verdict citations)"
            ))
            existing.delete()

        # Create Document + persist file
        with transaction.atomic():
            doc = Document.objects.create(
                type=doc_type,
                bidder_code=bidder_code,
                original_filename=pdf_path.name,
                sha256=sha,
            )
            with pdf_path.open("rb") as f:
                doc.file.save(pdf_path.name, f, save=True)

        self.stdout.write(self.style.MIGRATE_LABEL(
            f"  - {pdf_path.name} (type={doc_type}, bidder={bidder_code or '-'}, sha={sha[:8]}...)"
        ))

        # 1) Docling pass
        try:
            docling_blocks = parse_document(doc)
        except Exception as e:  # noqa: BLE001
            self.stdout.write(self.style.ERROR(f"    Docling failed: {e}"))
            docling_blocks = []

        page_count = max(doc.page_count or 1, 1)
        per_page = len(docling_blocks) / page_count

        self.stdout.write(
            f"    Docling: {len(docling_blocks)} blocks across {page_count} page(s) "
            f"({per_page:.1f}/page)"
        )

        # 2) Vision fallback if Docling found very little
        if use_vision and per_page < DOCLING_MIN_BLOCKS_PER_PAGE:
            self.stdout.write(self.style.WARNING(
                f"    Docling under threshold ({per_page:.1f}/page < {DOCLING_MIN_BLOCKS_PER_PAGE}); "
                "falling back to Gemini Vision OCR..."
            ))
            try:
                # Continue indexing from where Docling left off
                next_index = (
                    Block.objects.filter(document=doc).order_by("-block_index")
                    .values_list("block_index", flat=True).first()
                )
                start = (next_index + 1) if next_index is not None else 0
                vision_blocks = ocr_pdf_via_gemini(doc, starting_block_index=start)
                low = sum(1 for b in vision_blocks if b.confidence < settings.OCR_CONFIDENCE_THRESHOLD)
                self.stdout.write(self.style.SUCCESS(
                    f"    Gemini Vision: {len(vision_blocks)} blocks, "
                    f"{low}/{len(vision_blocks)} below {settings.OCR_CONFIDENCE_THRESHOLD}"
                ))
            except Exception as e:  # noqa: BLE001
                self.stdout.write(self.style.ERROR(f"    Gemini Vision OCR failed: {e}"))
        else:
            if not use_vision:
                self.stdout.write("    Vision fallback skipped (--no-vision-fallback)")
            else:
                self.stdout.write(f"    Vision fallback NOT needed ({per_page:.1f}/page is sufficient)")
