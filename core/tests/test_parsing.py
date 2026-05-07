"""Docling-parser regression tests on the synthetic dataset.

Marked `slow` because they spawn the Docling DocumentConverter — skip with
`pytest -m "not slow"` for a fast pre-commit run.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from django.conf import settings


@pytest.mark.slow
@pytest.mark.django_db
def test_docling_parses_rfp():
    """The synthetic RFP must produce ≥ 30 text blocks across its pages."""
    from core.models import Document, DocumentType
    from core.parsing.docling_parser import parse_document

    rfp_path = settings.SYNTHETIC_DIR / "rfp_synthetic_construction_001.pdf"
    if not rfp_path.exists():
        pytest.skip("Synthetic RFP not generated. Run: python manage.py generate_synthetic")

    sha = hashlib.sha256(rfp_path.read_bytes()).hexdigest()
    doc = Document.objects.create(
        type=DocumentType.RFP,
        original_filename=rfp_path.name,
        sha256=sha,
    )
    with rfp_path.open("rb") as f:
        doc.file.save(rfp_path.name, f, save=True)

    blocks = parse_document(doc)
    assert len(blocks) >= 30, f"Expected ≥ 30 blocks from RFP, got {len(blocks)}"
    assert doc.page_count and doc.page_count >= 4


@pytest.mark.slow
@pytest.mark.django_db
def test_docling_parses_bidder_a_cover_letter():
    """Bidder A cover letter is a clean digital PDF; should yield ≥ 10 blocks."""
    from core.models import Document, DocumentType
    from core.parsing.docling_parser import parse_document

    cover = settings.SYNTHETIC_DIR / "bidder_a" / "01_cover_letter.pdf"
    if not cover.exists():
        pytest.skip("Synthetic Bidder A cover letter missing.")

    sha = hashlib.sha256(cover.read_bytes()).hexdigest()
    doc = Document.objects.create(
        type=DocumentType.BIDDER_COVER,
        bidder_code="A",
        original_filename=cover.name,
        sha256=sha,
    )
    with cover.open("rb") as f:
        doc.file.save(cover.name, f, save=True)

    blocks = parse_document(doc)
    assert len(blocks) >= 10, f"Expected ≥ 10 blocks from cover letter, got {len(blocks)}"
    # Every block must have a non-empty bbox tuple
    for b in blocks:
        assert (b.bbox_r > b.bbox_l) and (b.bbox_t != b.bbox_b)
        assert b.confidence == 1.0  # digital PDFs always at full confidence
        assert b.source == "DOCLING"


@pytest.mark.slow
@pytest.mark.django_db
def test_docling_yields_almost_nothing_on_distorted_gst():
    """Bidder C's distorted GST is a rasterized + Augraphy-distorted PDF.
    Docling alone (OCR off) should return very few blocks → triggers vision fallback.
    """
    from core.models import Document, DocumentType
    from core.parsing.docling_parser import parse_document

    gst = settings.SYNTHETIC_DIR / "bidder_c" / "03_gst_certificate.pdf"
    if not gst.exists():
        pytest.skip("Synthetic Bidder C GST missing.")

    sha = hashlib.sha256(gst.read_bytes()).hexdigest()
    doc = Document.objects.create(
        type=DocumentType.BIDDER_GST,
        bidder_code="C",
        original_filename=gst.name,
        sha256=sha,
    )
    with gst.open("rb") as f:
        doc.file.save(gst.name, f, save=True)

    blocks = parse_document(doc)
    # Heuristic in ingest_bundle is < 5 blocks/page → fall back to Vision.
    assert len(blocks) < 5, (
        f"Docling extracted too many blocks ({len(blocks)}) from the distorted GST; "
        "vision fallback won't trigger. Check Augraphy distortion intensity."
    )
