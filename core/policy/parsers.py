"""Convert raw Fact values into structured fields the Rego rules consume.

Each criterion has its own parser:

    C-1  → value_inr (int rupees)
    C-2  → gstin     (canonical 15-char GSTIN substring, "" if none found)
    C-3  → dsc_class ("Class-3" / "Class-2" / "" if none recognised)
    C-4  → work_count, qualifying_count, work_values_inr
    C-5  → iso_present (bool)

Rationale: parsing rupee strings, regex-extracting GSTINs, and counting "|"
separators is bog-standard Python. Putting it in Rego would make the rules
unreadable for procurement officers. Rego stays focused on the actual policy
question ("does the parsed value meet the threshold?").

These helpers also raise no exceptions — every failure-to-parse path returns
an explicit None / "" / 0 so the Rego rules can route to ABSTAIN cleanly.
"""

from __future__ import annotations

import re
from typing import Any

from core.models import Criterion, Fact

# ---------------------------------------------------------------------------
# Money parsers
# ---------------------------------------------------------------------------

_INR_DIGITS_RX = re.compile(r"[\d,]{5,}")  # at least 5 chars to avoid stray 4-digit years
_INR_WORDS_RX = re.compile(r"(\d+(?:\.\d+)?)\s*(crore|cr|lakh|l)\b", re.I)


def parse_inr(value: str) -> int | None:
    """Return integer rupees parsed from `value`, or None if no number is recoverable.

    Handles:
        "Rs. 6,80,00,000/-"             → 68000000
        "5 Cr"                           → 50000000
        "1.5 Cr"                         → 15000000
        "75,00,000"                      → 7500000
        "Six Crore Eighty Lakh"          → None  (we don't parse word numerals)
        ""                               → None
    """
    if not value:
        return None

    # Pure-digit form first (most common from the LLM).
    candidates: list[int] = []
    for match in _INR_DIGITS_RX.finditer(value):
        digits = re.sub(r"\D", "", match.group(0))
        if len(digits) >= 5:
            candidates.append(int(digits))
    if candidates:
        return max(candidates)  # if multiple, take the largest (usually the headline figure)

    # "5 Cr" / "1.5 Lakh" form.
    rupees = 0
    for amount_str, unit in _INR_WORDS_RX.findall(value):
        amount = float(amount_str)
        if unit.lower().startswith("c"):
            rupees += int(amount * 1_00_00_000)
        elif unit.lower().startswith("l"):
            rupees += int(amount * 1_00_000)
    return rupees or None


# ---------------------------------------------------------------------------
# GSTIN parser
# ---------------------------------------------------------------------------

# Canonical GSTIN: 2 digits state + 10 PAN + 1 entity digit + Z + 1 check char.
_GSTIN_RX = re.compile(r"\b([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z][Z][0-9A-Z])\b")


def parse_gstin(value: str) -> str:
    """Return the first canonical GSTIN substring found, or '' if none."""
    if not value:
        return ""
    m = _GSTIN_RX.search(value.upper())
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# DSC class parser
# ---------------------------------------------------------------------------

_DSC_CLASS_RX = re.compile(r"\bclass[\s\-_]*(\d)\b", re.I)


def parse_dsc_class(value: str) -> str:
    """Return the canonical DSC class label ('Class-3', 'Class-2', '') or '' if none."""
    if not value:
        return ""
    m = _DSC_CLASS_RX.search(value)
    if m is None:
        return ""
    return f"Class-{m.group(1)}"


# ---------------------------------------------------------------------------
# Past-performance works parser
# ---------------------------------------------------------------------------

def parse_works(value: str) -> dict[str, Any]:
    """Split a "|"-joined works string into per-work records.

    Expects the LLM-emitted format:
        "Construction of X (Rs. 1,85,00,000) | Renovation of Y (Rs. 2,40,00,000) | ..."

    Returns:
        {
            "work_count": <int>,
            "work_values_inr": [<int|null>, ...],
            "qualifying_count": <int>,
        }
    `qualifying_count` counts works whose parsed value is at least the
    1.5 Cr threshold (matching the C-4 Rego rule's `work_threshold_inr`).
    """
    if not value:
        return {"work_count": 0, "work_values_inr": [], "qualifying_count": 0}

    raw_pieces = [p.strip() for p in value.split("|") if p.strip()]
    values: list[int | None] = [parse_inr(p) for p in raw_pieces]
    threshold = 1_50_00_000
    qualifying = sum(1 for v in values if v is not None and v >= threshold)
    return {
        "work_count": len(raw_pieces),
        "work_values_inr": values,
        "qualifying_count": qualifying,
    }


# ---------------------------------------------------------------------------
# ISO 9001 parser
# ---------------------------------------------------------------------------

_ISO_9001_RX = re.compile(r"\bISO\s*9001\b|\b9001\s*[:\-]?\s*2015\b", re.I)


def parse_iso_present(value: str) -> bool:
    """True if the value mentions ISO 9001 / 9001:2015."""
    if not value:
        return False
    return bool(_ISO_9001_RX.search(value))


# ---------------------------------------------------------------------------
# Public dispatcher — turn (Criterion, Fact) → Rego-friendly fact dict
# ---------------------------------------------------------------------------

