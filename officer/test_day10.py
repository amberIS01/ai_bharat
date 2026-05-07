"""Day-10 tests — covers the demo-defensive RED fixes (R1, R2, R3) + the
hero-screens polish (sidebar nav, criterion inline edit, signoff audit
timeline, empty-rule_id banner, citation_warnings).

Strategy:
  * Pure-Python where possible (form validation, payload-shape assertions).
  * Django Client for view-level smoke (rendered template contains the
    new pieces).
  * The Bidder-C × C-2 ABSTAIN drilldown is exercised in test_day9.py
    already; here we focus on the *new* Day-10 surface area.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client
from django.urls import reverse

from core.models import (
    Block,
    BlockSource,
    CoordOrigin,
    Criterion,
    CriterionType,
    Document,
    DocumentType,
    Fact,
    Verdict,
    VerdictStatus,
)
from officer.forms import CriterionInlineEditForm


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def auth_client(db):
    User = get_user_model()
    user = User.objects.create_superuser(
        username="day10_officer", email="d10@example.com", password="pwd",
    )
    c = Client()
    c.force_login(user)
    return c


# ---------------------------------------------------------------------------
# T1.1 — upload_tender no-RFP guard (R1)
# ---------------------------------------------------------------------------

def test_upload_tender_shows_error_when_ingest_yields_no_rfp(auth_client, db, monkeypatch):
    """Posting a 'PDF' that ingest can't classify as an RFP must NOT 500.

    We monkeypatch the ingest call to be a no-op (so no Document is created),
    then post a small PDF stub. The view must render the upload page with an
    error message instead of crashing on a None lookup.
    """
    from officer import views

    def _noop_ingest(path, *, bidder_code=None):
        return  # no-op: pretends the upload was rejected by ingest

    monkeypatch.setattr(views, "_ingest_path", _noop_ingest)

    fake = SimpleUploadedFile("not_a_real_rfp.pdf", b"%PDF-1.4\n", content_type="application/pdf")
    resp = auth_client.post(reverse("officer:upload_tender"), {"rfp_file": fake})
    assert resp.status_code == 200
    body = resp.content.decode().lower()
    assert "no rfp document" in body or "no rfp" in body
    assert Document.objects.count() == 0


# ---------------------------------------------------------------------------
# T1.2 — ingest_bundle --force refusal when downstream Facts/Verdicts cite
# ---------------------------------------------------------------------------

def test_ingest_bundle_force_blocked_by_downstream_citations(db, tmp_path):
    """Re-ingest with --force must refuse if Fact.evidence_blocks cites a
    Block on the existing Document — silently CASCADE-deleting that Block
    would orphan the Fact's M2M and break explainability."""
    rfp = Document.objects.create(
        type=DocumentType.RFP, sha256="da" * 32, original_filename="seed.pdf",
    )
    block = Block.objects.create(
        document=rfp, block_index=0, page_no=1,
        bbox_l=0, bbox_t=0, bbox_r=10, bbox_b=10,
        coord_origin=CoordOrigin.BOTTOMLEFT.value, text="x",
        confidence=1.0, source=BlockSource.DOCLING.value,
    )
    crit = Criterion.objects.create(
        rfp=rfp, code="C-1", title="t", requirement_text="r",
        type=CriterionType.FINANCIAL.value, mandatory=True,
    )
    fact = Fact.objects.create(criterion=crit, bidder_code="A", value="x")
    fact.evidence_blocks.add(block)

    # Same SHA, different file content on disk — but the SHA match is what
    # ingest_bundle uses to identify the existing Document.
    fake_pdf = tmp_path / "seed.pdf"
    fake_pdf.write_bytes(b"unused")

    # Patch the SHA256 helper to return the existing sha so we exercise the
    # --force collision path without actually parsing a real PDF.
    import core.management.commands.ingest_bundle as ib

    def _fake_sha(_p):
        return "da" * 32

    original = ib._sha256
    ib._sha256 = _fake_sha
    try:
        with pytest.raises(CommandError) as exc:
            call_command("ingest_bundle", str(fake_pdf), "--force")
        msg = str(exc.value)
        assert "Re-ingest blocked" in msg
        assert "1 Fact" in msg
        assert "force-cascade" in msg
    finally:
        ib._sha256 = original


