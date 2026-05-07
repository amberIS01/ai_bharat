"""Day-8 admin tests.

Smoke-cover every registered ModelAdmin via Django's test Client:

  * Admin login + index render.
  * Each model's changelist renders 200.
  * AuditEntry admin enforces read-only (add/change/delete forbidden).
  * Custom admin actions exist on the right models.

The full "click the action button" path is harder to unit-test (admin
actions require a POST with a properly-named action + selected_action[]
form field) — we test that path for the AuditEntry "Verify chain" action
to make sure it doesn't 500 on an empty chain.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from core.models import (
    AuditEntry,
    Block,
    Criterion,
    Document,
    DocumentType,
    Fact,
    GeminiCallCache,
    OverrideRequest,
    Verdict,
)


@pytest.fixture
def admin_client(db):
    User = get_user_model()
    user = User.objects.create_superuser(
        username="admin_test", email="a@example.com", password="pwd123",
    )
    c = Client()
    c.force_login(user)
    return c


# ---------------------------------------------------------------------------
# Login + index
# ---------------------------------------------------------------------------

def test_admin_login_required(db):
    resp = Client().get("/admin/")
    assert resp.status_code == 302
    assert "/login/" in resp.url.lower()


def test_admin_index_renders_for_superuser(admin_client):
    resp = admin_client.get("/admin/")
    assert resp.status_code == 200
    body = resp.content.decode()
    # Branding from admin.site.site_header
    assert "Praman" in body
    # All 8 model groups must show in the admin index when logged in.
    for label in [
        "Documents", "Blocks", "Criteria", "Facts", "Verdicts",
        "Override requests", "Audit entries", "Gemini call caches",
    ]:
        assert label in body, f"Admin index missing {label!r}"


# ---------------------------------------------------------------------------
# Each model's changelist renders
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("model_url", [
    "core_document_changelist",
    "core_block_changelist",
    "core_criterion_changelist",
    "core_fact_changelist",
    "core_verdict_changelist",
    "core_overriderequest_changelist",
    "core_auditentry_changelist",
    "core_geminicallcache_changelist",
])
def test_admin_changelist_renders(admin_client, model_url):
    url = reverse(f"admin:{model_url}")
    resp = admin_client.get(url)
    assert resp.status_code == 200, f"{url} returned {resp.status_code}"


# ---------------------------------------------------------------------------
# AuditEntry: hostile-edit lockdown
# ---------------------------------------------------------------------------

def test_audit_entry_admin_blocks_add(admin_client):
    """The admin must not show an 'Add' button for AuditEntry."""
    resp = admin_client.get(reverse("admin:core_auditentry_changelist"))
    assert resp.status_code == 200
    body = resp.content.decode()
    # Django's "Add" button text in EN locale.
    assert "Add audit entry" not in body, (
        "AuditEntry admin must NOT show an Add button — the chain is append-only "
        "via merkle_log.append() only."
    )


def test_audit_entry_admin_add_url_404s(admin_client):
    resp = admin_client.get(reverse("admin:core_auditentry_add"))
    # has_add_permission returns False → 403.
    assert resp.status_code == 403


def test_audit_entry_admin_change_view_is_read_only(admin_client, db):
    """A change-view GET on an AuditEntry must render but be view-only."""
    from core.audit.merkle_log import append
    AuditEntry.objects.all().delete()
    entry = append("test", "test", 1, {"x": 1})
    resp = admin_client.get(
        reverse("admin:core_auditentry_change", args=[entry.id])
    )
    # Django renders a "view-only" form when has_change_permission=False.
    # Accept either denial (302/403) or an HTML form with NO submit row.
    assert resp.status_code in (200, 302, 403)
    if resp.status_code == 200:
        body = resp.content.decode()
        # The standard "Save" buttons must NOT be in a view-only render.
        assert 'name="_save"' not in body, (
            "AuditEntry admin must not show Save buttons — chain is append-only."
        )


# ---------------------------------------------------------------------------
# Block: also locked down
# ---------------------------------------------------------------------------

def test_block_admin_add_blocked(admin_client):
    resp = admin_client.get(reverse("admin:core_block_add"))
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Verdict: not editable, but viewable
# ---------------------------------------------------------------------------

def test_verdict_admin_add_blocked(admin_client):
    resp = admin_client.get(reverse("admin:core_verdict_add"))
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# OverrideRequest: actions exist
# ---------------------------------------------------------------------------

def test_override_admin_lists_approve_reject_actions(admin_client, db):
    """Action menu only renders when the changelist has at least one row.
    Create a minimal OverrideRequest fixture so the actions become visible."""
    from core.models import (
        Criterion, CriterionType, Document, DocumentType,
        OverrideRequest, Verdict, VerdictStatus, OverrideStatus,
    )
    rfp = Document.objects.create(
        type=DocumentType.RFP, sha256="r" * 64, original_filename="r.pdf",
    )
    crit = Criterion.objects.create(
        rfp=rfp, code="C-1", title="t", requirement_text="r",
        type=CriterionType.FINANCIAL.value, mandatory=True,
    )
    v = Verdict.objects.create(
        criterion=crit, bidder_code="A",
        status=VerdictStatus.FAIL.value, rule_id="x", reason="x",
    )
    OverrideRequest.objects.create(
        verdict=v, requested_status=VerdictStatus.PASS.value,
        reason="x" * 30, status=OverrideStatus.PENDING.value,
    )
    resp = admin_client.get(reverse("admin:core_overriderequest_changelist"))
    assert resp.status_code == 200
    body = resp.content.decode()
    # Action labels live inside the admin's actions <select>
    assert "Approve selected override requests" in body
    assert "Reject selected override requests" in body


# ---------------------------------------------------------------------------
# AuditEntry: "Verify chain" action exists and runs
# ---------------------------------------------------------------------------

def test_audit_admin_verify_chain_action_does_not_500(admin_client, db):
    """POST the verify_chain_action with an empty chain — must redirect (302),
    not raise."""
    AuditEntry.objects.all().delete()
    resp = admin_client.post(
        reverse("admin:core_auditentry_changelist"),
        {
            "action": "verify_chain_action",
            # Even though the action ignores selection, Django requires
            # _selected_action to be present unless action-acts-on-all.
            "_selected_action": [],
            "select_across": "1",
            "index": "0",
        },
    )
    # The action runs and redirects back to the changelist.
    assert resp.status_code in (200, 302)


# ---------------------------------------------------------------------------
# GeminiCallCache: clear-cache action exists
# ---------------------------------------------------------------------------

def test_gemini_cache_admin_clear_action_exists(admin_client, db):
    """Same caveat as the override-actions test — needs at least one row to
    render the action menu. The action also lives on a view-only admin
    (has_change_permission=False), so we verify it's exposed via the
    `actions` attribute on the registered admin class."""
    from django.contrib.admin.sites import site
    GeminiCallCache.objects.create(
        prompt_sha256="p" * 64, image_sha256="i" * 64,
        model="gemini-2.5-flash", response_json={"x": 1},
    )
    admin_cls = site._registry[GeminiCallCache]
    assert "clear_all_cache_entries" in (admin_cls.actions or [])
    # And the changelist still renders.
    resp = admin_client.get(reverse("admin:core_geminicallcache_changelist"))
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Document admin filter / search
# ---------------------------------------------------------------------------

def test_document_admin_search_finds_match(admin_client, db):
    Document.objects.create(
        type=DocumentType.RFP, sha256="searchme" * 8,
        original_filename="findable.pdf",
    )
    resp = admin_client.get(
        reverse("admin:core_document_changelist") + "?q=findable",
    )
    assert resp.status_code == 200
    assert b"findable.pdf" in resp.content


# ---------------------------------------------------------------------------
# Branding
# ---------------------------------------------------------------------------

def test_admin_site_branding_is_praman(admin_client):
    resp = admin_client.get("/admin/")
    body = resp.content.decode()
    assert "Praman — Tender Evaluation (Admin)" in body
    assert "Praman back-office" in body