def fact_for_rego(criterion: Criterion, fact: Fact) -> dict[str, Any]:
    """Return the structured `input.fact` dict the Rego rules expect.

    Every dispatch starts from a common base (value, found, ocr_confidence,
    reason_if_low) and adds criterion-specific parsed fields on top.

    The dispatch is *title-based* (mirroring `rego_path_for_criterion`),
    not code-based: Gemini's criterion-code numbering is non-deterministic,
    so a re-extract that produces "Min Annual Turnover" as C-5 instead of
    C-1 must still inject `value_inr` for the C1 turnover rule.
    """
    raw = fact.raw_extraction_json or {}
    base: dict[str, Any] = {
        "value": fact.value or "",
        "found": bool(raw.get("found", bool(fact.value))),
        "ocr_confidence": float(fact.ocr_confidence),
        "reason_if_low": raw.get("reason_if_low", "") or "",
    }

    # Title-based dispatch: read the routed Rego path and inject the fields
    # *that rule* expects. If no rule maps, the base dict is returned and
    # the verdict will short-circuit to ABSTAIN no_rule_mapped upstream.
    rule_path = rego_path_for_criterion(criterion) or ""
    if "/c1/" in rule_path:        # turnover
        base["value_inr"] = parse_inr(fact.value)
    elif "/c2/" in rule_path:      # GST
        base["gstin"] = parse_gstin(fact.value)
    elif "/c3/" in rule_path:      # DSC
        base["dsc_class"] = parse_dsc_class(fact.value)
    elif "/c4/" in rule_path:      # past experience / similar works
        base.update(parse_works(fact.value))
    elif "/c5/" in rule_path:      # ISO 9001
        base["iso_present"] = parse_iso_present(fact.value)
    return base


# Map a Criterion to its Rego rule.
#
# Originally keyed on `criterion.code` (C-1..C-5), but Gemini's criteria
# extraction is non-deterministic about code numbering — re-running on a
# slightly different Block manifest can produce 7 criteria with the rupee /
# GST / DSC / experience / ISO categories renumbered. Hard-coding C-N → cN
# breaks every time the codes shift.
#
# Title/requirement-text matching is the robust dispatch: regardless of which
# integer Gemini hangs on a criterion, "annual turnover" goes to the C1 rule,
# "GST" goes to the C2 rule, and so on. Order matters — most specific first.

import re

_TITLE_TO_REGO_RULES: list[tuple[re.Pattern[str], str]] = [
    # C-1 — financial turnover threshold
    (re.compile(r"turnover|annual.*financial|profit.*loss|income", re.I),
     "praman/criteria/c1/result"),
    # C-2 — GST registration
    (re.compile(r"\bGST\b|goods\s+and\s+services\s+tax|GSTIN", re.I),
     "praman/criteria/c2/result"),
    # C-3 — Class-3 DSC
    (re.compile(r"\bDSC\b|digital\s*signature|class[\s\-_]*3", re.I),
     "praman/criteria/c3/result"),
    # C-4 — past experience / similar works
    (re.compile(r"experience|past[\s\-]*performance|similar\s+work|completed\s+work", re.I),
     "praman/criteria/c4/result"),
    # C-5 — ISO 9001 quality management
    (re.compile(r"\bISO\b|9001|quality\s+management", re.I),
     "praman/criteria/c5/result"),
]


def rego_path_for_criterion(criterion) -> str | None:
    """Resolve `criterion` to a Rego rule path by matching title + requirement_text.

    Returns None when no rule matches — e.g. Gemini extracted a pre-qualification
    clause we don't have a Rego rule for. Callers must skip rather than crash.
    """
    blob = f"{criterion.code or ''} {criterion.title or ''} {criterion.requirement_text or ''}"
    for rx, path in _TITLE_TO_REGO_RULES:
        if rx.search(blob):
            return path
    return None


# The five canonical criteria categories — used as a stable taxonomy across
# the demo even though Gemini's `code` numbering is non-deterministic.
CANONICAL_CATEGORIES = {
    "turnover":   _TITLE_TO_REGO_RULES[0][0],   # C-1
    "gst":        _TITLE_TO_REGO_RULES[1][0],   # C-2
    "dsc":        _TITLE_TO_REGO_RULES[2][0],   # C-3
    "experience": _TITLE_TO_REGO_RULES[3][0],   # C-4
    "iso":        _TITLE_TO_REGO_RULES[4][0],   # C-5
}


def find_criterion_by_category(criteria, category: str):
    """Return the first criterion whose title/requirement matches the named category.

    Used by downstream gates that need a stable way to refer to "the turnover
    criterion" / "the GST criterion" regardless of what `code` Gemini happened
    to assign on this run.

    `criteria` is any iterable of Criterion-like objects with `.code`, `.title`,
    and `.requirement_text` attributes.
    """
    if category not in CANONICAL_CATEGORIES:
        raise ValueError(
            f"Unknown category {category!r}. Valid: {sorted(CANONICAL_CATEGORIES)}"
        )
    rx = CANONICAL_CATEGORIES[category]
    for c in criteria:
        blob = f"{c.code or ''} {c.title or ''} {c.requirement_text or ''}"
        if rx.search(blob):
            return c
    return None


# Legacy alias kept for callers that still reference a static map; no new code
# should use this. New code should call `rego_path_for_criterion(c)`.
CRITERION_TO_REGO_PATH: dict[str, str] = {
    "C-1": "praman/criteria/c1/result",
    "C-2": "praman/criteria/c2/result",
    "C-3": "praman/criteria/c3/result",
    "C-4": "praman/criteria/c4/result",
    "C-5": "praman/criteria/c5/result",
}
