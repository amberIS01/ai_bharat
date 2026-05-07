"""Pure-Python tests for the Day-4 policy layer.

Three concerns:

  1. parse_* helpers in core/policy/parsers — string → Rego-friendly typed values.
  2. fact_for_rego dispatcher — picks the right per-criterion parser.
  3. OPAError surface in opa_client — fails loudly on bad responses.

OPA itself is exercised by the `verify_verdicts` management-command-level
acceptance test (which spawns the real Rego engine). Here we mock httpx.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.policy import parsers
from core.policy.opa_client import OPAError, opa_query


# ---------------------------------------------------------------------------
# parse_inr
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    ("Rs. 6,80,00,000/-", 68000000),
    ("Rs. 4,20,00,000/-", 42000000),
    ("75,00,000", 7500000),
    ("Rs. 5 Cr", 50000000),
    ("1.5 Cr", 15000000),
    ("Rs. 2.75 Crore", 27500000),
    ("", None),
    ("not a number", None),
    ("year 2024", None),  # 2024 is only 4 digits and not a rupee amount
])
def test_parse_inr_table(value, expected):
    assert parsers.parse_inr(value) == expected


def test_parse_inr_picks_largest_when_multiple_numbers():
    """Bidder fact strings sometimes mention smaller numbers (PIN codes, year ranges).
    The headline rupee figure is reliably the largest digit string."""
    assert parsers.parse_inr("Rs. 6,80,00,000 (FY 2024-25)") == 68000000


# ---------------------------------------------------------------------------
# parse_gstin
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    ("29AAAAA0000A1Z5", "29AAAAA0000A1Z5"),
    ("GSTIN: 29CCCCC0000C3Z7", "29CCCCC0000C3Z7"),
    ("Plot 27 Demo State - 000003", ""),  # PIN-style number, not a GSTIN
    ("", ""),
    ("29aaaaa0000a1z5", "29AAAAA0000A1Z5"),  # case-insensitive — uppercased
])
def test_parse_gstin_table(value, expected):
    assert parsers.parse_gstin(value) == expected


def test_parse_gstin_rejects_wrong_length():
    """A 14-char string that almost looks like a GSTIN must NOT match."""
    assert parsers.parse_gstin("29AAAAA0000A1Z") == ""


# ---------------------------------------------------------------------------
# parse_dsc_class
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    ("Class-3 Digital Signature Certificate", "Class-3"),
    ("Class 3 DSC issued by eMudhra", "Class-3"),
    ("class3 token", "Class-3"),
    ("Class-2 cert (deprecated)", "Class-2"),
    ("a digital signature certificate", ""),
    ("", ""),
])
def test_parse_dsc_class_table(value, expected):
    assert parsers.parse_dsc_class(value) == expected


# ---------------------------------------------------------------------------
# parse_works
# ---------------------------------------------------------------------------

def test_parse_works_counts_qualifying_above_1_5_cr():
    value = (
        "Construction of X (Rs. 1,85,00,000/-) | "
        "Renovation of Y (Rs. 2,40,00,000/-) | "
        "Storage Shed Z (Rs. 1,55,00,000/-)"
    )
    out = parsers.parse_works(value)
    assert out["work_count"] == 3
    assert out["qualifying_count"] == 3
    assert out["work_values_inr"] == [18500000, 24000000, 15500000]


def test_parse_works_excludes_below_threshold():
    value = (
        "Tiny job (Rs. 50,00,000) | "                # 50 lakh — below threshold
        "Real work (Rs. 1,80,00,000)"                 # 1.8 cr — above threshold
    )
    out = parsers.parse_works(value)
    assert out["work_count"] == 2
    assert out["qualifying_count"] == 1


def test_parse_works_handles_missing_values():
    """If the LLM forgets to include rupee amounts, qualifying_count stays 0."""
    value = "Repair of A | Renovation of B | Construction of C"
    out = parsers.parse_works(value)
    assert out["work_count"] == 3
    assert out["qualifying_count"] == 0


def test_parse_works_empty_string():
    out = parsers.parse_works("")
    assert out == {"work_count": 0, "work_values_inr": [], "qualifying_count": 0}


# ---------------------------------------------------------------------------
# parse_iso_present
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    ("ISO 9001:2015", True),
    ("iso 9001", True),
    ("Certified to 9001:2015 standard", True),
    ("ISO 27001 (info security)", False),  # different standard
    ("(absent)", False),
    ("", False),
])
def test_parse_iso_present_table(value, expected):
    assert parsers.parse_iso_present(value) == expected


# ---------------------------------------------------------------------------
# fact_for_rego dispatcher
# ---------------------------------------------------------------------------

class _FakeCriterion:
    """A criterion stub. Day-12 fact_for_rego dispatch is now title-based,
    so the title must look like the canonical category to route correctly.
    Code is preserved for legacy callers but no longer steers the dispatch."""

    def __init__(self, code, title="", requirement_text=""):
        self.code = code
        self.title = title
        self.requirement_text = requirement_text


class _FakeFact:
    def __init__(self, value, ocr_confidence=1.0, raw=None):
        self.value = value
        self.ocr_confidence = ocr_confidence
        self.raw_extraction_json = raw or {"found": bool(value), "reason_if_low": ""}


def test_fact_for_rego_turnover_includes_value_inr():
    out = parsers.fact_for_rego(
        _FakeCriterion("C-1", title="Min Annual Turnover"),
        _FakeFact("Rs. 6,80,00,000/-"),
    )
    assert out["value_inr"] == 68000000
    assert out["found"] is True
    assert out["ocr_confidence"] == 1.0


def test_fact_for_rego_gst_includes_gstin():
    out = parsers.fact_for_rego(
        _FakeCriterion("C-2", title="Valid GST Registration"),
        _FakeFact("29AAAAA0000A1Z5"),
    )
    assert out["gstin"] == "29AAAAA0000A1Z5"


def test_fact_for_rego_dsc_includes_dsc_class():
    out = parsers.fact_for_rego(
        _FakeCriterion("C-3", title="Class-3 Digital Signature Certificate"),
        _FakeFact("Class-3 DSC valid till 2027"),
    )
    assert out["dsc_class"] == "Class-3"


def test_fact_for_rego_experience_includes_work_counts():
    out = parsers.fact_for_rego(
        _FakeCriterion("C-4", title="Past Experience - Similar Works"),
        _FakeFact("Work A (Rs. 1,80,00,000) | Work B (Rs. 2,00,00,000) | Work C (Rs. 1,55,00,000)"),
    )
    assert out["work_count"] == 3
    assert out["qualifying_count"] == 3


def test_fact_for_rego_iso_iso_present():
    out_present = parsers.fact_for_rego(
        _FakeCriterion("C-5", title="ISO 9001 Quality Management"),
        _FakeFact("ISO 9001:2015"),
    )
    out_absent = parsers.fact_for_rego(
        _FakeCriterion("C-5", title="ISO 9001 Quality Management"),
        _FakeFact("(absent)"),
    )
    assert out_present["iso_present"] is True
    assert out_absent["iso_present"] is False


def test_fact_for_rego_dispatch_is_title_based_not_code_based():
    """Day-12 regression: even when Gemini assigns C-5 to a Turnover criterion
    (instead of the canonical C-1), fact_for_rego must inject value_inr
    because the rule routed by title is c1_turnover.rego."""
    crit = _FakeCriterion("C-5", title="Min Annual Turnover")
    out = parsers.fact_for_rego(crit, _FakeFact("Rs. 5,40,00,000/-"))
    assert "value_inr" in out
    assert out["value_inr"] == 54000000
    # And it must NOT inject ISO fields (which the old code-based dispatch did).
    assert "iso_present" not in out


# ---------------------------------------------------------------------------
# opa_query error paths (httpx mocked)
# ---------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError("err", request=None, response=None)

    def json(self):
        return self._body


def test_opa_query_no_result_field_raises():
    with patch("core.policy.opa_client.httpx.post", return_value=_FakeResp(200, {})):
        with pytest.raises(OPAError, match="no `result`"):
            opa_query("praman/criteria/c1/result", {"input": {}})


def test_opa_query_non_dict_result_raises():
    with patch("core.policy.opa_client.httpx.post", return_value=_FakeResp(200, {"result": "PASS"})):
        with pytest.raises(OPAError, match="non-dict result"):
            opa_query("praman/criteria/c1/result", {"input": {}})


def test_opa_query_missing_verdict_raises():
    with patch("core.policy.opa_client.httpx.post",
               return_value=_FakeResp(200, {"result": {"rule_id": "x"}})):
        with pytest.raises(OPAError, match="missing the `verdict` key"):
            opa_query("praman/criteria/c1/result", {"input": {}})


def test_opa_query_happy_path_returns_result_dict():
    body = {"result": {"verdict": "PASS", "rule_id": "x.pass", "reason": "ok", "bindings": {}}}
    with patch("core.policy.opa_client.httpx.post", return_value=_FakeResp(200, body)):
        out = opa_query("praman/criteria/c1/result", {"input": {}})
    assert out["verdict"] == "PASS"
    assert out["rule_id"] == "x.pass"
