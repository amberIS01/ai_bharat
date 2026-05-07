"""Gemini Vision OCR fallback.

Used for pages where Docling produces near-zero blocks (scans, phone photos,
Augraphy-distorted certificates) — e.g. Bidder C's GST.

The trick: Gemini doesn't expose a native confidence number, but a single
prompt asking for both `text + box_2d + confidence + reason_if_low` per region
yields a useful self-elicited confidence. Practitioners report this is a
reasonable proxy on noisy documents — and it's exactly what the demo's ABSTAIN
branch needs (Bidder C's distorted GST should self-report confidence < 0.85).

Bbox semantics: Gemini returns `[ymin, xmin, ymax, xmax]` normalized 0-1000.
We descale to pixel coordinates and store as TOPLEFT-origin (image native).

Caching: every call is keyed on `sha256(prompt + image_bytes) | model`. The
cache is a Django table (`GeminiCallCache`) so dev iteration is free.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import time
from pathlib import Path
from typing import Optional

from django.conf import settings
from django.db import transaction
from PIL import Image
from pydantic import BaseModel, Field

from core.models import (
    Block,
    BlockSource,
    CoordOrigin,
    Document,
    GeminiCallCache,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Response schema — Gemini fills this in via `response_schema=`
# ---------------------------------------------------------------------------

class OCRRegion(BaseModel):
    text: str = Field(description="The text content of this region as you read it.")
    box_2d: list[int] = Field(
        description="Normalized bounding box [ymin, xmin, ymax, xmax] on a 0-1000 scale.",
        min_length=4, max_length=4,
    )
    confidence: float = Field(
        description="Your confidence (0.0-1.0) that you read this region correctly.",
        ge=0.0, le=1.0,
    )
    reason_if_low: str = Field(
        default="",
        description="One-line explanation if confidence < 0.85 (e.g. 'photocopy noise', 'ink bleed', 'character ambiguous'). Empty string otherwise.",
    )


class OCRResult(BaseModel):
    regions: list[OCRRegion] = Field(
        description="Every distinct text region you can see in the image, in roughly natural reading order.",
    )
    overall_legibility: float = Field(
        description="Page-level legibility score (0.0-1.0). Set to the worst region's confidence.",
        ge=0.0, le=1.0,
    )


OCR_PROMPT = """\
You are a careful document OCR engine. Read every distinct text region in the \
attached image. For EACH region, return:

  * text          : the literal text content as you read it (preserve case, \
spacing, and punctuation as best you can)
  * box_2d        : the bounding box [ymin, xmin, ymax, xmax] normalized to \
a 0-1000 scale relative to image dimensions
  * confidence    : your confidence (0.0-1.0) that you read this region \
correctly. Use < 0.85 for any region where photocopy noise, ink bleed, \
character ambiguity, or other distortion makes you uncertain.
  * reason_if_low : a one-line reason whenever confidence < 0.85; empty \
string otherwise.

