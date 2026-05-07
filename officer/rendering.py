"""Server-side helpers for the Day-9 drill-down screen.

Two responsibilities:

  1. Rasterise a single PDF page to PNG bytes (via PyMuPDF — already installed
     for the Day-2 OCR rasteriser). This is what the officer's drill-down view
     embeds as <img>; we don't ship PDF.js.

  2. Translate a `core.models.Block` bbox into TOPLEFT image-pixel coordinates
     that match the rasterised PNG, regardless of whether the source was
     Docling (BOTTOMLEFT, PDF user-space points) or Gemini Vision (already
     TOPLEFT, in pixel coords of a 200-DPI raster).

Everything here is deterministic and side-effect-free, so the unit tests in
`officer/tests.py` can drive it without spawning a real PDF or browser.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from pathlib import Path

from core.models import Block, BlockSource, CoordOrigin, Document

log = logging.getLogger(__name__)


# Rasterisation DPI used at OCR time. Day-2's `gemini_vision_ocr.rasterize_pdf_page`
# defaults to 200 DPI. Keep this in sync — the OCR bboxes are pixel-accurate
# against this DPI, and we render the drill-down image at the same setting so
# Gemini Vision overlays line up without re-scaling.
PAGE_RENDER_DPI = 200


# ---------------------------------------------------------------------------
# PNG rasterisation
# ---------------------------------------------------------------------------

def render_page_png(pdf_path: Path | str, page_no: int, *, dpi: int = PAGE_RENDER_DPI) -> tuple[bytes, int, int]:
    """Render the given (1-indexed) PDF page to PNG bytes.

    Returns `(png_bytes, width_px, height_px)` so the caller can both serve
    the image and tell the front-end the natural dimensions for its overlay
    container. PyMuPDF handles the rendering; no Cairo / poppler needed.
    """
    import fitz  # PyMuPDF

    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    if page_no < 1:
        raise ValueError(f"page_no must be 1-indexed, got {page_no}")

    with fitz.open(str(pdf_path)) as pdf:
        if page_no > pdf.page_count:
            raise IndexError(
                f"PDF has {pdf.page_count} page(s); cannot render page {page_no}"
            )
        page = pdf.load_page(page_no - 1)
        pix = page.get_pixmap(dpi=dpi)
        png_bytes = pix.tobytes("png")
        return png_bytes, int(pix.width), int(pix.height)


def page_dimensions_pts(pdf_path: Path | str, page_no: int) -> tuple[float, float]:
    """Return the page (width_pts, height_pts) in PDF user space (1/72 inch)."""
    import fitz

    with fitz.open(str(pdf_path)) as pdf:
        page = pdf.load_page(page_no - 1)
        return float(page.rect.width), float(page.rect.height)


# ---------------------------------------------------------------------------
# Bbox conversion — Block → image-pixel TOPLEFT
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PixelBox:
    """A bbox in TOPLEFT image-pixel coordinates."""
    left: float
    top: float
    right: float
    bottom: float

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top


def block_to_pixel_box(
    block: Block,
    *,
    image_width_px: int,
    image_height_px: int,
    page_width_pts: float | None = None,
    page_height_pts: float | None = None,
) -> PixelBox:
    """Convert `block.bbox_*` into TOPLEFT image-pixel coordinates.

    Cases:
      * `coord_origin == BOTTOMLEFT` (Docling, PDF user-space points) — flip
        the Y axis using `page_height_pts`, then scale x and y by the
        image-pixel-per-PDF-point ratio.
      * `coord_origin == TOPLEFT` (Gemini Vision, raster pixels at OCR DPI) —
        if the OCR raster matched the current image dimensions, the bbox is
        already in the right space; otherwise rescale proportionally.
    """
    l, t, r, b = block.bbox_l, block.bbox_t, block.bbox_r, block.bbox_b

    if block.coord_origin == CoordOrigin.BOTTOMLEFT.value:
        if not (page_width_pts and page_height_pts):
            raise ValueError(
                "page_width_pts + page_height_pts are required to convert a "
                "BOTTOMLEFT (PDF user-space) bbox to image pixels."
            )
        sx = image_width_px / page_width_pts
        sy = image_height_px / page_height_pts
        # In BOTTOMLEFT: t (top of rect) is the larger Y, b (bottom) the smaller.
        # In TOPLEFT image space: top of rect is at (page_height_pts - t) * sy.
        return PixelBox(
            left=l * sx,
            top=(page_height_pts - t) * sy,
            right=r * sx,
            bottom=(page_height_pts - b) * sy,
        )

    # TOPLEFT path. We assume the OCR raster was rendered at the same DPI
    # we're now serving the page at; if not, the caller can pass scale via
    # image_width_px / image_height_px against the originals.
    return PixelBox(left=l, top=t, right=r, bottom=b)


# ---------------------------------------------------------------------------
# High-level helper: build the drill-down JSON payload
# ---------------------------------------------------------------------------

def build_evidence_payload(verdict, *, build_url) -> dict:
    """Assemble the JSON the drill-down page consumes.

    `build_url(name, **kwargs)` is `django.urls.reverse` injected from the
    view so this module stays independent of Django's request layer.

    Output shape::

        {
          "verdict": {...},
          "fact": {...},
          "citations": [
            {
              "block_id", "document_id", "page_no",
              "page_image_url", "image_width_px", "image_height_px",
              "bbox_pixels": {"left", "top", "right", "bottom"},
              "text", "confidence", "source"
            }, ...
          ],
        }
    """
    from core.models import Fact

    blocks = list(verdict.evidence_refs.select_related("document").all())
    citations = []
    citation_warnings: list[str] = []
    # Cache page dimensions per (document, page_no) so we don't re-open the
    # PDF for every cited block.
    pdf_dims_cache: dict[tuple[int, int], tuple[float, float, int, int]] = {}

    for b in blocks:
        doc: Document = b.document
        key = (doc.id, b.page_no)
        if key not in pdf_dims_cache:
            try:
                page_w_pts, page_h_pts = page_dimensions_pts(doc.file.path, b.page_no)
                # Fast width/height computation without re-rasterising: take
                # PyMuPDF's pixmap dimensions at PAGE_RENDER_DPI without the
                # full PNG encode.
                import fitz
                with fitz.open(str(doc.file.path)) as pdf:
                    page = pdf.load_page(b.page_no - 1)
                    pix = page.get_pixmap(dpi=PAGE_RENDER_DPI)
                    image_w_px, image_h_px = int(pix.width), int(pix.height)
                pdf_dims_cache[key] = (page_w_pts, page_h_pts, image_w_px, image_h_px)
            except Exception as e:  # noqa: BLE001
                log.warning(
                    "Could not compute page dims for doc=%d page=%d: %s",
                    doc.id, b.page_no, e,
                )
                citation_warnings.append(
                    f"Skipped block {b.id} (doc #{doc.id}, page {b.page_no}): "
                    f"page dimensions unavailable ({type(e).__name__})."
                )
                continue
        page_w_pts, page_h_pts, image_w_px, image_h_px = pdf_dims_cache[key]

        try:
            box = block_to_pixel_box(
                b,
                image_width_px=image_w_px,
                image_height_px=image_h_px,
                page_width_pts=page_w_pts,
                page_height_pts=page_h_pts,
            )
        except ValueError as e:
            citation_warnings.append(
                f"Skipped block {b.id} (doc #{doc.id}, page {b.page_no}): "
                f"bbox conversion failed ({e})."
            )
            continue

        citations.append({
            "block_id": b.id,
            "document_id": doc.id,
            "document_type": doc.type,
            "page_no": b.page_no,
            "page_image_url": build_url(
                "officer:document_page_png", doc.id, b.page_no
            ),
            "image_width_px": image_w_px,
            "image_height_px": image_h_px,
            "bbox_pixels": {
                "left": round(box.left, 1),
                "top": round(box.top, 1),
                "right": round(box.right, 1),
                "bottom": round(box.bottom, 1),
                "width": round(box.width, 1),
                "height": round(box.height, 1),
            },
            "text": (b.text or "")[:240],
            "confidence": float(b.confidence),
            "source": b.source,
            "is_low_confidence": b.is_low_confidence,
            "reason_if_low": (b.reason_if_low or "").strip(),
        })

    fact = Fact.objects.filter(
        criterion=verdict.criterion, bidder_code=verdict.bidder_code
    ).first()

    return {
        "verdict": {
            "id": verdict.id,
            "status": verdict.status,
            "rule_id": verdict.rule_id,
            "reason": verdict.reason,
            "bindings": verdict.bindings_json or {},
            "criterion": {
                "id": verdict.criterion.id,
                "code": verdict.criterion.code,
                "title": verdict.criterion.title,
                "type": verdict.criterion.type,
                "mandatory": verdict.criterion.mandatory,
                "requirement_text": verdict.criterion.requirement_text,
            },
            "bidder_code": verdict.bidder_code,
        },
        "fact": (
            None if fact is None else {
                "value": fact.value,
                "ocr_confidence": float(fact.ocr_confidence),
            }
        ),
        "citations": citations,
        "citation_warnings": citation_warnings,
    }


__all__ = [
    "PAGE_RENDER_DPI",
    "PixelBox",
    "render_page_png",
    "page_dimensions_pts",
    "block_to_pixel_box",
    "build_evidence_payload",
]
