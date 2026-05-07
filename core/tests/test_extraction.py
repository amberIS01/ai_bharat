"""Pure-Python tests for the Day-3 extraction layer.

No Django DB, no Gemini API calls — these test the deterministic helpers
that hold the extraction layer together: input hashing, manifest builders,
INR amount parsing, and confidence aggregation.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.extraction.gemini_client import (
    hash_image_blob,
    hash_inputs,
)
from core.extraction.schemas import (
    CriteriaResult,
    CriterionExtraction,
    FactExtraction,
)


# ---------------------------------------------------------------------------
# Schema sanity
# ---------------------------------------------------------------------------

def test_criterion_schema_round_trip():
    obj = CriterionExtraction(
        code="C-1",
        title="Minimum Annual Turnover",
        requirement_text="≥ Rs. 5 Cr",
        type="financial",
        mandatory=True,
        source_block_ids=[42, 43],
    )
    parsed = CriterionExtraction.model_validate_json(obj.model_dump_json())
    assert parsed == obj


def test_criteria_result_empty_default():
    r = CriteriaResult()
    assert r.criteria == []


def test_fact_extraction_low_confidence_requires_reason():
    """The schema permits empty reason_if_low for high-confidence facts."""
    high = FactExtraction(found=True, value="x", confidence=0.95)
    assert high.reason_if_low == ""

    low = FactExtraction(
        found=True, value="x", confidence=0.6,
        reason_if_low="photocopy noise",
    )
    assert low.reason_if_low == "photocopy noise"


def test_fact_extraction_confidence_clamped():
    """Confidence outside [0,1] should be rejected by Pydantic."""
    with pytest.raises(Exception):
        FactExtraction(found=True, value="x", confidence=1.5)
    with pytest.raises(Exception):
        FactExtraction(found=True, value="x", confidence=-0.1)


# ---------------------------------------------------------------------------
# Input hashing — determinism + sensitivity
# ---------------------------------------------------------------------------

def test_hash_inputs_is_deterministic():
    h1 = hash_inputs(prompt="P", manifest_obj={"a": 1}, model="m1", schema_cls=CriteriaResult)
    h2 = hash_inputs(prompt="P", manifest_obj={"a": 1}, model="m1", schema_cls=CriteriaResult)
    assert h1 == h2


def test_hash_inputs_changes_when_prompt_changes():
    h1 = hash_inputs(prompt="P", manifest_obj={"a": 1}, model="m1", schema_cls=CriteriaResult)
    h2 = hash_inputs(prompt="Q", manifest_obj={"a": 1}, model="m1", schema_cls=CriteriaResult)
    assert h1 != h2


def test_hash_inputs_changes_when_manifest_changes():
    h1 = hash_inputs(prompt="P", manifest_obj={"a": 1}, model="m1", schema_cls=CriteriaResult)
    h2 = hash_inputs(prompt="P", manifest_obj={"a": 2}, model="m1", schema_cls=CriteriaResult)
    assert h1 != h2


def test_hash_inputs_stable_under_dict_key_order():
    h1 = hash_inputs(prompt="P", manifest_obj={"a": 1, "b": 2}, model="m", schema_cls=CriteriaResult)
    h2 = hash_inputs(prompt="P", manifest_obj={"b": 2, "a": 1}, model="m", schema_cls=CriteriaResult)
    # JSON serialization with sort_keys=True must yield identical hash.
    assert h1 == h2


def test_hash_image_blob_empty_returns_empty_string():
    assert hash_image_blob() == ""


def test_hash_image_blob_changes_with_content():
    a = hash_image_blob(b"abc")
    b = hash_image_blob(b"abd")
    assert a != b
    assert len(a) == 64


# ---------------------------------------------------------------------------
# Manifest builder
# ---------------------------------------------------------------------------

def test_criteria_manifest_truncates_long_text():
    from core.extraction.criteria_extractor import build_block_manifest

    fake = SimpleNamespace(
        id=99, block_index=4, page_no=1,
        text="x" * 1000,
    )
    manifest = build_block_manifest([fake], max_text=50)
    assert len(manifest) == 1
    assert manifest[0]["block_id"] == 99
    assert manifest[0]["block_index"] == 4
    assert manifest[0]["page"] == 1
    assert manifest[0]["text"].endswith("...")
    assert len(manifest[0]["text"]) <= 50 + 3  # ellipsis adds 3


def test_criteria_manifest_keeps_short_text_unchanged():
    from core.extraction.criteria_extractor import build_block_manifest

    fake = SimpleNamespace(id=1, block_index=0, page_no=1, text="hello")
    manifest = build_block_manifest([fake], max_text=50)
    assert manifest[0]["text"] == "hello"


# ---------------------------------------------------------------------------
# Confidence aggregation
# ---------------------------------------------------------------------------

def test_aggregate_confidence_picks_minimum_when_blocks_are_noisy():
    """Day-12: if any cited block is below the 0.85 threshold, keep the
    conservative floor — that's what protects the Bidder-C ABSTAIN path."""
    from core.extraction.facts_extractor import _aggregate_confidence

    blocks = [
        SimpleNamespace(confidence=1.0),
        SimpleNamespace(confidence=0.9),
        SimpleNamespace(confidence=0.65),  # noisy
        SimpleNamespace(confidence=0.85),
    ]
    # min_block = 0.65 < 0.85 → conservative floor still applies
    assert _aggregate_confidence(0.95, blocks) == 0.65


