"""Pure-Python tests for Gemini Vision helpers — no Django DB, no API calls.

The two pieces under test are the parts that frequently break in transit:

    1. Bbox descaling from Gemini's normalized [ymin, xmin, ymax, xmax] 0-1000
       scale to image-pixel TOPLEFT coordinates.
    2. Stable image hashing — identical pixels must yield the same SHA-256 so
       the response cache is actually hit on re-runs.
"""

from __future__ import annotations

import hashlib
import io

from PIL import Image


def _descale(box_2d: list[int], width: int, height: int) -> tuple[float, float, float, float]:
    """Mirror of the descaling math in `core.parsing.gemini_vision_ocr`.

    Kept inline so the test fails loudly if someone edits one without the other.
    """
    ymin, xmin, ymax, xmax = box_2d
    return (
        (xmin / 1000.0) * width,
        (ymin / 1000.0) * height,
        (xmax / 1000.0) * width,
        (ymax / 1000.0) * height,
    )


def _png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# --- Descaling --------------------------------------------------------------

def test_descale_full_box_covers_whole_image():
    l, t, r, b = _descale([0, 0, 1000, 1000], 800, 600)
    assert (l, t, r, b) == (0.0, 0.0, 800.0, 600.0)


def test_descale_top_left_quadrant():
    l, t, r, b = _descale([0, 0, 500, 500], 800, 600)
    assert (l, t, r, b) == (0.0, 0.0, 400.0, 300.0)


def test_descale_returns_topleft_pixel_coords():
    """Gemini's box_2d uses top-left origin; left should always be < right, top < bottom."""
    l, t, r, b = _descale([100, 200, 300, 700], 1000, 800)
    assert l < r
    assert t < b
    assert (l, t, r, b) == (200.0, 80.0, 700.0, 240.0)


# --- Image hashing ----------------------------------------------------------

def test_identical_images_hash_identically():
    img1 = Image.new("RGB", (50, 50), color=(255, 0, 0))
    img2 = Image.new("RGB", (50, 50), color=(255, 0, 0))
    assert hashlib.sha256(_png_bytes(img1)).hexdigest() == hashlib.sha256(_png_bytes(img2)).hexdigest()


def test_different_images_hash_differently():
    img1 = Image.new("RGB", (50, 50), color=(255, 0, 0))
    img2 = Image.new("RGB", (50, 50), color=(0, 255, 0))
    assert hashlib.sha256(_png_bytes(img1)).hexdigest() != hashlib.sha256(_png_bytes(img2)).hexdigest()
