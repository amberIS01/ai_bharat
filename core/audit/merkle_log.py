"""Praman's append-only Merkle-chained audit log.

Public surface:

    GENESIS_PREV_HASH                       — 64 zero hex chars
    append(event_type, entity, entity_id, data, *, ts=None) -> AuditEntry
    head() -> AuditEntry | None
    verify_chain() -> tuple[bool, list[str]]

Hash construction (per entry):

    payload = {
        "seq":        <int, 1-indexed, monotonically increasing>,
        "ts":         <ISO-8601 string with timezone, microsecond precision>,
        "event_type": <str, e.g. "verdict.emitted">,
        "entity":     <str, e.g. "verdict">,
        "entity_id":  <int|null>,
        "data":       <whitelisted dict from core.audit.serializers>,
    }
    canonical_bytes = canonicalize(payload)         # RFC 8785 minimal
    this_hash       = sha256(prev_hash_hex.encode() + b"|" + canonical_bytes).hexdigest()

The `|` separator is a domain delimiter so a payload that ends with the same
bytes as a previous prev_hash cannot be confused with one of the chain links
(RFC 9162 / Certificate Transparency uses domain bytes for the same reason on
its tree variant).

Race safety: `append()` runs inside `transaction.atomic()` and uses
`select_for_update()` on the head AuditEntry to serialize concurrent writes,
so `seq` stays a strict monotone integer even under contention.

Verification: `verify_chain()` re-canonicalises every entry's stored
`payload_json` and recomputes its hash from the previous entry's stored
`this_hash`. The first inconsistency is reported precisely; we keep walking
to surface every downstream break (helpful when a single tampered entry
cascades).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
from typing import Any

from django.db import transaction
from django.utils import timezone

from core.audit.canonical import canonicalize
from core.models import AuditEntry

log = logging.getLogger(__name__)

GENESIS_PREV_HASH = "0" * 64


def _hash(prev_hash_hex: str, canonical_payload: bytes) -> str:
    """The single hash function used everywhere — append + verify share this."""
    h = hashlib.sha256()
    h.update(prev_hash_hex.encode("ascii"))
    h.update(b"|")
    h.update(canonical_payload)
    return h.hexdigest()


def _now_iso() -> str:
    """Return a timezone-aware ISO-8601 timestamp with microsecond precision."""
    return timezone.now().isoformat()


@transaction.atomic
def append(
    event_type: str,
    entity: str,
    entity_id: int | None,
    data: dict[str, Any],
    *,
    ts: str | None = None,
) -> AuditEntry:
    """Append one entry to the chain. Race-safe via select_for_update on head."""
    # Lock the current head row so two concurrent append() calls cannot pick
    # the same `seq`. If no entries exist yet (first call), there's nothing
    # to lock; the next caller will lock the row we're about to insert.
    head = (
        AuditEntry.objects.select_for_update()
        .order_by("-seq")
        .first()
    )
    seq = (head.seq + 1) if head else 1
    prev_hash = head.this_hash if head else GENESIS_PREV_HASH
    timestamp = ts or _now_iso()

    payload = {
        "seq": seq,
        "ts": timestamp,
        "event_type": event_type,
        "entity": entity,
        "entity_id": entity_id,
        "data": data,
    }
    canonical = canonicalize(payload)
    this_hash = _hash(prev_hash, canonical)

    # Persist what we hashed, byte-for-byte: store the deserialized dict in
    # JSONField (Django re-serializes), and the canonical bytes are reproducible
    # via canonicalize(payload_json) on read. We assert this is true in tests.
    entry = AuditEntry.objects.create(
        seq=seq,
        prev_hash=prev_hash,
        payload_json=payload,
        this_hash=this_hash,
        event_type=event_type,
    )
    log.debug("audit append seq=%d event_type=%s entity=%s/%s", seq, event_type, entity, entity_id)
    return entry


def head() -> AuditEntry | None:
    return AuditEntry.objects.order_by("-seq").first()


def recent(limit: int = 20) -> list[AuditEntry]:
    """Return the latest `limit` AuditEntry rows in newest-first order.

    Used by the Day-10 sign-off timeline. We deliberately do not filter
    by RFP id here — payload_json is opaque JSON, and the demo only has
    one RFP, so a global "newest 20" view is both correct and useful.
    """
    return list(AuditEntry.objects.order_by("-seq")[:limit])


def verify_chain() -> tuple[bool, list[str]]:
    """Walk every AuditEntry in seq order and rebuild the chain.

    Returns (ok, problems). `problems` lists every break with a precise
    seq + reason. An empty `problems` list means the chain is intact.
    """
    problems: list[str] = []
    expected_prev = GENESIS_PREV_HASH
    expected_seq = 1

    for entry in AuditEntry.objects.order_by("seq").iterator():
        # 1) seq must be strictly monotone with no gaps
        if entry.seq != expected_seq:
            problems.append(
                f"seq gap at entry pk={entry.pk}: expected seq={expected_seq}, got seq={entry.seq}"
            )

        # 2) prev_hash must match the previous entry's this_hash (or genesis)
        if entry.prev_hash != expected_prev:
            problems.append(
                f"seq={entry.seq}: prev_hash mismatch — stored {entry.prev_hash}, expected {expected_prev}"
            )

        # 3) recompute this_hash from canonical payload + prev_hash
        try:
            recomputed = _hash(entry.prev_hash, canonicalize(entry.payload_json))
        except (TypeError, ValueError) as e:
            problems.append(f"seq={entry.seq}: payload_json could not be canonicalised — {e}")
            recomputed = None
        if recomputed is not None and recomputed != entry.this_hash:
            problems.append(
                f"seq={entry.seq}: this_hash mismatch — stored {entry.this_hash[:16]}..., "
                f"recomputed {recomputed[:16]}... (payload_json was likely edited)"
            )

        # 4) sanity: payload_json's seq / event_type must match the indexed columns
        try:
            inner_seq = entry.payload_json.get("seq")
        except AttributeError:
            inner_seq = None
        if inner_seq != entry.seq:
            problems.append(
                f"seq={entry.seq}: payload_json.seq is {inner_seq}, expected {entry.seq}"
            )

        # Advance
        expected_prev = entry.this_hash
        expected_seq = entry.seq + 1

    return (not problems, problems)


def diagnose_chain() -> dict[str, Any]:
    """Debug dump used by `export_evidence` to embed the chain head hash
    in the signed PDF, and by `verify_demo` for the pre-flight summary."""
    entries = list(AuditEntry.objects.order_by("seq"))
    by_type: dict[str, int] = {}
    for e in entries:
        by_type[e.event_type] = by_type.get(e.event_type, 0) + 1
    return {
        "count": len(entries),
        "head_seq": entries[-1].seq if entries else 0,
        "head_hash": entries[-1].this_hash if entries else GENESIS_PREV_HASH,
        "genesis_prev_hash": GENESIS_PREV_HASH,
        "by_event_type": by_type,
    }


# Keep imports compact for callers
__all__ = [
    "GENESIS_PREV_HASH",
    "append",
    "head",
    "recent",
    "verify_chain",
    "diagnose_chain",
]


# Type alias for users that don't want to import Django's Any
PayloadDict = dict[str, Any]
del json  # not used — canonicalize handles JSON
