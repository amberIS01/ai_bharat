"""Day-15 fix #9 — template-comment leak regression test.

Two times now (Day 10 sidebar, Day 14 redesign), I've shipped a
multi-line `{# … #}` Django comment that gets rendered as literal
text on every page that extends `base.html`. `{# #}` is single-line
only; multi-line versions need `{% comment %}…{% endcomment %}`.

This test catches the bug at CI time by:
  1. Logging in as the demo officer
  2. Hitting every authenticated officer URL
  3. Asserting the rendered HTML never contains an unparsed
     `{#`, `#}`, `{% comment`, or `{% endcomment %}` marker

If a future contributor adds a multi-line `{# … #}` comment, this
test fails immediately on CI rather than 13 days into the build
when the user opens it in a browser.

The test also asserts that no `{{ var }}` interpolation token leaks
through unrendered, which guards against more obscure template-syntax
mistakes.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
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
    Verdict,
    VerdictStatus,
)


# Tokens that must never appear in rendered HTML output
UNPARSED_TOKENS = (
    "{#",          # opening single-line comment
    "#}",          # closing single-line comment
    "{% comment",  # opening block comment (unrendered)
    "{% endcomment",  # closing block comment (unrendered)
    "{{ ",         # variable interpolation (unrendered)
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
    return c


@pytest.fixture
def seeded_db(db):
    """Minimal DB so every officer URL has something to render."""
    rfp = Document.objects.create(
        type=DocumentType.RFP, sha256="ts" * 32, original_filename="ts.pdf",
    )
    block = Block.objects.create(
        document=rfp, block_index=0, page_no=1,
        bbox_l=0, bbox_t=0, bbox_r=10, bbox_b=10,
        coord_origin=CoordOrigin.BOTTOMLEFT.value, text="x",
        confidence=1.0, source=BlockSource.DOCLING.value,
    )
    crit = Criterion.objects.create(
        rfp=rfp, code="C-1", title="Min Annual Turnover",
        requirement_text="r", type=CriterionType.FINANCIAL.value, mandatory=True,
        source_clause_block=block,
    )
    v = Verdict.objects.create(
        criterion=crit, bidder_code="A",
        status=VerdictStatus.PASS.value, rule_id="c1.pass.x", reason="ok",
    )
    return rfp, crit, v


# ---------------------------------------------------------------------------
# The actual smoke
# ---------------------------------------------------------------------------

def test_dashboard_has_no_unparsed_template_markers(auth_client, seeded_db):
    resp = auth_client.get(reverse("officer:dashboard"))
    assert resp.status_code == 200
    body = resp.content.decode("utf-8", errors="replace")
    for tok in UNPARSED_TOKENS:
        assert tok not in body, (
            f"Unparsed template marker {tok!r} found in dashboard. "
            f"Likely a multi-line `{{# #}}` comment — switch to "
            f"`{{% comment %}}…{{% endcomment %}}`."
        )


def test_upload_tender_get_has_no_unparsed_markers(auth_client):
    resp = auth_client.get(reverse("officer:upload_tender"))
    assert resp.status_code == 200
    body = resp.content.decode("utf-8", errors="replace")
    for tok in UNPARSED_TOKENS:
        assert tok not in body, f"Unparsed marker {tok!r} in upload_tender"


def test_upload_bidder_get_has_no_unparsed_markers(auth_client, seeded_db):
    rfp, _, _ = seeded_db
    resp = auth_client.get(reverse("officer:upload_bidder", args=[rfp.id]))
    assert resp.status_code == 200
    body = resp.content.decode("utf-8", errors="replace")
    for tok in UNPARSED_TOKENS:
        assert tok not in body, f"Unparsed marker {tok!r} in upload_bidder"


def test_criteria_review_has_no_unparsed_markers(auth_client, seeded_db):
    rfp, _, _ = seeded_db
    resp = auth_client.get(reverse("officer:criteria_review", args=[rfp.id]))
    assert resp.status_code == 200
    body = resp.content.decode("utf-8", errors="replace")
    for tok in UNPARSED_TOKENS:
        assert tok not in body, f"Unparsed marker {tok!r} in criteria_review"


def test_eval_grid_has_no_unparsed_markers(auth_client, seeded_db):
    rfp, _, _ = seeded_db
    resp = auth_client.get(reverse("officer:eval_grid", args=[rfp.id]))
    assert resp.status_code == 200
    body = resp.content.decode("utf-8", errors="replace")
    for tok in UNPARSED_TOKENS:
        assert tok not in body, f"Unparsed marker {tok!r} in eval_grid"


def test_verdict_detail_has_no_unparsed_markers(auth_client, seeded_db):
    rfp, crit, v = seeded_db
    resp = auth_client.get(reverse("officer:verdict_detail", args=[v.id]))
    assert resp.status_code == 200
    body = resp.content.decode("utf-8", errors="replace")
    for tok in UNPARSED_TOKENS:
        assert tok not in body, f"Unparsed marker {tok!r} in verdict_detail"


def test_signoff_has_no_unparsed_markers(auth_client, seeded_db):
    rfp, _, _ = seeded_db
    resp = auth_client.get(reverse("officer:signoff", args=[rfp.id]))
    assert resp.status_code == 200
    body = resp.content.decode("utf-8", errors="replace")
    for tok in UNPARSED_TOKENS:
        assert tok not in body, f"Unparsed marker {tok!r} in signoff"


def test_audit_status_partial_has_no_unparsed_markers(auth_client):
    resp = auth_client.get(
        reverse("officer:audit_status"),
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    body = resp.content.decode("utf-8", errors="replace")
    for tok in UNPARSED_TOKENS:
        assert tok not in body, f"Unparsed marker {tok!r} in audit_status partial"


# ---------------------------------------------------------------------------
# Belt-and-suspenders: walk the template tree and load every template via
# Django's template loader, asserting it parses cleanly. This catches the
# class of bug where a syntax error would fall through to a 500 only on
# certain code paths.
# ---------------------------------------------------------------------------

def test_every_officer_template_parses_cleanly():
    from django.template.loader import get_template
    from django.template import TemplateSyntaxError

    template_dir = Path("officer/templates/officer")
    template_files = list(template_dir.rglob("*.html"))
    assert template_files, "Found no officer templates — fixture broken."

    for tf in template_files:
        # Compute the loader name relative to officer/templates
        relative = tf.relative_to("officer/templates")
        try:
            get_template(str(relative))
        except TemplateSyntaxError as e:
            pytest.fail(f"Template {relative} fails to parse: {e}")