def test_ingest_bundle_force_cascade_requires_force(db, tmp_path):
    """--force-cascade alone (without --force) must error out clearly."""
    pdf = tmp_path / "any.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    with pytest.raises(CommandError, match="--force-cascade requires --force"):
        call_command("ingest_bundle", str(pdf), "--force-cascade")


# ---------------------------------------------------------------------------
# T1.3 — citation_warnings in evidence.json payload
# ---------------------------------------------------------------------------

def test_evidence_payload_includes_citation_warnings_field(auth_client, db):
    """Every happy-path evidence.json response must carry the
    `citation_warnings: []` key (so the front-end can branch on it)."""
    rfp = Document.objects.create(
        type=DocumentType.RFP, sha256="cw" * 32, original_filename="cw.pdf",
    )
    crit = Criterion.objects.create(
        rfp=rfp, code="C-1", title="t", requirement_text="r",
        type=CriterionType.FINANCIAL.value, mandatory=True,
    )
    v = Verdict.objects.create(
        criterion=crit, bidder_code="A",
        status=VerdictStatus.PASS.value, rule_id="x", reason="ok",
    )
    resp = auth_client.get(reverse("officer:verdict_evidence_json", args=[v.id]))
    assert resp.status_code == 200
    payload = resp.json()
    assert "citation_warnings" in payload
    assert payload["citation_warnings"] == []


# ---------------------------------------------------------------------------
# T2.2 — CriterionInlineEditForm + criterion_inline_edit endpoint
# ---------------------------------------------------------------------------

def test_inline_edit_form_rejects_unknown_field():
    f = CriterionInlineEditForm({"field": "code", "value": "C-99"})
    assert not f.is_valid()
    assert "field" in f.errors


def test_inline_edit_form_normalises_mandatory_to_bool():
    for raw, expected in [("true", True), ("false", False), ("1", True), ("0", False)]:
        f = CriterionInlineEditForm({"field": "mandatory", "value": raw})
        assert f.is_valid(), f.errors
        assert f.cleaned_data["value"] is expected


def test_inline_edit_form_rejects_empty_title():
    f = CriterionInlineEditForm({"field": "title", "value": "   "})
    assert not f.is_valid()


def test_criterion_inline_edit_toggles_mandatory(auth_client, db):
    rfp = Document.objects.create(
        type=DocumentType.RFP, sha256="ie" * 32, original_filename="ie.pdf",
    )
    c = Criterion.objects.create(
        rfp=rfp, code="C-1", title="t", requirement_text="r",
        type=CriterionType.FINANCIAL.value, mandatory=True,
    )
    resp = auth_client.post(
        reverse("officer:criterion_inline_edit", args=[c.id]),
        {"field": "mandatory", "value": "false"},
    )
    assert resp.status_code == 200
    c.refresh_from_db()
    assert c.mandatory is False
    # Returned partial must contain the row id so HTMX can swap.
    assert f'id="criterion-{c.id}"'.encode() in resp.content


def test_criterion_inline_edit_invalid_field_returns_partial_with_error(auth_client, db):
    rfp = Document.objects.create(
        type=DocumentType.RFP, sha256="ie2" * 22, original_filename="ie2.pdf",
    )
    c = Criterion.objects.create(
        rfp=rfp, code="C-1", title="t", requirement_text="r",
        type=CriterionType.FINANCIAL.value, mandatory=True,
    )
    resp = auth_client.post(
        reverse("officer:criterion_inline_edit", args=[c.id]),
        {"field": "code", "value": "C-99"},  # not whitelisted
    )
    assert resp.status_code == 200  # partial, not 400 — HTMX still swaps
    assert b"Inline edit rejected" in resp.content
    c.refresh_from_db()
    assert c.code == "C-1"  # unchanged


