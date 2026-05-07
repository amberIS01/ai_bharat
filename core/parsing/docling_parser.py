"""Docling-backed PDF parser.

Reads a digital PDF (or whatever Docling supports) and persists each detected
text item as a `core.models.Block` row with its bbox + provenance.

The OCR fallback (`gemini_vision_ocr.py`) handles the cases where Docling
returns near-zero blocks because the input is a scan / phone photo / Augraphy-
distorted image — e.g. Bidder C's GST certificate.

Usage:
    from core.models import Document, DocumentType
    from core.parsing.docling_parser import parse_document

    doc = Document.objects.create(...)
    blocks = parse_document(doc)
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from django.db import transaction

from core.models import Block, BlockSource, CoordOrigin, Document

log = logging.getLogger(__name__)


# Lazy / cached singleton — DocumentConverter loads layout + table models on
# first use; we only want to pay that cost once per process.
_CONVERTER = None


def _get_converter():
    global _CONVERTER
    if _CONVERTER is None:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        # OCR off — digital PDFs from WeasyPrint already carry text. The Gemini
        # Vision fallback will handle scanned/distorted pages separately.
        # Tables on — the synthetic audit reports have FY turnover tables.
        opts = PdfPipelineOptions()
        opts.do_ocr = False
        opts.do_table_structure = True

        _CONVERTER = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=opts),
            }
        )
    return _CONVERTER


def _coord_origin_to_db(origin) -> str:
    """Translate Docling's CoordOrigin enum into our TextChoices value."""
    name = getattr(origin, "name", None) or str(origin).split(".")[-1]
    return CoordOrigin.BOTTOMLEFT.value if "BOTTOM" in name.upper() else CoordOrigin.TOPLEFT.value


def _serialize_table(table_item, doc) -> str:
    """Best-effort flat serialization of a Docling TableItem for full-text indexing."""
    try:
        return table_item.export_to_markdown(doc) or ""
    except Exception:  # noqa: BLE001
        # Fallback to whatever .text-like attribute exists
        return getattr(table_item, "text", "") or ""


@transaction.atomic
def parse_document(document: Document, *, file_path: str | Path | None = None) -> list[Block]:
    """Parse `document.file` (or `file_path` if given) via Docling and create Block rows.

    Idempotent at the (document, block_index) unique-key level: re-running on
    the same Document will fail the unique constraint instead of duplicating —
    callers that want to re-parse should delete existing blocks first.

    Returns the list of newly-created Block rows in insertion order.
    """
    src = Path(file_path) if file_path else Path(document.file.path)
    if not src.exists():
        raise FileNotFoundError(f"Document file missing: {src}")

    converter = _get_converter()

    t0 = time.monotonic()
    result = converter.convert(str(src))
    elapsed = time.monotonic() - t0
    doc = result.document

    blocks: list[Block] = []
    block_index = 0

    # --- Text items -------------------------------------------------------
    for text_item in doc.texts:
        if not text_item.prov:
            continue
        prov = text_item.prov[0]
        bbox = prov.bbox
        blocks.append(Block(
            document=document,
            block_index=block_index,
            page_no=int(prov.page_no),
            bbox_l=float(bbox.l),
            bbox_t=float(bbox.t),
            bbox_r=float(bbox.r),
            bbox_b=float(bbox.b),
            coord_origin=_coord_origin_to_db(bbox.coord_origin),
            text=text_item.text or "",
            confidence=1.0,
            source=BlockSource.DOCLING.value,
        ))
        block_index += 1

    # --- Tables (each table → one Block with markdown-serialized text) ----
    for table_item in doc.tables:
        if not table_item.prov:
            continue
        prov = table_item.prov[0]
        bbox = prov.bbox
        blocks.append(Block(
            document=document,
            block_index=block_index,
            page_no=int(prov.page_no),
            bbox_l=float(bbox.l),
            bbox_t=float(bbox.t),
            bbox_r=float(bbox.r),
            bbox_b=float(bbox.b),
            coord_origin=_coord_origin_to_db(bbox.coord_origin),
            text=_serialize_table(table_item, doc),
            confidence=1.0,
            source=BlockSource.DOCLING.value,
        ))
        block_index += 1

    # --- Pictures (no text, but we still index for future stamp detection) -
    for pic_item in doc.pictures:
        if not pic_item.prov:
            continue
        prov = pic_item.prov[0]
        bbox = prov.bbox
        caption = ""
        try:
            caption = pic_item.caption_text(doc) or ""
        except Exception:  # noqa: BLE001
            pass
        blocks.append(Block(
            document=document,
            block_index=block_index,
            page_no=int(prov.page_no),
            bbox_l=float(bbox.l),
            bbox_t=float(bbox.t),
            bbox_r=float(bbox.r),
            bbox_b=float(bbox.b),
            coord_origin=_coord_origin_to_db(bbox.coord_origin),
            text=f"[picture] {caption}".strip(),
            confidence=1.0,
            source=BlockSource.DOCLING.value,
        ))
        block_index += 1

    # Bulk-create the Block rows. bulk_create() bypasses post_save signals,
    # which is documented Django behaviour — so the audit-log layer never sees
    # per-Block events. We compensate below by emitting a single
    # `blocks.ingested` audit entry per parse_document() call.
    created = Block.objects.bulk_create(blocks)

    # Update page count on the Document if we now know it.
    if doc.pages and document.page_count != len(doc.pages):
        document.page_count = len(doc.pages)
        document.save(update_fields=["page_count"])

    # Audit-log summary for the bulk_create path (Day-5 Step 5).
    # Avoid an import cycle: defer the import until call time.
    from core.audit import merkle_log

    if created:
        block_ids = sorted(b.id for b in created if b.id is not None)
        by_source: dict[str, int] = {}
        for b in created:
            by_source[b.source] = by_source.get(b.source, 0) + 1
        merkle_log.append(
            "blocks.ingested",
            "document",
            document.id,
            {
                "document_id": document.id,
                "document_type": document.type,
                "block_count": len(created),
                "min_block_id": block_ids[0] if block_ids else None,
                "max_block_id": block_ids[-1] if block_ids else None,
                "by_source": by_source,
                "page_count": document.page_count,
            },
        )

    log.info(
        "Docling parsed %s: texts=%d tables=%d pictures=%d in %.2fs",
        src.name, len(doc.texts), len(doc.tables), len(doc.pictures), elapsed,
    )
    return created
