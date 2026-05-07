"""Day-13 tests — covers the deck artifacts (T2.1-T2.4) and the
snapshot-freshness check (T1.1).

These tests verify the *existence and shape* of the documentation
artifacts. We deliberately do NOT lint markdown content (that's
Sahil's prose), only that the structural anchors a slide-builder
expects are present.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError


# ---------------------------------------------------------------------------
# T1.1 — bootstrap_demo snapshot freshness check + --restore-snapshot flag
# ---------------------------------------------------------------------------

def test_bootstrap_demo_restore_snapshot_overwrites_live_db(tmp_path, monkeypatch):
    """--restore-snapshot must replace the live DB with the snapshot."""
    import core.management.commands.bootstrap_demo as bd
    snap = tmp_path / "db.sqlite3.demo"
    live = tmp_path / "db.sqlite3"
    snap.write_bytes(b"SNAPSHOT-CONTENT")
    live.write_bytes(b"LIVE-CONTENT")

    monkeypatch.setattr(bd, "DEMO_SNAPSHOT_PATH", snap)
    monkeypatch.setattr(bd, "LIVE_DB_PATH", live)

    call_command("bootstrap_demo", "--restore-snapshot")
    assert live.read_bytes() == b"SNAPSHOT-CONTENT"


def test_bootstrap_demo_restore_snapshot_errors_when_snapshot_missing(tmp_path, monkeypatch):
    """If --restore-snapshot is requested but no snapshot exists, error out."""
    import core.management.commands.bootstrap_demo as bd
    snap = tmp_path / "db.sqlite3.demo"  # not created
    live = tmp_path / "db.sqlite3"

    monkeypatch.setattr(bd, "DEMO_SNAPSHOT_PATH", snap)
    monkeypatch.setattr(bd, "LIVE_DB_PATH", live)

    with pytest.raises(CommandError, match="--restore-snapshot requested but"):
        call_command("bootstrap_demo", "--restore-snapshot")


def test_bootstrap_demo_warns_on_diverged_snapshot(tmp_path, monkeypatch, capsys):
    """When both live and snapshot exist with different content, the
    command must print a loud warning naming both SHAs."""
    import core.management.commands.bootstrap_demo as bd
    snap = tmp_path / "db.sqlite3.demo"
    live = tmp_path / "db.sqlite3"
    snap.write_bytes(b"SNAPSHOT-CONTENT")
    live.write_bytes(b"DIFFERENT-LIVE-CONTENT")

    monkeypatch.setattr(bd, "DEMO_SNAPSHOT_PATH", snap)
    monkeypatch.setattr(bd, "LIVE_DB_PATH", live)

    # Force the rest of bootstrap_demo to no-op via --skip-extract +
    # --skip-evaluate + --skip-synthetic; the warning comes BEFORE any of
    # those steps.
    try:
        call_command(
            "bootstrap_demo", "--skip-synthetic", "--skip-extract", "--skip-evaluate",
        )
    except Exception:  # noqa: BLE001
        # The remaining steps may raise; we only care about the warning text.
        pass
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "diverges from db.sqlite3.demo snapshot" in combined
    assert "--restore-snapshot" in combined


# ---------------------------------------------------------------------------
# T1.2 — README + QUICKSTART
# ---------------------------------------------------------------------------

def test_readme_exists_and_links_to_quickstart():
    base = Path(settings.BASE_DIR)
    readme = base / "README.md"
    assert readme.exists()
    body = readme.read_text(encoding="utf-8")
    assert "QUICKSTART" in body
    assert "architecture.svg" in body


def test_quickstart_md_has_seven_step_recipe():
    base = Path(settings.BASE_DIR)
    qs = base / "docs" / "QUICKSTART.md"
    assert qs.exists()
    body = qs.read_text(encoding="utf-8")
    # The 7-step recipe must mention every command.
    for cmd in (
        "git clone",
        "pip install -r requirements.txt",
        "python manage.py migrate",
        "deploy/bin/opa run",       # OPA in a separate terminal
        "bootstrap_demo",
        "runserver",
        "officer / praman_demo_2026",
    ):
        assert cmd in body, f"QUICKSTART missing: {cmd!r}"


def test_quickstart_md_has_troubleshooting_section():
    base = Path(settings.BASE_DIR)
    qs = base / "docs" / "QUICKSTART.md"
    body = qs.read_text(encoding="utf-8")
    assert "Troubleshooting" in body
    # Each known failure mode must be addressed.
    for symptom in (
        "OPA not reachable",
        "no_rule_matched",
        "Live DB diverges",
    ):
        assert symptom in body, f"QUICKSTART troubleshooting missing: {symptom!r}"


# ---------------------------------------------------------------------------
# T2.1 — deck_outline.md
# ---------------------------------------------------------------------------

def test_deck_outline_has_all_8_slides():
    base = Path(settings.BASE_DIR)
    deck = base / "docs" / "deck_outline.md"
    assert deck.exists()
    body = deck.read_text(encoding="utf-8")
    for n in range(1, 9):
        assert f"## Slide {n} ·" in body, f"deck_outline missing Slide {n}"


def test_deck_outline_explicitly_excludes_narration():
    """Per CLAUDE.md, the deck outline must NOT contain speaker notes
    or video script verbatim. Test for the meta-statement that says so."""
    base = Path(settings.BASE_DIR)
    body = (base / "docs" / "deck_outline.md").read_text(encoding="utf-8")
    assert "narration" in body.lower()
    assert "structural" in body.lower()


# ---------------------------------------------------------------------------
# T2.2 — demo_shotlist.md
# ---------------------------------------------------------------------------

def test_demo_shotlist_includes_all_5_hero_screens():
    base = Path(settings.BASE_DIR)
    sl = base / "docs" / "demo_shotlist.md"
    assert sl.exists()
    body = sl.read_text(encoding="utf-8")
    for url in (
        "/officer/",                # dashboard
        "/officer/rfp/21/criteria/",
        "/officer/rfp/21/grid/",
        "/officer/verdict/",        # drilldown
        "/officer/rfp/21/signoff/",
    ):
        assert url in body, f"shotlist missing URL {url!r}"


def test_demo_shotlist_has_recording_checklist():
    base = Path(settings.BASE_DIR)
    body = (base / "docs" / "demo_shotlist.md").read_text(encoding="utf-8")
    assert "Recording checklist" in body


# ---------------------------------------------------------------------------
# T2.3 — demo_recovery.md
# ---------------------------------------------------------------------------

def test_demo_recovery_covers_all_known_failure_modes():
    base = Path(settings.BASE_DIR)
    rec = base / "docs" / "demo_recovery.md"
    assert rec.exists()
    body = rec.read_text(encoding="utf-8")
    for failure_mode in (
        "OPA is not running",
        "CDN unreachable",
        "Snapshot DB is stale",
        "Audit chain integrity broken",
    ):
        assert failure_mode in body, f"recovery doc missing: {failure_mode!r}"


# ---------------------------------------------------------------------------
# T2.4 — numbers.md
# ---------------------------------------------------------------------------

def test_numbers_md_includes_test_count_and_verdict_matrix():
    """Day-15 fix #6: don't pin the docs to a stale literal. Assert the
    file *has* a test-count claim (any 3-digit number followed by 'passed'
    or 'tests'), but don't lock it to a specific number that goes stale
    every time we add tests."""
    import re
    base = Path(settings.BASE_DIR)
    nm = base / "docs" / "numbers.md"
    assert nm.exists()
    body = nm.read_text(encoding="utf-8")
    # Test count claim — any 3-digit count followed by passed/tests/etc.
    has_test_count = re.search(r"\b\d{3}\b.*(?:passed|tests|skip)", body, re.IGNORECASE)
    assert has_test_count is not None, (
        "numbers.md must include a current test-count claim "
        "(format: NNN passed / NNN tests). Update after each pytest run."
    )
    # Verdict matrix must be enumerated
    assert "Bidder A" in body
    assert "Bidder B" in body
    assert "Bidder C" in body
    # Round-1 non-negotiables proof-point table
    assert "**N1**" in body
    assert "**N2**" in body
    assert "**N3**" in body
    assert "**N4**" in body


def test_numbers_md_has_regenerate_command():
    base = Path(settings.BASE_DIR)
    body = (base / "docs" / "numbers.md").read_text(encoding="utf-8")
    assert "Regenerate command" in body
    assert "AuditEntry.objects.count()" in body
