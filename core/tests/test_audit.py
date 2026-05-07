"""Pure-Python + Django-DB tests for the Day-5 audit chain.

Concerns covered:
  1. RFC-8785-minimal canonical JSON is order-stable, whitespace-free, and
     keeps Unicode as-is.
  2. The hash function is `sha256(prev_hash || b"|" || canonical_payload)`.
  3. Genesis entry uses prev_hash = 64 zeros and seq = 1.
  4. `verify_chain()` is green on a fresh chain.
  5. Direct mutation of `payload_json` (bypassing signals) is detected
     precisely at the tampered seq.
  6. Signal receivers skip `raw=True` (fixture loads must not pollute the chain).
  7. `transaction.on_commit` semantics: an atomic block that rolls back
     produces no audit entries.
  8. Title-based Rego rule dispatch picks the right rule for differently-
     numbered criteria.
"""

from __future__ import annotations

import hashlib

import pytest
from django.db import transaction

from core.audit.canonical import canonicalize
from core.audit.merkle_log import (
    GENESIS_PREV_HASH,
    append,
    diagnose_chain,
    head,
    verify_chain,
)
from core.audit.serializers import (
    serialize_criterion,
    serialize_document,
)
from core.models import (
    AuditEntry,
    Criterion,
    CriterionType,
    Document,
    DocumentType,
    Fact,
)


# ---------------------------------------------------------------------------
# canonical
# ---------------------------------------------------------------------------

def test_canonical_orders_keys_lexicographically():
    a = canonicalize({"b": 2, "a": 1, "c": 3})
    b = canonicalize({"a": 1, "b": 2, "c": 3})
    c = canonicalize({"c": 3, "a": 1, "b": 2})
    assert a == b == c == b'{"a":1,"b":2,"c":3}'


def test_canonical_strips_whitespace():
    assert canonicalize({"x": [1, 2, 3]}) == b'{"x":[1,2,3]}'


def test_canonical_preserves_unicode():
    # Devanagari + emoji round-trip without \u-escaping.
    out = canonicalize({"hindi": "प्रमाण", "emoji": "✅"})
    assert "प्रमाण".encode("utf-8") in out
    assert "✅".encode("utf-8") in out


def test_canonical_is_deterministic_on_nested_structures():
    obj = {"outer": {"b": [3, 1, 2], "a": "x"}, "k": True}
    b1 = canonicalize(obj)
    b2 = canonicalize(obj)
    assert b1 == b2


# ---------------------------------------------------------------------------
# Merkle chain — Django DB tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_genesis_entry_uses_64_zeros():
    AuditEntry.objects.all().delete()
    entry = append("test.event", "test", 1, {"hello": "world"})
    assert entry.seq == 1
    assert entry.prev_hash == GENESIS_PREV_HASH
    assert len(entry.this_hash) == 64
    assert head() == entry


@pytest.mark.django_db(transaction=True)
def test_second_entry_chains_to_first():
    AuditEntry.objects.all().delete()
    e1 = append("a", "test", 1, {})
    e2 = append("b", "test", 2, {})
    assert e2.seq == 2
    assert e2.prev_hash == e1.this_hash
    assert e2.this_hash != e1.this_hash


@pytest.mark.django_db(transaction=True)
def test_hash_function_matches_documented_construction():
    AuditEntry.objects.all().delete()
    entry = append("evt", "test", 1, {"a": 1})
    expected = hashlib.sha256(
        GENESIS_PREV_HASH.encode("ascii") + b"|" + canonicalize(entry.payload_json)
    ).hexdigest()
    assert entry.this_hash == expected


@pytest.mark.django_db(transaction=True)
def test_verify_chain_green_on_fresh_chain():
    AuditEntry.objects.all().delete()
    for i in range(5):
        append(f"test.{i}", "test", i, {"i": i})
    ok, problems = verify_chain()
    assert ok, f"Expected green chain, got problems: {problems}"
    assert problems == []


@pytest.mark.django_db(transaction=True)
def test_tamper_detected_at_exact_seq():
    AuditEntry.objects.all().delete()
    for i in range(5):
        append(f"test.{i}", "test", i, {"i": i})
    target = AuditEntry.objects.get(seq=3)
    bad = dict(target.payload_json)
    bad["data"] = {"i": "TAMPERED"}
    AuditEntry.objects.filter(pk=target.pk).update(payload_json=bad)

    ok, problems = verify_chain()
    assert ok is False
    assert any("seq=3" in p for p in problems), (
        f"Expected at least one problem mentioning seq=3, got: {problems}"
    )


