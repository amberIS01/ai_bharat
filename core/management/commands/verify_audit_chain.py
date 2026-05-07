"""Walk the AuditEntry chain and report any inconsistency.

Exit code 0 on a green chain, 1 on first inconsistency. Useful as a CI gate
before exporting the evidence PDF on Day 6.
"""

from __future__ import annotations

import sys

from django.core.management.base import BaseCommand

from core.audit.merkle_log import diagnose_chain, verify_chain


class Command(BaseCommand):
    help = "Walk the Merkle audit chain and report any inconsistency."

    def handle(self, *args, **opts):
        info = diagnose_chain()
        self.stdout.write(self.style.MIGRATE_HEADING("=== Audit chain status ==="))
        self.stdout.write(f"  total entries: {info['count']}")
        self.stdout.write(f"  head seq:      {info['head_seq']}")
        self.stdout.write(f"  head hash:     {info['head_hash']}")
        if info["count"] == 0:
            self.stdout.write(self.style.WARNING(
                "Audit chain is empty. Run any state-changing command (ingest_bundle, "
                "extract_facts, evaluate_verdicts) to populate it."
            ))
            return

        ok, problems = verify_chain()
        if ok:
            self.stdout.write(self.style.SUCCESS(
                "  GREEN — every entry's hash chains correctly from genesis."
            ))
            return

        self.stdout.write(self.style.ERROR(
            f"  TAMPER DETECTED — {len(problems)} inconsistency(ies):"
        ))
        for p in problems:
            self.stdout.write(self.style.ERROR(f"    - {p}"))
        sys.exit(1)
