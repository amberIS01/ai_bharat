"""Officer-app view tests.

Use Django's test Client to smoke-test the URL routing, auth gating, and
form validation. The full upload+ingest path goes through Docling/Gemini
and lives in the slow tests of `core/tests/`; here we cover the cheap
HTTP-level assertions.

Tests should never hit the network. Where a view calls `opa_health_check`
we expect the failure path (OPA isn't running during pytest), and the
template should render anyway with a friendly message.
"""

from __future__ import annotations

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
    Fact,
    Verdict,
    VerdictStatus,
)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@pytest.fixture
def officer(db):
    User = get_user_model()
    return User.objects.create_user(
        username="officer", password="praman_demo_2026", is_staff=True
    )


@pytest.fixture
def auth_client(officer):
    c = Client()
    c.force_login(officer)
    return c


def test_dashboard_requires_login(db):
    c = Client()
    resp = c.get(reverse("officer:dashboard"))
    # @login_required redirects to LOGIN_URL with ?next= the original URL.
    assert resp.status_code == 302
    assert "/accounts/login/" in resp.url


def test_root_redirects_to_officer(db):
    resp = Client().get("/")
    assert resp.status_code == 302
    assert resp.url == "/officer/"


def test_login_page_renders(db):
    resp = Client().get("/accounts/login/")
    assert resp.status_code == 200
    assert b"Sign in" in resp.content


def test_login_page_does_not_leak_default_credentials(db):
    """Day-15 fix — for a CRPF tender system whose entire pitch is
    'evidence-grade, audit-trail, signed by Class-3 DSC', showing
    `officer / praman_demo_2026` on the login page is a credibility
    killer. The credentials live in `docs/QUICKSTART.md` and the
    operator runbook — never on the login screen a juror types into."""
    resp = Client().get("/accounts/login/")
    assert resp.status_code == 200
    assert b"praman_demo_2026" not in resp.content, (
        "Login page must NOT display the demo password — "
        "looks like a hackathon prototype, not a procurement tool."
    )
    # The username "officer" alone is generic enough that it might
    # appear in non-credential contexts; we don't assert against it
    # as long as it's not paired with the password.


# ---------------------------------------------------------------------------
# Dashboard (logged in)
# ---------------------------------------------------------------------------

def test_dashboard_renders_for_authenticated_officer(auth_client):
    resp = auth_client.get(reverse("officer:dashboard"))
    assert resp.status_code == 200
    assert b"Praman" in resp.content
    # Bharat-Ledger redesign copy: dashboard mentions OPA via "Policy engine"
    # and the audit chain via "Audit chain · head".
    assert b"OPA" in resp.content or b"Policy engine" in resp.content
    assert b"Audit chain" in resp.content or b"chain" in resp.content.lower()


def test_dashboard_lists_existing_rfps(auth_client, db):
    Document.objects.create(
        type=DocumentType.RFP, sha256="d1" * 32, original_filename="t1.pdf",
    )
    resp = auth_client.get(reverse("officer:dashboard"))
    assert resp.status_code == 200
    assert b"t1.pdf" in resp.content


# ---------------------------------------------------------------------------
# Upload tender — GET
# ---------------------------------------------------------------------------

def test_upload_tender_get_renders_form(auth_client):
    resp = auth_client.get(reverse("officer:upload_tender"))
    assert resp.status_code == 200
    # Bharat-Ledger redesign copy: "Enter a tender into the register"
    assert b"tender" in resp.content.lower()
    assert b"Tender notice" in resp.content
    assert b'enctype="multipart/form-data"' in resp.content


def test_upload_tender_rejects_non_pdf(auth_client):
    resp = auth_client.post(
        reverse("officer:upload_tender"),
        {"rfp_file": _fake_upload("malicious.exe", b"MZ\x90")},
    )
    # Form invalid → 200 with error message.
    assert resp.status_code == 200
    assert b"not allowed" in resp.content


def test_upload_tender_rejects_oversized(auth_client):
    """Anything bigger than 50 MB must be rejected at the form layer."""
    huge = _fake_upload(
        "big.pdf", b"%PDF-1.4\n" + b"\x00" * (60 * 1024 * 1024),
    )
    resp = auth_client.post(reverse("officer:upload_tender"), {"rfp_file": huge})
    assert resp.status_code == 200
    assert b"per-file cap" in resp.content