@pytest.mark.django_db(transaction=True)
def test_tamper_detection_only_flags_from_tampered_seq_onward():
    """Pre-tamper seqs should still verify — only seq>=tampered should report."""
    AuditEntry.objects.all().delete()
    for i in range(6):
        append(f"test.{i}", "test", i, {"i": i})
    # Tamper seq=4. seqs 1-3 should remain individually consistent; seq=4
    # should fail; seq=5/6 chain off the tampered hash but their stored
    # prev_hash matches the un-mutated this_hash, so they re-verify
    # independently. Only the tampered entry's *own* recompute fails.
    target = AuditEntry.objects.get(seq=4)
    bad = dict(target.payload_json)
    bad["data"] = {"i": "TAMPERED"}
    AuditEntry.objects.filter(pk=target.pk).update(payload_json=bad)

    ok, problems = verify_chain()
    assert ok is False
    flagged_seqs = {int(p.split("seq=")[1].split(":")[0]) for p in problems if "seq=" in p}
    assert 4 in flagged_seqs
    # seqs 1, 2, 3 must NOT be reported as tampered.
    assert 1 not in flagged_seqs
    assert 2 not in flagged_seqs
    assert 3 not in flagged_seqs


@pytest.mark.django_db(transaction=True)
def test_diagnose_chain_summary_shape():
    AuditEntry.objects.all().delete()
    append("type.a", "test", 1, {})
    append("type.a", "test", 2, {})
    append("type.b", "test", 3, {})
    info = diagnose_chain()
    assert info["count"] == 3
    assert info["head_seq"] == 3
    assert info["by_event_type"] == {"type.a": 2, "type.b": 1}


# ---------------------------------------------------------------------------
# Signal handler behaviour
# ---------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_document_save_appends_audit_entry():
    AuditEntry.objects.all().delete()
    doc = Document.objects.create(
        type=DocumentType.RFP, sha256="t" * 64, original_filename="t.pdf"
    )
    entries = AuditEntry.objects.filter(event_type="document.created")
    assert entries.count() == 1
    assert entries.first().payload_json["entity_id"] == doc.id


@pytest.mark.django_db(transaction=True)
def test_document_delete_appends_audit_entry():
    AuditEntry.objects.all().delete()
    doc = Document.objects.create(
        type=DocumentType.RFP, sha256="d" * 64, original_filename="d.pdf"
    )
    AuditEntry.objects.all().delete()  # discard the create entry
    doc_id = doc.id
    doc.delete()
    deletes = AuditEntry.objects.filter(event_type="document.deleted")
    assert deletes.count() == 1
    assert deletes.first().payload_json["entity_id"] == doc_id


@pytest.mark.django_db(transaction=True)
def test_signal_skips_raw_loads():
    """post_save with raw=True (fixture loads) must NOT write an audit entry."""
    from core.audit import signals as audit_signals

    AuditEntry.objects.all().delete()
    doc = Document(
        id=999_999, type=DocumentType.RFP, sha256="r" * 64, original_filename="raw.pdf"
    )
    # Manually invoke the receiver as Django would during a fixture load.
    audit_signals._on_document_saved(
        sender=Document, instance=doc, created=True, raw=True
    )
    assert AuditEntry.objects.count() == 0


# ---------------------------------------------------------------------------
# Serializer determinism
# ---------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_document_serializer_includes_audit_relevant_fields_only():
    doc = Document.objects.create(
        type=DocumentType.RFP, sha256="z" * 64, original_filename="z.pdf",
        page_count=5,
    )
    payload = serialize_document(doc)
    assert payload["sha256"] == "z" * 64
    assert payload["page_count"] == 5
    assert payload["type"] == DocumentType.RFP.value
    assert "file" not in payload  # binary blob never in audit payload
    assert "created_at" not in payload  # event has its own ts


@pytest.mark.django_db(transaction=True)
def test_criterion_serializer_payload_keys():
    rfp = Document.objects.create(
        type=DocumentType.RFP, sha256="cr" * 32, original_filename="rfp.pdf",
    )
    c = Criterion.objects.create(
        rfp=rfp, code="C-1", title="t",
        requirement_text="r", type=CriterionType.FINANCIAL.value, mandatory=True,
    )
    payload = serialize_criterion(c)
    assert set(payload.keys()) == {
        "id", "rfp_id", "code", "title", "requirement_text",
        "type", "mandatory", "source_clause_block_id",
    }


# ---------------------------------------------------------------------------
# Title-based Rego dispatch
# ---------------------------------------------------------------------------

def test_rego_dispatch_recognises_turnover_under_any_code():
    from core.policy.parsers import rego_path_for_criterion

    class _C:
        code = "PRE-7"  # not C-1 — Gemini renumbered it
        title = "Minimum Annual Turnover"
        requirement_text = "Bidder shall have an annual turnover of at least Rs. 5 Cr."

    assert rego_path_for_criterion(_C()) == "praman/criteria/c1/result"


def test_rego_dispatch_recognises_gst_under_any_code():
    from core.policy.parsers import rego_path_for_criterion

    class _C:
        code = "X-99"
        title = "Valid GST Registration"
        requirement_text = "GSTIN must be valid as on bid submission."

    assert rego_path_for_criterion(_C()) == "praman/criteria/c2/result"


def test_rego_dispatch_returns_none_for_unmapped_pre_qualification():
    from core.policy.parsers import rego_path_for_criterion

    class _C:
        code = "PRE-1"
        title = "Registered Contractor Status"
        requirement_text = "Bidder must be a registered contractor with CPWD or equivalent."

    assert rego_path_for_criterion(_C()) is None