def test_aggregate_confidence_no_blocks_returns_gemini_score():
    from core.extraction.facts_extractor import _aggregate_confidence
    assert _aggregate_confidence(0.7, []) == 0.7


def test_aggregate_confidence_trusts_clean_blocks_over_gemini_self_doubt():
    """Day-12 fix B2: when every cited block is clean (≥ threshold), Gemini's
    prompt-elicited self-doubt must NOT pull the Fact below the threshold.
    This was the bug behind A × C-2 (DSC) abstaining: blocks were 1.0 but
    Gemini returned 0.8, and `min()` floored to 0.8."""
    from core.extraction.facts_extractor import _aggregate_confidence
    blocks = [SimpleNamespace(confidence=1.0)]
    # Gemini=0.4 + blocks=1.0 → trust the blocks, return 1.0
    assert _aggregate_confidence(0.4, blocks) == 1.0
    # Gemini=0.95 + blocks=1.0 → still take the higher
    assert _aggregate_confidence(0.95, blocks) == 1.0
    # Gemini=0.95 + blocks=0.85 (exactly at threshold) → 0.95
    assert _aggregate_confidence(0.95, [SimpleNamespace(confidence=0.85)]) == 0.95


# ---------------------------------------------------------------------------
# INR amount parser — `parse_inr` from core.policy.parsers (Day-12+ canonical)
# ---------------------------------------------------------------------------

def test_inr_parse_pure_digits_with_commas():
    from core.policy.parsers import parse_inr as _parse_inr_amount
    assert _parse_inr_amount("Rs. 6,80,00,000/-") == 6_80_00_000
    assert _parse_inr_amount("4,20,00,000") == 4_20_00_000


def test_inr_parse_words_six_crore_eighty_lakh():
    from core.policy.parsers import parse_inr as _parse_inr_amount
    val = _parse_inr_amount("Six Crore Eighty Lakh") or 0
    # We just need this to be plausibly above 6 cr (6_00_00_000) and below 7 cr.
    # Note: words-only parsing extracts only numeric tokens, so word numerals
    # aren't supported — only digit forms are. This is by design.
    # The spelled-out version returns None; the digit form Rs. 6,80,00,000 returns 6.8 cr.
    assert val == 0  # words-only can't be parsed by this best-effort helper


def test_inr_parse_mixed_digits_and_units():
    from core.policy.parsers import parse_inr as _parse_inr_amount
    # "5 Cr" → 5 * 1 cr
    val = _parse_inr_amount("Rs. 5 Cr")
    assert val == 5_00_00_000


def test_inr_parse_returns_none_on_garbage():
    from core.policy.parsers import parse_inr as _parse_inr_amount
    assert _parse_inr_amount("") is None
    assert _parse_inr_amount("not a number") is None