# ---------------------------------------------------------------------------
# T2.3 — Sign-off audit timeline
# ---------------------------------------------------------------------------

def test_signoff_renders_recent_audit_timeline(auth_client, db):
    rfp = Document.objects.create(
        type=DocumentType.RFP, sha256="ts" * 32, original_filename="ts.pdf",
    )
    # The Document save above fired an audit signal; ingest a few more
    # entries via Criterion creation to make the timeline non-empty.
    Criterion.objects.create(
        rfp=rfp, code="C-1", title="t", requirement_text="r",
        type=CriterionType.FINANCIAL.value, mandatory=True,
    )
    resp = auth_client.get(reverse("officer:signoff", args=[rfp.id]))
    assert resp.status_code == 200
    body = resp.content.decode()
    # Day-15 motion redesign: section is "Audit chain" with the
    # last-12-entries hashchain SVG visualization rendered above the table.
    assert "Audit chain" in body or "Audit timeline" in body
    # At least the document.* event from the RFP save should be there.
    assert "document" in body.lower()


# ---------------------------------------------------------------------------
# T2.4 — Empty rule_id banner
# ---------------------------------------------------------------------------

def test_verdict_detail_warns_on_empty_rule_id(auth_client, db):
    rfp = Document.objects.create(
        type=DocumentType.RFP, sha256="er" * 32, original_filename="er.pdf",
    )
    crit = Criterion.objects.create(
        rfp=rfp, code="C-1", title="t", requirement_text="r",
        type=CriterionType.FINANCIAL.value, mandatory=True,
    )
    v = Verdict.objects.create(
        criterion=crit, bidder_code="A",
        status=VerdictStatus.PASS.value,
        rule_id="",  # the bug we're guarding
        reason="malformed-rule path",
    )
    resp = auth_client.get(reverse("officer:verdict_detail", args=[v.id]))
    assert resp.status_code == 200
    # Bharat-Ledger redesign: banner copy is "Rule identifier missing"
    body_lower = resp.content.lower()
    assert b"rule identifier missing" in body_lower or b"rule_id missing" in body_lower
    assert b"override" in body_lower


# ---------------------------------------------------------------------------
# Navigation chrome (sidebar was removed in the full-width refactor —
# nav is now via masthead wordmark + per-page crumbs + Cmd-K palette)
# ---------------------------------------------------------------------------

def test_authenticated_pages_have_navigation_chrome(auth_client, db):
    rfp = Document.objects.create(
        type=DocumentType.RFP, sha256="sb" * 32, original_filename="sb.pdf",
    )
    resp = auth_client.get(reverse("officer:criteria_review", args=[rfp.id]))
    assert resp.status_code == 200
    body = resp.content.decode()
    # Masthead wordmark links home
    assert "wordmark" in body
    # Cmd-K hint is present
    assert "cmdk-hint" in body or "ninja-keys" in body
    # Per-page crumb shows where you are
    assert "crumb" in body
    # No persistent sidebar — explicitly removed in the full-width refactor
    assert "docket-step" not in body
    assert "Docket · Pipeline" not in body


def test_login_page_has_no_sidebar_artifacts(db):
    """The login page never had auth chrome; it still doesn't."""
    resp = Client().get("/accounts/login/")
    assert resp.status_code == 200
    assert b"docket-step" not in resp.content
    assert b"Docket" not in resp.content


# ---------------------------------------------------------------------------
# bootstrap_demo (T1.5) — smoke that the command exists with expected flags
# ---------------------------------------------------------------------------

def test_bootstrap_demo_command_has_skip_flags(db):
    """Smoke that the bootstrap_demo command exists and exposes the
    documented flags. We inspect argparse directly rather than --help to
    avoid stdout-stream redirection quirks under pytest."""
    from core.management.commands.bootstrap_demo import Command

    parser = Command().create_parser("manage.py", "bootstrap_demo")
    flags = {a.option_strings[0] for a in parser._actions if a.option_strings}
    assert "--skip-synthetic" in flags
    assert "--skip-extract" in flags
    assert "--skip-evaluate" in flags
