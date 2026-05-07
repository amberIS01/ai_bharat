"""Day-4 management command: evaluate Rego rules over every (criterion × bidder)
Fact and persist Verdict rows.

Usage:
    python manage.py evaluate_verdicts
    python manage.py evaluate_verdicts --bidder C
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from core.models import Fact, Verdict
from core.policy.opa_client import OPAError, evaluate_verdict


class Command(BaseCommand):
    help = "Run OPA / Rego rules over every Fact in DB and persist Verdicts."

    def add_arguments(self, parser):
        parser.add_argument("--bidder", type=str, default=None,
                            help="Restrict to a single bidder code (A / B / C).")
        parser.add_argument("--criterion", type=str, default=None,
                            help="Restrict to a single criterion code (C-1, C-2, ...).")

    def handle(self, *args, **opts):
        qs = Fact.objects.select_related("criterion").all()
        if opts.get("bidder"):
            qs = qs.filter(bidder_code=opts["bidder"].upper().strip())
        if opts.get("criterion"):
            qs = qs.filter(criterion__code=opts["criterion"].upper().strip())

        if not qs.exists():
            self.stdout.write(self.style.ERROR(
                "No Fact rows match. Run `extract_facts` first, or relax the filters."
            ))
            return

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"Evaluating {qs.count()} Fact rows via OPA ..."
        ))

        rows: list[tuple[str, str, str, str, str]] = []
        for fact in qs.order_by("bidder_code", "criterion__code"):
            try:
                v = evaluate_verdict(fact.criterion, fact)
            except OPAError as e:
                self.stdout.write(self.style.ERROR(
                    f"  {fact.criterion.code} × {fact.bidder_code}: ERROR — {e}"
                ))
                rows.append((fact.bidder_code, fact.criterion.code, "ERROR", str(e), ""))
                continue
            rows.append((fact.bidder_code, fact.criterion.code, v.status, v.rule_id, v.reason))

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"=== Verdict matrix (Verdicts in DB: {Verdict.objects.count()}) ==="))
        for bidder, code, status, rule_id, reason in rows:
            badge = {
                "PASS": self.style.SUCCESS,
                "FAIL": self.style.ERROR,
                "ABSTAIN": self.style.WARNING,
            }.get(status, self.style.NOTICE)
            self.stdout.write(
                f"  Bidder {bidder}  {code}  {badge(status):<7}  rule={rule_id}"
            )
            self.stdout.write(f"        reason: {reason[:120]}")
