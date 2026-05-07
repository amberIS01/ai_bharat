"""Day-6 tests — cert generation idempotency, PDF builder context shape,
signer wiring, end-to-end signature verification on a generated PDF.

The end-to-end test is marked `slow` because it spawns WeasyPrint + pyHanko;
skip with `pytest -m "not slow"` for fast pre-commit runs.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from unittest import mock

import pytest

from core.audit.cert_gen import (
    DEFAULT_PASSWORD,
    generate_demo_signing_cert,
    load_demo_cert,
)


# ---------------------------------------------------------------------------
# cert_gen
# ---------------------------------------------------------------------------

def test_cert_gen_creates_pkcs12(tmp_path):
    p12 = tmp_path / "demo.p12"
    out = generate_demo_signing_cert(p12, password=b"unit_test_pwd")
    assert out == p12
    assert p12.exists()
    assert p12.stat().st_size > 1500  # rough lower bound for RSA-2048 + cert


def test_cert_gen_is_idempotent(tmp_path):
    p12 = tmp_path / "demo.p12"
    generate_demo_signing_cert(p12, password=b"x" * 16)
    bytes_first = p12.read_bytes()
    # Second call must NOT overwrite the existing file.
    generate_demo_signing_cert(p12, password=b"DIFFERENT_PWD_BYTES")
    bytes_second = p12.read_bytes()
    assert bytes_first == bytes_second


def test_cert_gen_subject_marks_demo_use(tmp_path):
    p12 = tmp_path / "demo.p12"
    generate_demo_signing_cert(p12, password=b"unit_test_pwd")
    _, cert, _ = load_demo_cert(p12, password=b"unit_test_pwd")
    rfc4514 = cert.subject.rfc4514_string()
    assert "Praman Demo Officer" in rfc4514
    assert "DEMO USE ONLY" in rfc4514
    assert ",C=IN" in rfc4514


def test_cert_gen_validity_in_the_future(tmp_path):
    p12 = tmp_path / "demo.p12"
    generate_demo_signing_cert(p12, password=b"unit_test_pwd", validity_days=10)
    _, cert, _ = load_demo_cert(p12, password=b"unit_test_pwd")
    now = dt.datetime.now(dt.timezone.utc)
    assert cert.not_valid_before_utc <= now
    assert cert.not_valid_after_utc > now
    # 10-day validity window
    delta = cert.not_valid_after_utc - cert.not_valid_before_utc
    assert 9 <= delta.days <= 11


def test_cert_gen_basic_constraints_not_a_ca(tmp_path):
    """Demo signer must not look like a CA cert."""
    from cryptography import x509
    p12 = tmp_path / "demo.p12"
    generate_demo_signing_cert(p12, password=b"unit_test_pwd")
    _, cert, _ = load_demo_cert(p12, password=b"unit_test_pwd")
    bc = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
    assert bc.ca is False


# ---------------------------------------------------------------------------
# PDF context shape
# ---------------------------------------------------------------------------

def test_bindings_pretty_handles_none_and_empty():
    from core.audit.pdf_export import _bindings_pretty
    assert _bindings_pretty(None) == ""
    assert _bindings_pretty({}) == ""


def test_bindings_pretty_sorts_keys():
    from core.audit.pdf_export import _bindings_pretty
    out = _bindings_pretty({"b": 2, "a": 1})
    # First non-whitespace key in pretty-printed JSON should be "a"
    first_quote = out.index('"')
    assert out[first_quote:first_quote + 3] == '"a"'


def test_bindings_pretty_handles_nested():
    from core.audit.pdf_export import _bindings_pretty
    out = _bindings_pretty({"top": {"x": 1, "y": 2}})
    assert '"top"' in out
    assert '"x": 1' in out


# ---------------------------------------------------------------------------
# Signer module — verification helper only (mocking pyHanko itself is heavy)
# ---------------------------------------------------------------------------

def test_signer_constants_match_cert_gen():
    """The signer module's defaults must point at the cert_gen output."""
    from core.audit.cert_gen import DEFAULT_P12_PATH as gen_path
    from core.audit import signer as signer_mod
    # The signer's sign_evidence_pdf defaults to using DEFAULT_P12_PATH.
    src = Path(signer_mod.__file__).read_text()
    assert "DEFAULT_P12_PATH" in src
    assert str(gen_path) == "deploy/demo_signer.p12"


def test_verify_signed_pdf_handles_unsigned_input(tmp_path):
    """A PDF without any embedded signature must surface a clean error."""
    from core.audit.signer import verify_signed_pdf
    # Write a minimal valid PDF skeleton — no signature.
    p = tmp_path / "no_sig.pdf"
    p.write_bytes(
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\n"
        b"xref\n0 3\n0000000000 65535 f \n"
        b"0000000009 00000 n \n0000000052 00000 n \n"
        b"trailer<</Size 3/Root 1 0 R>>\nstartxref\n91\n%%EOF\n"
    )
    status = verify_signed_pdf(p)
    assert status["valid"] is False
    assert status["signature_intact"] is False
    err = status["errors"][0] if status["errors"] else ""
    # Either "No embedded signatures" (well-formed but unsigned) or a parse
    # failure (malformed PDF skeleton). Both must surface as a structured
    # error rather than crashing.
    assert err, "Expected an error message; got empty list"
    assert (
        "No embedded signatures" in err
        or "PDF parse failed" in err
        or "Could not open" in err
    ), f"Unexpected error message: {err!r}"


# ---------------------------------------------------------------------------
# End-to-end (slow) — only if synthetic data + verdicts are present in DB
# ---------------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.django_db(transaction=True)
def test_evidence_pdf_round_trip(tmp_path):
    """Full integration: build → sign → verify → tamper → restore."""
    from core.audit.pdf_export import build_evidence_pdf
    from core.audit.signer import sign_evidence_pdf, verify_signed_pdf
    from core.models import Document, DocumentType, Verdict

    rfp = (
        Document.objects.filter(type=DocumentType.RFP).order_by("-created_at").first()
    )
    if not rfp:
        pytest.skip("No RFP in DB — run ingest_bundle first")
    if not Verdict.objects.exists():
        pytest.skip("No Verdicts in DB — run evaluate_verdicts first")

    unsigned = tmp_path / "unsigned.pdf"
    signed = tmp_path / "signed.pdf"

    build_evidence_pdf(rfp, unsigned)
    assert unsigned.stat().st_size > 5000  # non-trivial output

    sign_evidence_pdf(unsigned, signed)
    assert signed.stat().st_size > unsigned.stat().st_size  # signing adds bytes

    status = verify_signed_pdf(signed)
    assert status["signature_intact"] is True
    assert "Praman Demo Officer" in status["signer_subject"]

    # Tamper
    original = signed.read_bytes()
    mid = len(original) // 2
    tampered = bytearray(original)
    tampered[mid] ^= 0x01
    signed.write_bytes(bytes(tampered))
    assert verify_signed_pdf(signed)["signature_intact"] is False

    # Restore
    signed.write_bytes(original)
    assert verify_signed_pdf(signed)["signature_intact"] is True
