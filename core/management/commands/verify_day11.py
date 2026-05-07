"""Day-11 acceptance gate: full end-to-end pipeline test.

Sequence:
    1. Reset the database to a clean state (drop Documents → CASCADEs to
       Blocks/Criteria/Facts/Verdicts).
    2. Reset the audit chain.
    3. Run `bootstrap_demo --skip-evaluate` so OPA isn't required for the
       parse/extract steps.
    4. Run `evaluate_verdicts` separately so the OPA-down message is
       distinct from a parse failure.
    5. Assert the post-conditions:
         * 1 RFP, ≥12 bidder Documents, ≥50 Blocks, ≥1 GEMINI_VISION block.
         * 7 Criteria, 21 Facts, 21 Verdicts. (Days 5/9 added two
           pre-qualification criteria — Registered Contractor Status and
           PAN Registration — that have no Rego rule and correctly route
           to ABSTAIN no_rule_mapped. The 5 *Rego-mapped* categories are
           turnover / GST / DSC / experience / ISO; the 7 *extracted*
           criteria are those plus the two unmapped pre-quals.)
         * Verdict matrix (the demo's narrative spine):
             - Bidder A: 5 PASS + 2 ABSTAIN on C-1, C-3 (no_rule_mapped)
             - Bidder B: 4 PASS + 1 FAIL on C-5 (turnover) + 2 ABSTAIN
             - Bidder C: 4 PASS + 1 ABSTAIN on C-4 (low-conf GST) + 2 ABSTAIN
         * Audit chain `verify_chain()` is green.
    6. Run `export_evidence` and tamper-test the signed PDF.

Run from a clean DB (or with --reset to wipe state in-place):

    python manage.py start_opa &
    python manage.py verify_day11 --reset
"""

from __future__ import annotations

import sys

from django.core.management import call_command
from django.core.management.base import BaseCommand

from core.audit import merkle_log
from core.audit.signer import verify_signed_pdf
from core.models import (
    AuditEntry,
    Block,
    BlockSource,
    Criterion,
    Document,
    DocumentType,
    Fact,
    Verdict,
    VerdictStatus,
)


# Expected verdict matrix — the demo's load-bearing claim. Each value is
# the expected status for (bidder_code, criterion_code).
#
# The current pipeline produces 7 criteria (Gemini extract was re-run with
# a stricter prompt on Day 5/9 and added "Registered Contractor Status"
# and "PAN" alongside the original 5):
#   C-1: Registered Contractor Status   (no Rego rule → ABSTAIN no_rule_mapped)
#   C-2: DSC                            → c3_dsc.rego
#   C-3: PAN                            (no Rego rule → ABSTAIN no_rule_mapped)
#   C-4: GST                            → c2_gst.rego
#   C-5: Min Annual Turnover            → c1_turnover.rego
#   C-6: Past Experience                → c4_experience.rego
#   C-7: Quality Management Cert        → c5_iso.rego
#
# Demo intent for the 7-criterion model:
#   Bidder A (clean): 5 PASS on mapped + 2 ABSTAIN on unmapped C-1/C-3
#   Bidder B (low turnover): 4 PASS + FAIL on C-5 + 2 ABSTAIN on C-1/C-3
#   Bidder C (distorted GST): 4 PASS + ABSTAIN on C-4 + 2 ABSTAIN on C-1/C-3
EXPECTED_MATRIX = {
    ("A", "C-1"): VerdictStatus.ABSTAIN.value,  # no Rego rule mapped
    ("A", "C-2"): VerdictStatus.PASS.value,
    ("A", "C-3"): VerdictStatus.ABSTAIN.value,  # no Rego rule mapped
    ("A", "C-4"): VerdictStatus.PASS.value,
    ("A", "C-5"): VerdictStatus.PASS.value,
    ("A", "C-6"): VerdictStatus.PASS.value,
    ("A", "C-7"): VerdictStatus.PASS.value,
    ("B", "C-1"): VerdictStatus.ABSTAIN.value,
    ("B", "C-2"): VerdictStatus.PASS.value,
    ("B", "C-3"): VerdictStatus.ABSTAIN.value,
    ("B", "C-4"): VerdictStatus.PASS.value,
    ("B", "C-5"): VerdictStatus.FAIL.value,     # turnover below threshold
    ("B", "C-6"): VerdictStatus.PASS.value,
    ("B", "C-7"): VerdictStatus.PASS.value,
    ("C", "C-1"): VerdictStatus.ABSTAIN.value,
    ("C", "C-2"): VerdictStatus.PASS.value,
    ("C", "C-3"): VerdictStatus.ABSTAIN.value,
    ("C", "C-4"): VerdictStatus.ABSTAIN.value,  # distorted GST → low conf
    ("C", "C-5"): VerdictStatus.PASS.value,
    ("C", "C-6"): VerdictStatus.PASS.value,
    ("C", "C-7"): VerdictStatus.PASS.value,
}


