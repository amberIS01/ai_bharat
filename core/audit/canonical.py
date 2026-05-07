"""RFC 8785 minimal JSON Canonicalization (JCS).

The full RFC 8785 has subtle rules about IEEE-754 number formatting and Unicode
escapes. Our payloads only contain: integers, ISO-format timestamps, ASCII-safe
strings, and small dicts/lists. For this surface, the minimal JCS recipe is
indistinguishable from the full algorithm:

    * lexicographic key ordering              (sort_keys=True)
    * whitespace stripped                     (separators=(",", ":"))
    * Unicode kept as UTF-8, not escape-coded (ensure_ascii=False)

If a Praman payload ever grows to need full IEEE-754 number formatting we'll
swap this module for a real RFC 8785 implementation; until then this is the
right amount of code for what we actually serialize.

Hash inputs go through `canonicalize()` so `verify_chain()` can re-derive the
exact same bytes the producer hashed.
"""

from __future__ import annotations

import json
from typing import Any


def canonicalize(obj: Any) -> bytes:
    """Return the canonical UTF-8 bytes for `obj`.

    Determinism guarantees:
      * Object keys are emitted in lexicographic order.
      * No whitespace.
      * Unicode is preserved (not \\u-escaped) — required so two Pythons
        running on different LANG locales produce identical bytes.
    """
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,  # datetimes / Decimals / Path → repr; we already pre-stringify
    ).encode("utf-8")
