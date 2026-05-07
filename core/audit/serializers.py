"""Convert Django model instances into the small, stable dicts we hash.

Every serializer returns a plain dict containing only fields that are part of
the audit story:
    * Identity (PKs, codes, hashes — the join keys an auditor would use).
    * Decision-relevant content (criterion text, fact value, verdict status).
    * Provenance (M2M of cited blocks, sorted for hash stability).

Things deliberately NOT serialized:
    * File blobs / binary content (Document.sha256 is the audit fingerprint).
    * created_at timestamps on the row (the audit entry has its own ts).
    * Foreign-key joined objects beyond their PK — only the PK travels.

These dicts are the `data` field inside the audit-entry payload. The merkle_log
wraps them with seq / event_type / ts / entity for the actual hash input.
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Document
# ---------------------------------------------------------------------------

def serialize_document(doc) -> dict[str, Any]:
    return {
        "id": doc.id,
        "type": doc.type,
        "bidder_code": doc.bidder_code or "",
        "sha256": doc.sha256,
        "original_filename": doc.original_filename or "",
        "page_count": doc.page_count,
    }


# ---------------------------------------------------------------------------
# Criterion
# ---------------------------------------------------------------------------

def serialize_criterion(c) -> dict[str, Any]:
    return {
        "id": c.id,
        "rfp_id": c.rfp_id,
        "code": c.code,
        "title": c.title,
        "requirement_text": c.requirement_text,
        "type": c.type,
        "mandatory": bool(c.mandatory),
        "source_clause_block_id": c.source_clause_block_id,
    }


# ---------------------------------------------------------------------------
# Fact
# ---------------------------------------------------------------------------

def serialize_fact(f) -> dict[str, Any]:
    """`f` should be fetched with `prefetch_related('evidence_blocks')`.

    We sort the evidence-block PK list so two equivalent M2M sets hash to the
    same payload regardless of insert order.
    """
    evidence_ids = sorted(b.id for b in f.evidence_blocks.all())
    return {
        "id": f.id,
        "criterion_id": f.criterion_id,
        "bidder_code": f.bidder_code,
        "value": f.value or "",
        "ocr_confidence": float(f.ocr_confidence),
        "evidence_block_ids": evidence_ids,
    }


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------

def serialize_verdict(v) -> dict[str, Any]:
    """`v` should be fetched with `prefetch_related('evidence_refs')`."""
    evidence_ids = sorted(b.id for b in v.evidence_refs.all())
    return {
        "id": v.id,
        "criterion_id": v.criterion_id,
        "bidder_code": v.bidder_code,
        "status": v.status,
        "rule_id": v.rule_id,
        "bindings": v.bindings_json or {},
        "reason": v.reason or "",
        "evidence_block_ids": evidence_ids,
    }


# ---------------------------------------------------------------------------
# OverrideRequest
# ---------------------------------------------------------------------------

def serialize_override(o) -> dict[str, Any]:
    return {
        "id": o.id,
        "verdict_id": o.verdict_id,
        "officer_id": o.officer_id,
        "requested_status": o.requested_status,
        "reason": o.reason,
        "status": o.status,
    }