Set `overall_legibility` to the WORST per-region confidence on the page \
(this is the demo's ABSTAIN trigger when documents are scans / phone photos \
of stamped paperwork).

If a region is entirely illegible, still emit a row for it with confidence \
< 0.5 and a reason explaining why. Do NOT silently drop unreadable regions.
"""


# ---------------------------------------------------------------------------
# Gemini client (lazy singleton, vision key with fallback to general key)
# ---------------------------------------------------------------------------

_CLIENT = None


def _get_client():
    global _CLIENT
    if _CLIENT is None:
        from google import genai
        api_key = settings.GEMINI_VISION_API_KEY or settings.GEMINI_API_KEY
        if not api_key:
            raise RuntimeError(
                "Neither GEMINI_VISION_API_KEY nor GEMINI_API_KEY is set in .env"
            )
        _CLIENT = genai.Client(api_key=api_key)
    return _CLIENT


# ---------------------------------------------------------------------------
# Page rasterization — pdf2image first, PyMuPDF fallback (no poppler needed)
# ---------------------------------------------------------------------------

def rasterize_pdf_page(pdf_path: Path, page_no: int, dpi: int = 200) -> Image.Image:
    """Render a single PDF page (1-indexed) as a PIL.Image (RGB)."""
    try:
        from pdf2image import convert_from_path
        images = convert_from_path(
            str(pdf_path), dpi=dpi, first_page=page_no, last_page=page_no
        )
        if images:
            return images[0].convert("RGB")
    except Exception as e:  # noqa: BLE001
        log.warning("pdf2image failed (%s), falling back to PyMuPDF", e)

    import fitz  # PyMuPDF
    pdf = fitz.open(str(pdf_path))
    try:
        page = pdf.load_page(page_no - 1)
        pix = page.get_pixmap(dpi=dpi)
        mode = "RGB" if pix.alpha == 0 else "RGBA"
        img = Image.frombytes(mode, (pix.width, pix.height), pix.samples)
        return img.convert("RGB")
    finally:
        pdf.close()


def _image_bytes(img: Image.Image) -> bytes:
    """Stable PNG-encoded bytes of an image — used both for hashing and for the API call."""
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Cached OCR call
# ---------------------------------------------------------------------------

def _ocr_call_cached(
    image: Image.Image,
    prompt: str = OCR_PROMPT,
    model: str | None = None,
) -> tuple[OCRResult, bool]:
    """Return (parsed result, cache_hit_bool).

    On cache miss, calls the Gemini API and persists the response.
    """
    model = model or settings.GEMINI_MODEL_FLASH
    img_bytes = _image_bytes(image)
    image_sha = _hash(img_bytes)
    prompt_sha = _hash(prompt.encode("utf-8"))

    # Cache lookup
    cache_qs = GeminiCallCache.objects.filter(
        prompt_sha256=prompt_sha, image_sha256=image_sha, model=model
    )
    if cache_qs.exists():
        cached = cache_qs.first()
        cached.hits = (cached.hits or 0) + 1
        cached.save(update_fields=["hits"])
        try:
            parsed = OCRResult(**cached.response_json)
            return parsed, True
        except Exception as e:  # noqa: BLE001
            log.warning("Cached response failed validation, re-fetching: %s", e)
            cache_qs.delete()

    # Live call
    from google.genai import types
    client = _get_client()
    t0 = time.monotonic()
    response = client.models.generate_content(
        model=model,
        contents=[image, prompt],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=OCRResult,
        ),
    )
    elapsed = time.monotonic() - t0

    parsed: OCRResult | None = getattr(response, "parsed", None)
    if parsed is None:
        # Fall back to manual JSON parse if the SDK didn't auto-parse.
        text = getattr(response, "text", None) or ""
        parsed = OCRResult(**json.loads(text))

    GeminiCallCache.objects.create(
        prompt_sha256=prompt_sha,
        image_sha256=image_sha,
        model=model,
        response_json=parsed.model_dump(),
    )
    log.info(
        "Gemini Vision OCR call: model=%s regions=%d legibility=%.2f elapsed=%.2fs",
        model, len(parsed.regions), parsed.overall_legibility, elapsed,
    )
    return parsed, False


# ---------------------------------------------------------------------------
# Public entry point: OCR a PDF and persist Blocks
# ---------------------------------------------------------------------------

@transaction.atomic
def ocr_pdf_via_gemini(
    document: Document,
    *,
    file_path: str | Path | None = None,
    page_indices: Optional[list[int]] = None,
    dpi: int = 200,
    model: str | None = None,
    starting_block_index: int = 0,
) -> list[Block]:
    """Rasterize each requested page and OCR via Gemini Vision Flash.

    Persists one Block per detected region with `source=GEMINI_VISION` and the
    self-elicited `confidence` + optional `reason_if_low`.

    `page_indices` is 1-indexed; when `None` we OCR every page in the PDF.
    """
    src = Path(file_path) if file_path else Path(document.file.path)
    if not src.exists():
        raise FileNotFoundError(f"Document file missing: {src}")

    # Determine page count via PyMuPDF (fast and dependency-free of poppler).
    import fitz
    with fitz.open(str(src)) as pdf:
        total_pages = pdf.page_count
    pages = page_indices or list(range(1, total_pages + 1))

    blocks: list[Block] = []
    block_index = starting_block_index

    for page_no in pages:
        img = rasterize_pdf_page(src, page_no, dpi=dpi)
        ocr, cache_hit = _ocr_call_cached(img, model=model)
        log.info(
            "OCR page %d/%d: %d regions, legibility=%.2f, cache=%s",
            page_no, total_pages, len(ocr.regions), ocr.overall_legibility,
            "HIT" if cache_hit else "MISS",
        )

        width, height = img.size
        for region in ocr.regions:
            ymin, xmin, ymax, xmax = region.box_2d
            # Descale 0-1000 normalized → pixel coords (TOPLEFT origin).
            px_l = (xmin / 1000.0) * width
            px_t = (ymin / 1000.0) * height
            px_r = (xmax / 1000.0) * width
            px_b = (ymax / 1000.0) * height

            blocks.append(Block(
                document=document,
                block_index=block_index,
                page_no=page_no,
                bbox_l=px_l,
                bbox_t=px_t,
                bbox_r=px_r,
                bbox_b=px_b,
                coord_origin=CoordOrigin.TOPLEFT.value,
                text=region.text,
                confidence=region.confidence,
                source=BlockSource.GEMINI_VISION.value,
                reason_if_low=region.reason_if_low or "",
            ))
            block_index += 1

    Block.objects.bulk_create(blocks)

    if document.page_count is None or document.page_count != total_pages:
        document.page_count = total_pages
        document.save(update_fields=["page_count"])

    return blocks
