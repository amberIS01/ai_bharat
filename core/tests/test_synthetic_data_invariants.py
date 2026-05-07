"""Synthetic data invariants — fast, pure-Python checks that the demo
profile of each bidder still produces the intended PASS / FAIL / ABSTAIN.

These run in ms; no Django DB, no Docling, no API calls.
"""

from __future__ import annotations

from core.synthetic.bidders import (
    ALL_BIDDERS,
    BIDDER_A,
    BIDDER_B,
    BIDDER_C,
)
from core.synthetic.criteria import CRITERIA


# Threshold from the demo design: criterion C-1 requires turnover >= 5 Cr.
TURNOVER_THRESHOLD_INR = 5_00_00_000


def test_three_bidders_with_distinct_codes():
    codes = sorted(b.code for b in ALL_BIDDERS)
    assert codes == ["A", "B", "C"]


def test_bidder_a_passes_turnover_threshold():
    """Bidder A — highest year must be at or above 5 Cr (the C-1 threshold)."""
    assert BIDDER_A.highest_turnover.amount >= TURNOVER_THRESHOLD_INR


def test_bidder_b_fails_turnover_threshold():
    """Bidder B — highest year must be strictly below 5 Cr (drives the FAIL)."""
    assert BIDDER_B.highest_turnover.amount < TURNOVER_THRESHOLD_INR


def test_bidder_c_passes_turnover_threshold():
    """Bidder C's turnover should be fine — it FAILs ABSTAINs on GST, not turnover."""
    assert BIDDER_C.highest_turnover.amount >= TURNOVER_THRESHOLD_INR


def test_only_bidder_c_has_gst_distortion():
    distorters = [b.code for b in ALL_BIDDERS if b.gst_distort]
    assert distorters == ["C"]


def test_each_bidder_has_at_least_three_qualifying_works():
    """Criterion C-4 requires >= 3 similar works above the 1.5 Cr threshold."""
    for b in ALL_BIDDERS:
        qualifying = [w for w in b.works if w.above_threshold]
        assert len(qualifying) >= 3, f"Bidder {b.code} has only {len(qualifying)} qualifying works"


def test_bidder_a_audit_amount_words_match():
    """Bidder A's highest year must be visible as 'Six Crore Eighty Lakh' for OCR/LLM extraction."""
    highest = BIDDER_A.highest_turnover
    assert highest.fy == "FY 2024-25"
    assert "Six Crore" in highest.amount_words


def test_criteria_have_one_optional_and_four_mandatory():
    optional = [c for c in CRITERIA if not c.mandatory]
    mandatory = [c for c in CRITERIA if c.mandatory]
    assert len(mandatory) == 4
    assert len(optional) == 1
    assert optional[0].id == "C-5"


def test_indian_inr_format_helper():
    """The lakh/crore comma separator must match Indian convention."""
    from core.synthetic.bidders import _format_inr
    assert _format_inr(5_00_00_000) == "5,00,00,000"
    assert _format_inr(1_50_00_000) == "1,50,00,000"
    assert _format_inr(680_00_000) == "68,000,000".replace("68,0", "6,80,") or _format_inr(680_00_000) == "6,80,00,000"
    assert _format_inr(1_000) == "1,000"
