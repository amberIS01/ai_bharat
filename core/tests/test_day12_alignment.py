"""Day-12 tests — covers the verdict-alignment fixes (B1, B2) and the
demo-snapshot mechanism (B4) at a unit level.

The full end-to-end alignment test is `verify_day11`, which runs the
real bootstrap + OPA + Gemini path and is too slow / stateful for
pytest. These are the targeted unit tests.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError

from core.policy import parsers


# ---------------------------------------------------------------------------
# B1 — fact_for_rego title-based dispatch
# ---------------------------------------------------------------------------

class _FakeCriterion:
    def __init__(self, code, title="", requirement_text=""):
        self.code = code
        self.title = title
        self.requirement_text = requirement_text


class _FakeFact:
    def __init__(self, value, ocr_confidence=1.0):
        self.value = value
        self.ocr_confidence = ocr_confidence
        self.raw_extraction_json = {"found": bool(value), "reason_if_low": ""}


def test_dispatch_routes_turnover_regardless_of_code():
    """The smoke-gun regression: when Gemini assigns C-5 (instead of C-1) to
    "Min Annual Turnover", value_inr must still be injected because the rule
    routed by title is c1_turnover.rego."""
    for code in ("C-1", "C-5", "C-99"):
        out = parsers.fact_for_rego(
            _FakeCriterion(code, title="Min Annual Turnover"),
            _FakeFact("Rs. 6,80,00,000/-"),
        )
        assert out["value_inr"] == 68000000, f"failed at code={code}"
        assert "iso_present" not in out, f"unwanted ISO field at code={code}"


def test_dispatch_routes_gst_to_gstin_field():
    out = parsers.fact_for_rego(
        _FakeCriterion("C-4", title="Valid GST Registration"),
        _FakeFact("29AAAAA0000A1Z5"),
    )
    assert out["gstin"] == "29AAAAA0000A1Z5"


def test_dispatch_returns_base_when_no_rule_matches():
    out = parsers.fact_for_rego(
        _FakeCriterion("C-1", title="Registered Contractor Status"),
        _FakeFact("(absent)"),
    )
    # No category-specific field added; only the base shape.
    assert "value_inr" not in out
    assert "gstin" not in out
    assert "dsc_class" not in out
    assert "iso_present" not in out
    # Base shape always present:
    assert "value" in out
    assert "ocr_confidence" in out


# ---------------------------------------------------------------------------
# B2 — _aggregate_confidence trusts clean blocks
# ---------------------------------------------------------------------------

def test_aggregate_confidence_does_not_floor_on_clean_blocks():
    """The DSC-extraction regression: blocks are 1.0, Gemini self-doubts at
    0.8, but the resulting Fact must remain at 1.0 (not 0.8)."""
    from core.extraction.facts_extractor import _aggregate_confidence
    blocks = [SimpleNamespace(confidence=1.0)]
    assert _aggregate_confidence(0.8, blocks) == 1.0


def test_aggregate_confidence_keeps_floor_on_noisy_blocks():
    """Bidder C × distorted GST: blocks have conf 0.7, Gemini 0.9 — keep
    the conservative 0.7 floor so ABSTAIN routing still fires."""
    from core.extraction.facts_extractor import _aggregate_confidence
    blocks = [SimpleNamespace(confidence=0.9), SimpleNamespace(confidence=0.7)]
    assert _aggregate_confidence(0.9, blocks) == 0.7


def test_aggregate_confidence_picks_higher_when_blocks_mixed_at_threshold():
    """If min block is exactly at threshold (0.85), Gemini's higher self-
    confidence should win — this is the fence between safe and risky."""
    from core.extraction.facts_extractor import _aggregate_confidence
    blocks = [SimpleNamespace(confidence=0.85)]
    assert _aggregate_confidence(0.95, blocks) == 0.95


# ---------------------------------------------------------------------------
# B4 — snapshot_demo_db management command
# ---------------------------------------------------------------------------

def test_snapshot_demo_db_writes_target_file(db, tmp_path, monkeypatch):
    """The command must copy db.sqlite3 to db.sqlite3.demo (or the
    --target path) and print a sha256 + size."""
    # Place a fake src DB at BASE_DIR/db.sqlite3 (or use the live one).
    from core.management.commands import snapshot_demo_db as snap
    base = Path(settings.BASE_DIR)
    src = base / "db.sqlite3"
    target = tmp_path / "db.sqlite3.demo"

    # Ensure the source exists; if not, skip.
    if not src.exists():
        pytest.skip("No db.sqlite3 in BASE_DIR; cannot test snapshot.")

    # Force the chain to be ok by passing --force (we don't care about
    # chain state for this unit test).
    call_command("snapshot_demo_db", "--target", str(target), "--force")
    assert target.exists()
    assert target.stat().st_size == src.stat().st_size


def test_snapshot_demo_db_refuses_on_missing_source(db, tmp_path, settings):
    """If db.sqlite3 doesn't exist (somehow), the command must error out
    with a CommandError, not crash."""
    settings.BASE_DIR = tmp_path  # there's no db.sqlite3 here
    with pytest.raises(CommandError, match="Source DB not found"):
        call_command("snapshot_demo_db", "--target", str(tmp_path / "out.sqlite3"))


# ---------------------------------------------------------------------------
# B7 — architecture.svg deck artifact exists
# ---------------------------------------------------------------------------

def test_architecture_svg_exists_and_is_nonempty():
    p = Path(settings.BASE_DIR) / "docs" / "architecture.svg"
    assert p.exists(), "Day-12 deliverable: docs/architecture.svg must exist"
    assert p.stat().st_size > 5_000, "Diagram should be substantial (≥ 5 KB)"
    content = p.read_text(encoding="utf-8")
    # Sanity check that the SVG contains the four pipeline lanes.
    for label in ("INGEST", "EXTRACT", "EVALUATE", "AUDIT", "OFFICER UI"):
        assert label in content, f"architecture.svg missing the {label} lane"
