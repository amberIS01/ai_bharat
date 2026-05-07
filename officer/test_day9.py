"""Day-9 tests for the AG-Grid JSON, evidence JSON, and document_page_png
endpoints — plus the bbox-coordinate-conversion helper.

Strategy:
  * Pure-Python tests for `block_to_pixel_box` (no Django DB; just dataclasses).
  * Endpoint tests via Django Client with fixture rows.
  * For document_page_png we need a real PDF on disk; we generate a tiny
    one-page PDF via PyMuPDF inline so we don't depend on the synthetic
    dataset existing.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from core.models import (
    Block,
    BlockSource,
    CoordOrigin,
    Criterion,
    CriterionType,
    Document,
    DocumentType,
    Fact,
    Verdict,
    VerdictStatus,
)
from officer.rendering import (
    PixelBox,
    block_to_pixel_box,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def auth_client(db):
    User = get_user_model()
    user = User.objects.create_superuser(
        username="day9_officer", email="d9@example.com", password="pwd",
    )
    c = Client()
    c.force_login(user)
    return c


def _fake_block(*, coord_origin, l, t, r, b):
    """Build a Block-like object without saving — used by the pure-Python tests."""
    class _B:
        pass
    obj = _B()
    obj.bbox_l, obj.bbox_t, obj.bbox_r, obj.bbox_b = l, t, r, b
    obj.coord_origin = coord_origin
    return obj


def _make_one_page_pdf(text: str = "hello") -> bytes:
    """Produce a minimal single-page PDF in-memory via PyMuPDF."""
    import fitz
    pdf = fitz.open()
    page = pdf.new_page(width=595, height=842)  # A4 in points
    page.insert_text((72, 72), text, fontsize=12)
    buf = io.BytesIO()
    pdf.save(buf)
    pdf.close()
    return buf.getvalue()


# ---------------------------------------------------------------------------
# block_to_pixel_box — pure math
# ---------------------------------------------------------------------------

def test_bottomleft_bbox_translates_to_topleft_pixels():
    # PDF page 595×842 points (A4), rasterised at 1190×1684 (200 DPI = 2x).
    block = _fake_block(coord_origin=CoordOrigin.BOTTOMLEFT.value,
                         l=100, t=800, r=200, b=750)
    box = block_to_pixel_box(
        block,
        image_width_px=1190, image_height_px=1684,
        page_width_pts=595, page_height_pts=842,
    )
    # x scales by 1190/595 = 2.0; y by 1684/842 = 2.0.
    assert box.left == pytest.approx(200.0)
    assert box.right == pytest.approx(400.0)
    # In BOTTOMLEFT, t=800 is near the top of the page → image y = (842-800)*2 = 84
    assert box.top == pytest.approx(84.0)
    # b=750 → image y = (842-750)*2 = 184
    assert box.bottom == pytest.approx(184.0)
    assert box.width == pytest.approx(200.0)
    assert box.height == pytest.approx(100.0)


def test_topleft_bbox_passes_through_unchanged():
    block = _fake_block(coord_origin=CoordOrigin.TOPLEFT.value,
                         l=10, t=20, r=110, b=70)
    box = block_to_pixel_box(
        block,
        image_width_px=1000, image_height_px=1000,
    )
    assert box == PixelBox(left=10, top=20, right=110, bottom=70)


def test_bottomleft_without_page_dims_raises():
    block = _fake_block(coord_origin=CoordOrigin.BOTTOMLEFT.value,
                         l=0, t=10, r=10, b=0)
    with pytest.raises(ValueError, match="page_width_pts"):
        block_to_pixel_box(block, image_width_px=100, image_height_px=100)


def test_pixelbox_width_height_properties():
    box = PixelBox(left=10, top=20, right=110, bottom=70)
    assert box.width == 100
    assert box.height == 50


# ---------------------------------------------------------------------------
# Endpoint: GET /officer/rfp/<id>/grid.json
# ---------------------------------------------------------------------------

def _seed_minimal_dataset(db):
    """Create one RFP, two bidders, two criteria, and a verdict matrix that
    exercises PASS / FAIL / ABSTAIN / MISSING in the JSON output."""
    rfp = Document.objects.create(
        type=DocumentType.RFP, sha256="rfp" * 22 + "aa",
        original_filename="rfp.pdf",
    )
    Document.objects.create(
        type=DocumentType.BIDDER_COVER, bidder_code="A",
        sha256="ba" * 32, original_filename="ba.pdf",
    )
    Document.objects.create(
        type=DocumentType.BIDDER_COVER, bidder_code="B",
        sha256="bb" * 32, original_filename="bb.pdf",
    )
    c1 = Criterion.objects.create(
        rfp=rfp, code="C-1", title="Turnover", requirement_text="r",
        type=CriterionType.FINANCIAL.value, mandatory=True,
    )
    c2 = Criterion.objects.create(
        rfp=rfp, code="C-2", title="GST", requirement_text="r",
        type=CriterionType.COMPLIANCE.value, mandatory=True,
    )
    Verdict.objects.create(
        criterion=c1, bidder_code="A",
        status=VerdictStatus.PASS.value, rule_id="c1.pass.x", reason="ok",
    )
    Verdict.objects.create(
        criterion=c2, bidder_code="A",
        status=VerdictStatus.PASS.value, rule_id="c2.pass.x", reason="ok",
    )
    Verdict.objects.create(
        criterion=c1, bidder_code="B",
        status=VerdictStatus.FAIL.value, rule_id="c1.fail.x", reason="low",
    )
    # Bidder B C-2 has NO verdict → MISSING in the matrix.
    return rfp, c1, c2


def test_grid_json_returns_full_matrix(auth_client, db):
    rfp, c1, c2 = _seed_minimal_dataset(db)
    resp = auth_client.get(reverse("officer:eval_grid_json", args=[rfp.id]))
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["rfp_id"] == rfp.id
    codes = [c["code"] for c in payload["criteria"]]
    assert codes == ["C-1", "C-2"]
    assert payload["bidder_codes"] == ["A", "B"]

    # Bidder A row
    a_row = next(r for r in payload["rows"] if r["bidder_code"] == "A")
    assert a_row["C-1"]["status"] == "PASS"
    assert a_row["C-2"]["status"] == "PASS"
    assert a_row["_summary"]["PASS"] == 2
    assert a_row["_summary"]["MISSING"] == 0

    # Bidder B row — C-2 is MISSING
    b_row = next(r for r in payload["rows"] if r["bidder_code"] == "B")
    assert b_row["C-1"]["status"] == "FAIL"
    assert b_row["C-2"] is None
    assert b_row["_summary"]["FAIL"] == 1
    assert b_row["_summary"]["MISSING"] == 1


def test_grid_json_404_for_non_rfp(auth_client, db):
    not_rfp = Document.objects.create(
        type=DocumentType.BIDDER_COVER, bidder_code="A",
        sha256="not" * 22, original_filename="x.pdf",
    )
    resp = auth_client.get(reverse("officer:eval_grid_json", args=[not_rfp.id]))
    assert resp.status_code == 404


def test_grid_json_requires_login(db):
    rfp, _, _ = _seed_minimal_dataset(db)
    resp = Client().get(reverse("officer:eval_grid_json", args=[rfp.id]))
    assert resp.status_code == 302


# ---------------------------------------------------------------------------
# Endpoint: GET /officer/document/<id>/page/<n>.png
# ---------------------------------------------------------------------------

def test_document_page_png_serves_real_image(auth_client, db, tmp_path):
    pdf_bytes = _make_one_page_pdf("Praman test page")
    doc = Document.objects.create(
        type=DocumentType.RFP, sha256="png" * 22 + "aa",
        original_filename="png.pdf",
    )
    doc.file.save("png.pdf", SimpleUploadedFile("png.pdf", pdf_bytes), save=True)

    resp = auth_client.get(reverse("officer:document_page_png", args=[doc.id, 1]))
    assert resp.status_code == 200
    assert resp["Content-Type"] == "image/png"
    # PNG magic bytes
    assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_document_page_png_404_on_bad_page(auth_client, db, tmp_path):
    doc = Document.objects.create(
        type=DocumentType.RFP, sha256="png2" * 16 + "aa",
        original_filename="png2.pdf",
    )
    doc.file.save("png2.pdf",
                  SimpleUploadedFile("png2.pdf", _make_one_page_pdf("p")), save=True)
    resp = auth_client.get(reverse("officer:document_page_png", args=[doc.id, 99]))
    assert resp.status_code == 404


def test_document_page_png_requires_login(db):
    doc = Document.objects.create(
        type=DocumentType.RFP, sha256="png3" * 16 + "aa",
        original_filename="png3.pdf",
    )
    resp = Client().get(reverse("officer:document_page_png", args=[doc.id, 1]))
    assert resp.status_code == 302


# ---------------------------------------------------------------------------
# Endpoint: GET /officer/verdict/<id>/evidence.json
# ---------------------------------------------------------------------------

def test_verdict_evidence_json_returns_well_formed_payload(auth_client, db):
    pdf_bytes = _make_one_page_pdf("evidence test")
    rfp = Document.objects.create(
        type=DocumentType.RFP, sha256="ev" * 32, original_filename="ev.pdf",
    )
    rfp.file.save("ev.pdf", SimpleUploadedFile("ev.pdf", pdf_bytes), save=True)

    block = Block.objects.create(
        document=rfp, block_index=0, page_no=1,
        bbox_l=100, bbox_t=800, bbox_r=200, bbox_b=750,
        coord_origin=CoordOrigin.BOTTOMLEFT.value,
        text="Requirement: turnover ≥ Rs. 5 Cr",
        confidence=1.0, source=BlockSource.DOCLING.value,
    )
    crit = Criterion.objects.create(
        rfp=rfp, code="C-1", title="Turnover", requirement_text="r",
        type=CriterionType.FINANCIAL.value, mandatory=True,
        source_clause_block=block,
    )
    fact = Fact.objects.create(
        criterion=crit, bidder_code="A",
        value="Rs. 6,80,00,000/-", ocr_confidence=1.0,
    )
    fact.evidence_blocks.add(block)
    verdict = Verdict.objects.create(
        criterion=crit, bidder_code="A",
        status=VerdictStatus.PASS.value, rule_id="c1.pass.x",
        bindings_json={"value_inr": 68000000}, reason="ok",
    )
    verdict.evidence_refs.add(block)

    resp = auth_client.get(
        reverse("officer:verdict_evidence_json", args=[verdict.id]),
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["verdict"]["status"] == "PASS"
    assert payload["verdict"]["criterion"]["code"] == "C-1"
    assert payload["fact"]["value"] == "Rs. 6,80,00,000/-"
    assert len(payload["citations"]) == 1
    cit = payload["citations"][0]
    assert cit["block_id"] == block.id
    assert cit["page_no"] == 1
    assert cit["source"] == "DOCLING"
    assert cit["text"].startswith("Requirement")
    # Bbox should be in image-pixel TOPLEFT space: positive width/height.
    bb = cit["bbox_pixels"]
    assert bb["right"] > bb["left"]
    assert bb["bottom"] > bb["top"]
    assert bb["width"] > 0 and bb["height"] > 0


def test_verdict_evidence_json_404_for_unknown(auth_client, db):
    resp = auth_client.get(
        reverse("officer:verdict_evidence_json", args=[999_999_999]),
    )
    assert resp.status_code == 404
