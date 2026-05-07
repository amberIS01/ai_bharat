"""Day-15 fix #7 — derive a Verdict's effective status from the most
recent APPROVED OverrideRequest, falling back to the original Rego
output when no approved override exists.

The original `Verdict.status` (set by OPA) is preserved as immutable
truth. Officer overrides flow through `OverrideRequest`, which an
admin approves. Until Day 15, the eval grid + drilldown + signed PDF
all read `Verdict.status` directly — so an approved override changed
nothing visible. That's the integrity gap reviewers will catch first.

`effective_status(verdict)` returns the status the system "stands by"
right now: approved override (newest first) wins, else the original.

Usage:

    from core.policy.effective import effective_status, effective_override
    eff = effective_status(verdict)            # "PASS" / "FAIL" / "ABSTAIN"
    override = effective_override(verdict)     # OverrideRequest or None

The helper is read-only and side-effect-free; it does not mutate
`Verdict.status`. The audit trail of *why* the effective status
differs lives entirely in the OverrideRequest history.
"""

from __future__ import annotations

from typing import Optional

from core.models import OverrideRequest, OverrideStatus, Verdict


def effective_override(verdict: Verdict) -> Optional[OverrideRequest]:
    """Return the most recent APPROVED OverrideRequest for `verdict`,
    or None if none exist.

    Newest-wins semantics: if an officer files two approved overrides
    over time (e.g. PASS → FAIL → PASS), the latest one is what the
    system stands by.
    """
    return (
        OverrideRequest.objects
        .filter(verdict=verdict, status=OverrideStatus.APPROVED)
        .order_by("-ts")
        .first()
    )


def effective_status(verdict: Verdict) -> str:
    """Return `verdict`'s effective status — approved override wins,
    else the original Rego status.
    """
    override = effective_override(verdict)
    if override is not None:
        return override.requested_status
    return verdict.status


def is_overridden(verdict: Verdict) -> bool:
    """Cheap helper for templates: True iff effective ≠ original."""
    return effective_override(verdict) is not None


__all__ = ["effective_override", "effective_status", "is_overridden"]
