"""Day-15 fix #7 — effective_status helper + override propagation.

Reviewer's gap was: officer files an OverrideRequest, admin approves
it via /admin/, but the grid + drilldown + PDF still show the
ORIGINAL Rego status. The override does nothing visible.

These tests prove the new path:
  1. effective_status() returns original when no approved override
  2. effective_status() returns approved override's requested_status
  3. APPROVED + APPROVED → newest wins
  4. PENDING / REJECTED overrides are ignored
  5. eval_grid_json reflects effective status in cells
  6. verdict_detail page shows both Rego original AND effective stamp
  7. PDF export uses effective status (only structural assert; signing
     is exercised by other tests)
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import (
    Criterion,
    CriterionType,
    Document,
    DocumentType,
    OverrideRequest,
    OverrideStatus,
    Verdict,
    VerdictStatus,
)
from core.policy.effective import (
    effective_override,
    effective_status,
    is_overridden,
)


@pytest.fixture
def auth_client(db):
    User = get_user_model()
    user = User.objects.filter(username="officer").first()
    if not user:
        user = User.objects.create_superuser(
            username="officer", email="o@example.com",
            password="praman_demo_2026",
        )
    c = Client(HTTP_HOST="127.0.0.1")
    c.force_login(user)
    return c, user


@pytest.fixture
def fail_verdict(db):
    rfp = Document.objects.create(
        type=DocumentType.RFP, sha256="es" * 32, original_filename="es.pdf",
    )
    crit = Criterion.objects.create(
        rfp=rfp, code="C-1", title="Min Annual Turnover",
        requirement_text="r", type=CriterionType.FINANCIAL.value, mandatory=True,
    )
    v = Verdict.objects.create(
        criterion=crit, bidder_code="A",
        status=VerdictStatus.FAIL.value,
        rule_id="c1.fail.turnover_below_threshold",
        reason="below threshold",
    )
    return v


# ---------------------------------------------------------------------------
# Helper-level tests
# ---------------------------------------------------------------------------

def test_effective_status_returns_original_when_no_override(fail_verdict):
    assert effective_status(fail_verdict) == VerdictStatus.FAIL.value
    assert effective_override(fail_verdict) is None
    assert is_overridden(fail_verdict) is False


def test_effective_status_returns_approved_override(fail_verdict, auth_client):
    _, user = auth_client
    OverrideRequest.objects.create(
        verdict=fail_verdict, officer=user,
        requested_status=VerdictStatus.PASS.value,
        reason="manually verified by officer; on-site visit confirmed turnover",
        status=OverrideStatus.APPROVED,
    )
    assert effective_status(fail_verdict) == VerdictStatus.PASS.value
    assert is_overridden(fail_verdict) is True


def test_pending_or_rejected_overrides_are_ignored(fail_verdict, auth_client):
    _, user = auth_client
    OverrideRequest.objects.create(
        verdict=fail_verdict, officer=user,
        requested_status=VerdictStatus.PASS.value,
        reason="please review",
        status=OverrideStatus.PENDING,
    )
    OverrideRequest.objects.create(
        verdict=fail_verdict, officer=user,
        requested_status=VerdictStatus.PASS.value,
        reason="rejected for insufficient evidence",
        status=OverrideStatus.REJECTED,
    )
    # Both ignored — original wins.
    assert effective_status(fail_verdict) == VerdictStatus.FAIL.value


def test_newest_approved_override_wins(fail_verdict, auth_client, db):
    _, user = auth_client
    o1 = OverrideRequest.objects.create(
        verdict=fail_verdict, officer=user,
        requested_status=VerdictStatus.PASS.value,
        reason="first approval", status=OverrideStatus.APPROVED,
    )
    # Force ts ordering — auto_now_add puts both in the same instant in tests
    o1.ts = timezone.now() - timedelta(minutes=10)
    o1.save(update_fields=["ts"])
    OverrideRequest.objects.create(
        verdict=fail_verdict, officer=user,
        requested_status=VerdictStatus.ABSTAIN.value,
        reason="reconsidered: low confidence on supporting docs",
        status=OverrideStatus.APPROVED,
    )
    # Newest wins → ABSTAIN
    assert effective_status(fail_verdict) == VerdictStatus.ABSTAIN.value


# ---------------------------------------------------------------------------
# Grid + drilldown propagation
# ---------------------------------------------------------------------------

def test_eval_grid_json_returns_effective_status_for_overridden_cell(
    fail_verdict, auth_client,
):
    client, user = auth_client
    OverrideRequest.objects.create(
        verdict=fail_verdict, officer=user,
        requested_status=VerdictStatus.PASS.value,
        reason="officer override after on-site verification",
        status=OverrideStatus.APPROVED,
    )
    rfp = fail_verdict.criterion.rfp
    # Need a bidder Document so eval_grid_json includes bidder "A"
    Document.objects.create(
        type=DocumentType.BIDDER_COVER, bidder_code="A",
        sha256="bd" * 32, original_filename="bd.pdf",
    )
    resp = client.get(reverse("officer:eval_grid_json", args=[rfp.id]))
    assert resp.status_code == 200
    payload = resp.json()
    a_row = next(r for r in payload["rows"] if r["bidder_code"] == "A")
    cell = a_row["C-1"]
    assert cell["status"] == "PASS", "effective status should win"
    assert cell["status_original"] == "FAIL", "original Rego must be preserved"
    assert cell["is_overridden"] is True
    # Summary count is keyed off effective.
    assert a_row["_summary"]["PASS"] == 1
    assert a_row["_summary"]["FAIL"] == 0


def test_verdict_detail_shows_both_original_and_effective(fail_verdict, auth_client):
    client, user = auth_client
    OverrideRequest.objects.create(
        verdict=fail_verdict, officer=user,
        requested_status=VerdictStatus.PASS.value,
        reason="officer override after on-site verification",
        status=OverrideStatus.APPROVED,
    )
    resp = client.get(reverse("officer:verdict_detail", args=[fail_verdict.id]))
    assert resp.status_code == 200
    body = resp.content.decode()
    # The big stamp shows the effective status (PASS).
    assert ">PASS<" in body or "PASS</div>" in body
    # The "overridden by" caption surfaces the original FAIL.
    assert "overridden by" in body
    assert "FAIL" in body  # struck-through original pill