# ---------------------------------------------------------------------------
# Upload bidder — GET shows existing bidder codes
# ---------------------------------------------------------------------------

def test_upload_bidder_get_lists_existing_bidders(auth_client, db):
    rfp = Document.objects.create(
        type=DocumentType.RFP, sha256="r" * 64, original_filename="rfp.pdf",
    )
    Document.objects.create(
        type=DocumentType.BIDDER_COVER, bidder_code="A",
        sha256="a" * 64, original_filename="cover_a.pdf",
    )
    Document.objects.create(
        type=DocumentType.BIDDER_COVER, bidder_code="B",
        sha256="b" * 64, original_filename="cover_b.pdf",
    )
    resp = auth_client.get(reverse("officer:upload_bidder", args=[rfp.id]))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Bidder A" in body
    assert "Bidder B" in body


def test_upload_bidder_404_for_non_rfp(auth_client, db):
    not_rfp = Document.objects.create(
        type=DocumentType.BIDDER_COVER, bidder_code="A",
        sha256="x" * 64, original_filename="x.pdf",
    )
    resp = auth_client.get(reverse("officer:upload_bidder", args=[not_rfp.id]))
    assert resp.status_code == 404


def test_upload_bidder_invalid_code_rejected(auth_client, db):
    rfp = Document.objects.create(
        type=DocumentType.RFP, sha256="r2" * 32, original_filename="r2.pdf",
    )
    resp = auth_client.post(
        reverse("officer:upload_bidder", args=[rfp.id]),
        {
            "bidder_code": "ab",  # too long
            "bundle_file": _fake_upload("b.pdf", b"%PDF-1.4\n"),
        },
    )
    assert resp.status_code == 200
    assert b"single uppercase letter" in resp.content


# ---------------------------------------------------------------------------
# Criteria review + eval grid
# ---------------------------------------------------------------------------

def test_criteria_review_renders_with_no_criteria(auth_client, db):
    rfp = Document.objects.create(
        type=DocumentType.RFP, sha256="r3" * 32, original_filename="r3.pdf",
    )
    resp = auth_client.get(reverse("officer:criteria_review", args=[rfp.id]))
    assert resp.status_code == 200
    assert b"No criteria extracted yet" in resp.content


def test_eval_grid_renders_verdict_matrix(auth_client, db):
    """Day-7 assertion was content-based; Day-9 moved bidder/verdict rendering
    into AG-Grid (client-side from /grid.json), so the page itself only
    contains the grid container. Test now: (1) page renders 200 and shows
    the AG-Grid container + criteria title hooks, (2) the JSON endpoint
    returns the populated matrix.
    """
    rfp = Document.objects.create(
        type=DocumentType.RFP, sha256="r4" * 32, original_filename="r4.pdf",
    )
    Document.objects.create(
        type=DocumentType.BIDDER_COVER, bidder_code="A",
        sha256="ba" * 32, original_filename="cover_a.pdf",
    )
    block = Block.objects.create(
        document=rfp, block_index=0, page_no=1,
        bbox_l=0, bbox_t=0, bbox_r=10, bbox_b=10,
        coord_origin=CoordOrigin.BOTTOMLEFT.value, text="t",
        confidence=1.0, source=BlockSource.DOCLING.value,
    )
    crit = Criterion.objects.create(
        rfp=rfp, code="C-1", title="Min turnover",
        requirement_text="Turnover ≥ Rs. 5 Cr",
        type=CriterionType.FINANCIAL.value, mandatory=True,
        source_clause_block=block,
    )
    Fact.objects.create(
        criterion=crit, bidder_code="A", value="Rs. 6,80,00,000/-",
        ocr_confidence=1.0,
    )
    Verdict.objects.create(
        criterion=crit, bidder_code="A",
        status=VerdictStatus.PASS.value,
        rule_id="c1.pass.turnover_meets_threshold",
        bindings_json={"value_inr": 68000000, "threshold_inr": 50000000},
        reason="Annual turnover meets threshold.",
    )

    # 1) The eval-grid page renders 200 and includes the AG-Grid container.
    resp = auth_client.get(reverse("officer:eval_grid", args=[rfp.id]))
    assert resp.status_code == 200
    assert b'id="praman-grid"' in resp.content
    # The legend pill labels are server-rendered, so "PASS" still shows.
    assert b"PASS" in resp.content

    # 2) The grid.json endpoint returns the matrix data the JS consumes.
    json_resp = auth_client.get(reverse("officer:eval_grid_json", args=[rfp.id]))
    assert json_resp.status_code == 200
    payload = json_resp.json()
    assert payload["bidder_codes"] == ["A"]
    a_row = payload["rows"][0]
    assert a_row["bidder_code"] == "A"
    assert a_row["C-1"]["status"] == "PASS"
    assert a_row["C-1"]["rule_id"] == "c1.pass.turnover_meets_threshold"


