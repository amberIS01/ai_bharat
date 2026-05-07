"""Thin OPA HTTP client.

`evaluate_verdict(criterion, fact)` is the public surface. It:
    1. Translates (criterion, fact) into the input.fact + input.criterion
       shape the Rego rules consume (via core.policy.parsers).
    2. POSTs to OPA at /v1/data/<rule_path>.
    3. Persists a Verdict row with the structured response.
    4. Wires `evidence_refs` M2M from the Fact's evidence_blocks.

OPA must be reachable at settings.OPA_URL (default http://127.0.0.1:8181).
The `start_opa` management command spawns OPA pointed at deploy/rules/.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from django.conf import settings
from django.db import transaction

from core.models import (
    Criterion,
    Fact,
    Verdict,
    VerdictStatus,
)
from core.policy.parsers import fact_for_rego, rego_path_for_criterion

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class OPAError(RuntimeError):
    """OPA returned an unusable response or failed to reach the server."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def opa_health_check(timeout: float = 1.0) -> tuple[bool, str]:
    """Quick check before officer-facing flows kick off `evaluate_verdicts`.

    Returns (ok, message). On failure the message is human-readable so the
    Day-7 UI can show it directly instead of a 500.
    """
    url = f"{settings.OPA_URL.rstrip('/')}/health"
    try:
        resp = httpx.get(url, timeout=timeout)
        resp.raise_for_status()
        return True, "OPA reachable"
    except httpx.HTTPError as e:
        return False, (
            f"OPA is not reachable at {settings.OPA_URL}. "
            f"Start it via `python manage.py start_opa` and wait for the health "
            f"endpoint to come up. ({type(e).__name__}: {e})"
        )


def opa_query(rule_path: str, input_obj: dict[str, Any]) -> dict[str, Any]:
    """Synchronous OPA query. Returns the `result` field of the response.

    Raises OPAError if OPA is unreachable, returns no result, or returns a
    non-dict result. The Rego rules in deploy/rules/ always return a dict
    (`{verdict, rule_id, reason, bindings}`); a non-dict result means we
    pointed at the wrong rule_path or hit an undefined rule.
    """
    url = f"{settings.OPA_URL.rstrip('/')}/v1/data/{rule_path.lstrip('/')}"
    try:
        resp = httpx.post(url, json={"input": input_obj}, timeout=10.0)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise OPAError(f"OPA HTTP error querying {url}: {e}") from e

    body = resp.json()
    if "result" not in body:
        raise OPAError(
            f"OPA returned no `result` for {url}. "
            f"This usually means the rule is undefined. Body: {body}"
        )
    result = body["result"]
    if not isinstance(result, dict):
        raise OPAError(
            f"OPA returned a non-dict result for {url}: {result!r}. "
            f"Praman rules must always return {{verdict, rule_id, reason, bindings}}."
        )
    if "verdict" not in result:
        raise OPAError(
            f"OPA result for {url} is missing the `verdict` key: {result!r}"
        )
    return result


@transaction.atomic
def evaluate_verdict(
    criterion: Criterion,
    fact: Fact,
    *,
    confidence_threshold: float | None = None,
) -> Verdict:
    """Run the criterion's Rego rule against the Fact and persist a Verdict row."""
    rule_path = rego_path_for_criterion(criterion)
    if not rule_path:
        # Gemini sometimes extracts pre-qualification clauses (e.g. "Registered
        # Contractor Status") that we don't have a Rego rule for. Don't crash —
        # emit a structured ABSTAIN that the UI / officer can act on. The
        # rule_id makes it clear which "no rule" case triggered.
        verdict, _ = Verdict.objects.update_or_create(
            criterion=criterion,
            bidder_code=fact.bidder_code,
            defaults={
                "status": VerdictStatus.ABSTAIN.value,
                "rule_id": "no_rule_mapped",
                "bindings_json": {
                    "criterion_code": criterion.code,
                    "criterion_title": criterion.title,
                },
                "reason": (
                    f"No Rego rule is currently mapped for criterion "
                    f"{criterion.code!r} ('{criterion.title}'). Routed to "
                    f"manual review. Add a regex to "
                    f"core/policy/parsers._TITLE_TO_REGO_RULES if this is a "
                    f"recurring criterion."
                ),
            },
        )
        verdict.evidence_refs.set(list(fact.evidence_blocks.all()))
        log.info(
            "Verdict (%s × %s) = ABSTAIN/no_rule_mapped (title=%r)",
            criterion.code, fact.bidder_code, criterion.title,
        )
        return verdict

    threshold = (
        confidence_threshold
        if confidence_threshold is not None
        else settings.OCR_CONFIDENCE_THRESHOLD
    )

    input_obj = {
        "criterion": {
            "code": criterion.code,
            "type": criterion.type,
            "title": criterion.title,
            "mandatory": criterion.mandatory,
        },
        "fact": fact_for_rego(criterion, fact),
        "confidence_threshold": float(threshold),
    }

    result = opa_query(rule_path, input_obj)
    verdict_str = result["verdict"]
    if verdict_str not in {s.value for s in VerdictStatus}:
        raise OPAError(
            f"Rego rule emitted unknown verdict {verdict_str!r}. "
            f"Allowed: {[s.value for s in VerdictStatus]}."
        )

    # Day-15 fix #7: Verdict.decided_at is auto_now_add — set on first
    # save only. Without this explicit refresh, re-evaluating after a
    # criteria edit leaves decided_at stuck at the original timestamp,
    # so the eval_grid's "verdicts are stale" banner never goes away.
    # Update decided_at on every evaluate so it tracks the most recent
    # OPA decision, not the row's birthday.
    from django.utils import timezone as _tz
    verdict, _ = Verdict.objects.update_or_create(
        criterion=criterion,
        bidder_code=fact.bidder_code,
        defaults={
            "status": verdict_str,
            "rule_id": result.get("rule_id", ""),
            "bindings_json": result.get("bindings", {}),
            "reason": result.get("reason", ""),
            "decided_at": _tz.now(),
        },
    )
    # Carry the Fact's evidence forward — every Verdict cites the same
    # bbox/block evidence the Fact was derived from. That's the chain the
    # Day-9 drilldown UI follows: cell click → Verdict → evidence Blocks → bbox.
    verdict.evidence_refs.set(list(fact.evidence_blocks.all()))

    log.info(
        "Verdict (%s × %s) = %s (rule_id=%s)",
        criterion.code, fact.bidder_code, verdict_str, verdict.rule_id,
    )
    return verdict


def evaluate_all_verdicts(*, confidence_threshold: float | None = None) -> list[Verdict]:
    """Run the rule engine for every (Criterion, Fact) pair currently in the DB."""
    out: list[Verdict] = []
    for fact in Fact.objects.select_related("criterion").all():
        if rego_path_for_criterion(fact.criterion) is None:
            log.warning(
                "Skipping criterion %s (%r): no Rego rule mapped",
                fact.criterion.code, fact.criterion.title,
            )
            continue
        out.append(evaluate_verdict(
            fact.criterion, fact, confidence_threshold=confidence_threshold,
        ))
    return out