class Command(BaseCommand):
    help = "Day-11 end-to-end acceptance: bootstrap → verdicts → audit chain → tamper test."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset", action="store_true",
            help="Wipe Documents/Blocks/Criteria/Facts/Verdicts/AuditEntries before running.",
        )
        parser.add_argument(
            "--skip-bootstrap", action="store_true",
            help="Skip bootstrap_demo (use the existing DB state).",
        )

    def handle(self, *args, **opts):
        failures: list[str] = []

        # ---- [1/6] Optional reset -----------------------------------------
        if opts["reset"]:
            self.stdout.write(self.style.MIGRATE_HEADING(
                "\n[1/6] --reset: wiping pipeline state"
            ))
            Document.objects.all().delete()  # CASCADE → Block, Criterion, etc.
            AuditEntry.objects.all().delete()
            self.stdout.write(self.style.SUCCESS("  state wiped"))

        # ---- [2/6] Bootstrap pipeline -------------------------------------
        if not opts["skip_bootstrap"]:
            self.stdout.write(self.style.MIGRATE_HEADING(
                "\n[2/6] Running bootstrap_demo --skip-evaluate"
            ))
            try:
                call_command("bootstrap_demo", "--skip-evaluate")
            except Exception as e:  # noqa: BLE001
                failures.append(f"bootstrap_demo failed: {type(e).__name__}: {e}")
                self._report(failures)
                return

        # ---- [3/6] Run evaluation separately ------------------------------
        self.stdout.write(self.style.MIGRATE_HEADING(
            "\n[3/6] Running evaluate_verdicts (requires OPA at 127.0.0.1:8181)"
        ))
        try:
            call_command("evaluate_verdicts")
        except Exception as e:  # noqa: BLE001
            failures.append(
                f"evaluate_verdicts failed (is OPA running? `python manage.py start_opa`): "
                f"{type(e).__name__}: {e}"
            )
            self._report(failures)
            return

        # ---- [4/6] Post-condition assertions ------------------------------
        self.stdout.write(self.style.MIGRATE_HEADING(
            "\n[4/6] Verifying pipeline outputs"
        ))

        rfp_count = Document.objects.filter(type=DocumentType.RFP).count()
        bidder_doc_count = Document.objects.exclude(bidder_code="").count()
        block_count = Block.objects.count()
        gemini_blocks = Block.objects.filter(source=BlockSource.GEMINI_VISION).count()
        criterion_count = Criterion.objects.count()
        fact_count = Fact.objects.count()
        verdict_count = Verdict.objects.count()

        self.stdout.write(f"  RFP Documents:                   {rfp_count}")
        self.stdout.write(f"  Bidder Documents:                {bidder_doc_count}")
        self.stdout.write(f"  Blocks (DOCLING + GEMINI_VISION): {block_count} ({gemini_blocks} from Gemini)")
        self.stdout.write(f"  Criteria:                        {criterion_count}")
        self.stdout.write(f"  Facts:                           {fact_count}")
        self.stdout.write(f"  Verdicts:                        {verdict_count}")

        if rfp_count != 1:
            failures.append(f"Expected 1 RFP, found {rfp_count}.")
        if bidder_doc_count < 12:
            failures.append(f"Expected ≥12 bidder Documents, found {bidder_doc_count}.")
        if block_count < 50:
            failures.append(f"Expected ≥50 Blocks, found {block_count}.")
        if gemini_blocks < 1:
            failures.append(
                "Expected at least 1 Block from GEMINI_VISION (Bidder C distorted GST), found 0."
            )
        if criterion_count != 7:
            failures.append(f"Expected 7 Criteria, found {criterion_count}.")
        if fact_count != 21:
            failures.append(f"Expected 21 Facts (7 × 3 bidders), found {fact_count}.")
        if verdict_count != 21:
            failures.append(f"Expected 21 Verdicts (7 × 3 bidders), found {verdict_count}.")

        # ---- [5/6] Verdict matrix check -----------------------------------
        self.stdout.write(self.style.MIGRATE_HEADING(
            "\n[5/6] Checking verdict matrix"
        ))
        actual = {
            (v.bidder_code, v.criterion.code): v.status
            for v in Verdict.objects.select_related("criterion")
        }
        for (bidder, code), expected_status in EXPECTED_MATRIX.items():
            got = actual.get((bidder, code))
            if got != expected_status:
                failures.append(
                    f"Verdict matrix mismatch: bidder={bidder} criterion={code} "
                    f"expected={expected_status} got={got!r}"
                )
            else:
                self.stdout.write(f"  ✓ {bidder} × {code}: {got}")

        # ---- Audit chain integrity ----------------------------------------
        chain_ok, chain_problems = merkle_log.verify_chain()
        if not chain_ok:
            failures.append(
                f"Merkle audit chain has {len(chain_problems)} problem(s): {chain_problems[:3]}"
            )
        else:
            head = merkle_log.head()
            self.stdout.write(self.style.SUCCESS(
                f"  ✓ audit chain green ({head.seq if head else 0} entries, head={head.this_hash[:16] if head else '-'}…)"
            ))

        # ---- [6/6] Sign-off + tamper test ---------------------------------
        self.stdout.write(self.style.MIGRATE_HEADING(
            "\n[6/6] Sign-off + tamper test"
        ))
        try:
            call_command("verify_day6")
        except SystemExit as e:
            if e.code:
                failures.append(f"verify_day6 (signed-PDF gate) exited with code {e.code}.")
        except Exception as e:  # noqa: BLE001
            failures.append(f"verify_day6 raised: {type(e).__name__}: {e}")

        # ---- Verdict ------------------------------------------------------
        self._report(failures)

    def _report(self, failures: list[str]) -> None:
        self.stdout.write("")
        if failures:
            self.stdout.write(self.style.ERROR("=== Day-11 FAIL ==="))
            for f in failures:
                self.stdout.write(self.style.ERROR(f"  - {f}"))
            sys.exit(1)
        self.stdout.write(self.style.SUCCESS(
            "=== Day-11 PASS — pipeline end-to-end is demo-ready ==="
        ))