# ---------------------------------------------------------------------------
# Verdict detail + override
# ---------------------------------------------------------------------------

def test_verdict_detail_renders(auth_client, db):
    rfp = Document.objects.create(
        type=DocumentType.RFP, sha256="r5" * 32, original_filename="r5.pdf",
    )
    crit = Criterion.objects.create(
        rfp=rfp, code="C-1", title="Min turnover",
        requirement_text="r", type=CriterionType.FINANCIAL.value, mandatory=True,
    )
    v = Verdict.objects.create(
        criterion=crit, bidder_code="A",
        status=VerdictStatus.FAIL.value,
        rule_id="c1.fail.turnover_below_threshold",
        bindings_json={"value_inr": 42000000, "threshold_inr": 50000000},
        reason="Turnover too low.",
    )
    resp = auth_client.get(reverse("officer:verdict_detail", args=[v.id]))
    assert resp.status_code == 200
    assert b"FAIL" in resp.content
    assert b"c1.fail.turnover_below_threshold" in resp.content
    # Override form must be present
    assert b"override" in resp.content.lower()


def test_verdict_override_too_short_reason_rejected(auth_client, db):
    rfp = Document.objects.create(
        type=DocumentType.RFP, sha256="r6" * 32, original_filename="r6.pdf",
    )
    crit = Criterion.objects.create(
        rfp=rfp, code="C-1", title="x",
        requirement_text="r", type=CriterionType.FINANCIAL.value, mandatory=True,
    )
    v = Verdict.objects.create(
        criterion=crit, bidder_code="A",
        status=VerdictStatus.FAIL.value, rule_id="x", reason="x",
    )
    resp = auth_client.post(
        reverse("officer:verdict_override", args=[v.id]),
        {"requested_status": "PASS", "reason": "too short"},
    )
    # Redirects back with an error message; the override row must NOT be created.
    assert resp.status_code == 302
    assert v.overrides.count() == 0


# ---------------------------------------------------------------------------
# Sign-off page (without actually running export — that's slow)
# ---------------------------------------------------------------------------

def test_signoff_get_renders_without_existing_pdf(auth_client, db):
    rfp = Document.objects.create(
        type=DocumentType.RFP, sha256="r7" * 32, original_filename="r7.pdf",
    )
    resp = auth_client.get(reverse("officer:signoff", args=[rfp.id]))
    assert resp.status_code == 200
    # Bharat-Ledger redesign: "Sign-off & sealed evidence" / "No sealed PDFs"
    assert b"Sign-off" in resp.content
    assert b"No sealed PDFs" in resp.content or b"No signed PDF" in resp.content


# ---------------------------------------------------------------------------
# Audit status JSON / HTMX fragment
# ---------------------------------------------------------------------------

def test_audit_status_json_for_non_htmx(auth_client):
    resp = auth_client.get(reverse("officer:audit_status"))
    assert resp.status_code == 200
    assert resp["Content-Type"].startswith("application/json")
    data = resp.json()
    assert "count" in data and "verified" in data


def test_audit_status_htmx_returns_html_fragment(auth_client):
    resp = auth_client.get(
        reverse("officer:audit_status"),
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    body = resp.content.decode()
    # Bharat-Ledger redesign: pill copy is "chain · N sealed" / "chain broken"
    assert "chain" in body.lower()
    # Fragment, not full page — must NOT contain <html> tag
    assert "<html" not in body.lower()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _fake_upload(name: str, content: bytes):
    from django.core.files.uploadedfile import SimpleUploadedFile
    return SimpleUploadedFile(name, content, content_type="application/octet-stream")
