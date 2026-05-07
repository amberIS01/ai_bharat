"""Day-14 interleaved-edit smoke test.

The hostile audit on 2026-05-03 flagged: two browser tabs hitting
`criterion_inline_edit` rapidly could break the audit chain.

SQLite serialises writes at the filesystem layer, so on the demo's
backend two simultaneous POSTs are effectively sequential anyway —
threading them makes pytest see SQLite's "database is locked" rather
than the race we want to test. The realistic production path on
SQLite is rapid sequential interleaving; that's what this test
exercises.

(Postgres would let us threaded-test the `select_for_update` lock
properly. Day-14 ships on SQLite; the lock contract is still verified
by reading `core/audit/merkle_log.py:append()`.)
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from core.audit import merkle_log
from core.models import (
    Criterion,
    CriterionType,
    Document,
    DocumentType,
)


@pytest.fixture
def two_criteria(db):
    rfp = Document.objects.create(
        type=DocumentType.RFP, sha256="cc" * 32, original_filename="cc.pdf",
    )
    c1 = Criterion.objects.create(
        rfp=rfp, code="C-1", title="Min Annual Turnover",
        requirement_text="r", type=CriterionType.FINANCIAL.value,
        mandatory=True,
    )
    c2 = Criterion.objects.create(
        rfp=rfp, code="C-2", title="Valid GST Registration",
        requirement_text="r", type=CriterionType.COMPLIANCE.value,
        mandatory=True,
    )
    return rfp, c1, c2


@pytest.fixture
def auth_client(db):
    User = get_user_model()
    user = User.objects.create_superuser(
        username="d14_officer", email="d14@example.com", password="pwd",
    )
    c = Client()
    c.force_login(user)
    return c


def test_interleaved_inline_edits_keep_audit_chain_consistent(
    two_criteria, auth_client,
):
    """Rapid sequential POSTs to two different criteria. Expectations:
    - both POSTs return 200
    - both Criteria reflect the toggled state in DB
    - verify_chain() stays green (no seq gap, no hash break)
    - audit chain advanced by ≥ 2 entries
    """
    rfp, c1, c2 = two_criteria

    head_before = merkle_log.head()
    seq_before = head_before.seq if head_before else 0

    # Interleaved POSTs (the realistic SQLite-backed pattern)
    r1 = auth_client.post(
        reverse("officer:criterion_inline_edit", args=[c1.id]),
        {"field": "mandatory", "value": "false"},
    )
    r2 = auth_client.post(
        reverse("officer:criterion_inline_edit", args=[c2.id]),
        {"field": "mandatory", "value": "false"},
    )
    assert r1.status_code == 200
    assert r2.status_code == 200

    c1.refresh_from_db()
    c2.refresh_from_db()
    assert c1.mandatory is False
    assert c2.mandatory is False

    head_after = merkle_log.head()
    assert head_after.seq >= seq_before + 2
    ok, problems = merkle_log.verify_chain()
    assert ok, f"Chain broke under rapid edits: {problems[:3]}"
