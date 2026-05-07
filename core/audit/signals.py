"""Django signal receivers that drop a Merkle audit entry on each mutation.

Wiring lives in `core.apps.CoreConfig.ready()`. Every receiver:

  * skips `raw=True` (fixture loads must not pollute the audit log)
  * uses an explicit `dispatch_uid` so a stray re-import cannot register twice
  * for models whose audit-relevant data lives in M2M fields (Fact, Verdict),
    schedules the actual `merkle_log.append()` call via
    `transaction.on_commit(...)` so the M2M `.set()` that follows the .save()
    is visible when we serialize. on_commit also gives the right
    "transaction-rolled-back ⇒ no audit entry" semantic for free.

Bulk operations (`Block.objects.bulk_create(...)` in docling_parser, `update()`
querysets) are documented in the Django signals docs as bypassing post_save.
We handle that path by an explicit `merkle_log.append("blocks.ingested", ...)`
in the parser itself.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from core.audit import merkle_log
from core.audit.serializers import (
    serialize_criterion,
    serialize_document,
    serialize_fact,
    serialize_override,
    serialize_verdict,
)
from core.models import (
    Criterion,
    Document,
    Fact,
    OverrideRequest,
    Verdict,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Document
# ---------------------------------------------------------------------------

@receiver(post_save, sender=Document, dispatch_uid="praman_audit_document_save")
def _on_document_saved(sender, instance, created, raw, **kwargs):
    if raw:
        return
    event = "document.created" if created else "document.updated"
    merkle_log.append(event, "document", instance.id, serialize_document(instance))


@receiver(post_delete, sender=Document, dispatch_uid="praman_audit_document_delete")
def _on_document_deleted(sender, instance, **kwargs):
    # Capture the snapshot at delete-time. Once the row is gone the FK joins
    # below would yield NULLs, so we copy what we need into the payload.
    snapshot = serialize_document(instance)
    merkle_log.append("document.deleted", "document", instance.id, snapshot)


# ---------------------------------------------------------------------------
# Criterion
# ---------------------------------------------------------------------------

@receiver(post_save, sender=Criterion, dispatch_uid="praman_audit_criterion_save")
def _on_criterion_saved(sender, instance, created, raw, **kwargs):
    if raw:
        return
    event = "criterion.created" if created else "criterion.updated"
    merkle_log.append(event, "criterion", instance.id, serialize_criterion(instance))


# ---------------------------------------------------------------------------
# Fact — uses on_commit to wait for evidence_blocks M2M to settle
# ---------------------------------------------------------------------------

def _audit_fact(pk: int, event: str) -> None:
    """Re-fetch the Fact with M2M prefetched, then append the audit entry."""
    fact = (
        Fact.objects.prefetch_related("evidence_blocks").filter(pk=pk).first()
    )
    if fact is None:
        # Fact was deleted before the on_commit callback ran. Don't audit a
        # ghost — the deletion will get its own audit entry from post_delete.
        return
    merkle_log.append(event, "fact", pk, serialize_fact(fact))


@receiver(post_save, sender=Fact, dispatch_uid="praman_audit_fact_save")
def _on_fact_saved(sender, instance, created, raw, **kwargs):
    if raw:
        return
    event = "fact.created" if created else "fact.updated"
    pk = instance.pk
    transaction.on_commit(lambda: _audit_fact(pk, event))


# ---------------------------------------------------------------------------
# Verdict — same on_commit pattern as Fact (evidence_refs M2M)
# ---------------------------------------------------------------------------

def _audit_verdict(pk: int, event: str) -> None:
    verdict = (
        Verdict.objects.prefetch_related("evidence_refs").filter(pk=pk).first()
    )
    if verdict is None:
        return
    merkle_log.append(event, "verdict", pk, serialize_verdict(verdict))


@receiver(post_save, sender=Verdict, dispatch_uid="praman_audit_verdict_save")
def _on_verdict_saved(sender, instance, created, raw, **kwargs):
    if raw:
        return
    event = "verdict.emitted" if created else "verdict.updated"
    pk = instance.pk
    transaction.on_commit(lambda: _audit_verdict(pk, event))


# ---------------------------------------------------------------------------
# OverrideRequest
# ---------------------------------------------------------------------------

@receiver(post_save, sender=OverrideRequest, dispatch_uid="praman_audit_override_save")
def _on_override_saved(sender, instance, created, raw, **kwargs):
    if raw:
        return
    if created:
        event = "override.requested"
    elif instance.status == "APPROVED":
        event = "override.approved"
    elif instance.status == "REJECTED":
        event = "override.rejected"
    else:
        event = "override.updated"
    merkle_log.append(event, "override", instance.id, serialize_override(instance))
