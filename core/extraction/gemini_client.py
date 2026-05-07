"""Shared Gemini client + cache helper for the extraction layer.

Wraps `google-genai` with:
    * a singleton client per (api_key, retry policy)
    * a single entrypoint that:
        - hashes the request inputs to look up `GeminiCallCache`
        - on miss, calls Gemini with `response_schema=<Pydantic model>`
        - persists the (parsed) response back to the cache
    * deterministic input-hashing so re-runs on the same inputs are free.

Day-3 callers (criteria_extractor, facts_extractor) use `call_gemini_structured()`
exclusively so the cache is honoured everywhere and no two layers reinvent the
caching primitive.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Type, TypeVar

from django.conf import settings
from pydantic import BaseModel

from core.models import GeminiCallCache

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


# ---------------------------------------------------------------------------
# Singleton clients keyed by api_key
# ---------------------------------------------------------------------------

_CLIENTS: dict[str, Any] = {}


def _client(api_key: str | None = None):
    """Return (and lazily build) a `google.genai.Client` with sensible retry."""
    from google import genai
    from google.genai import types

    key = api_key or settings.GEMINI_API_KEY
    if not key:
        raise RuntimeError("No Gemini API key set in settings.")
    if key in _CLIENTS:
        return _CLIENTS[key]

    client = genai.Client(
        api_key=key,
        http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(
                initial_delay=1.0,
                attempts=5,
                http_status_codes=[408, 429, 500, 502, 503, 504],
            ),
            timeout=120 * 1000,  # 120 s per call
        ),
    )
    _CLIENTS[key] = client
    return client


# ---------------------------------------------------------------------------
# Hashing helpers — every input that affects the response goes into the key.
# ---------------------------------------------------------------------------

def _sha256(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def hash_inputs(*, prompt: str, manifest_obj: Any, model: str, schema_cls: Type[BaseModel]) -> str:
    """Stable hash over (prompt template, JSON-serialized manifest, model name, schema name)."""
    parts = [
        prompt,
        json.dumps(manifest_obj, sort_keys=True, default=str),
        model,
        schema_cls.__name__,
    ]
    return _sha256("\x1f".join(parts))


def hash_image_blob(*image_blobs: bytes) -> str:
    """Stable hash over a tuple of raw image bytes (concatenated under a separator)."""
    if not image_blobs:
        return ""
    h = hashlib.sha256()
    for blob in image_blobs:
        h.update(b"\x1f")
        h.update(blob)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Cache-aware call
# ---------------------------------------------------------------------------

def call_gemini_structured(
    *,
    prompt: str,
    manifest_obj: Any,
    image_blobs: list[bytes] | None = None,
    image_mime_types: list[str] | None = None,
    schema_cls: Type[T],
    model: str | None = None,
    api_key: str | None = None,
) -> tuple[T, bool]:
    """Run a structured-output Gemini call with response_schema, with caching.

    Args:
        prompt: The full prompt template (already filled in with criterion / bidder etc).
        manifest_obj: The Python object used in the prompt — JSON-serialized for hashing.
        image_blobs: Optional list of raw image / PDF bytes to attach.
        image_mime_types: Mime types matching `image_blobs`. Defaults to image/png each.
        schema_cls: Pydantic class — passed as `response_schema` and used to validate the result.
        model: Gemini model id (defaults to settings.GEMINI_MODEL_FLASH).
        api_key: Override key (defaults to settings.GEMINI_API_KEY).

    Returns:
        (parsed_instance, cache_hit_bool)
    """
    from google.genai import types

    model = model or settings.GEMINI_MODEL_FLASH
    image_blobs = image_blobs or []
    image_mime_types = image_mime_types or ["image/png"] * len(image_blobs)
    if len(image_mime_types) != len(image_blobs):
        raise ValueError("image_mime_types must have the same length as image_blobs")

    prompt_sha = hash_inputs(
        prompt=prompt, manifest_obj=manifest_obj, model=model, schema_cls=schema_cls
    )
    image_sha = hash_image_blob(*image_blobs)

    # Cache lookup
    cache_qs = GeminiCallCache.objects.filter(
        prompt_sha256=prompt_sha, image_sha256=image_sha, model=model
    )
    if cache_qs.exists():
        cached = cache_qs.first()
        cached.hits = (cached.hits or 0) + 1
        cached.save(update_fields=["hits"])
        try:
            return schema_cls(**cached.response_json), True
        except Exception as e:  # noqa: BLE001
            log.warning("Cached %s response failed validation; re-fetching: %s", schema_cls.__name__, e)
            cache_qs.delete()

    # Build the contents list — manifest is JSON-stringified into the prompt.
    contents: list[Any] = [
        prompt + "\n\nINPUT MANIFEST (JSON):\n" + json.dumps(manifest_obj, indent=2, default=str)
    ]
    for blob, mime in zip(image_blobs, image_mime_types):
        contents.append(types.Part.from_bytes(data=blob, mime_type=mime))

    client = _client(api_key)
    t0 = time.monotonic()
    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema_cls,
        ),
    )
    elapsed = time.monotonic() - t0

    # Prefer the SDK's pre-parsed object; fall back to manual JSON validation.
    parsed: T | None = getattr(response, "parsed", None)
    if parsed is None:
        text = getattr(response, "text", None) or ""
        parsed = schema_cls.model_validate_json(text)

    GeminiCallCache.objects.create(
        prompt_sha256=prompt_sha,
        image_sha256=image_sha,
        model=model,
        response_json=parsed.model_dump(),
    )

    log.info(
        "Gemini structured call: model=%s schema=%s elapsed=%.2fs cache=MISS",
        model, schema_cls.__name__, elapsed,
    )
    return parsed, False
