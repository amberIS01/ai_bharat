"""Get a fresh checkout to the demo screen in one command.

Day-10 hostile-audit fix R5: Day-1 through Day-6 require running ~7
management commands in the right order to populate the demo dataset
(generate_synthetic → ingest_bundle ×4 → extract_criteria → extract_facts →
evaluate_verdicts). On a fresh git clone or rebuilt demo VM, that's a
real risk — one missed step and the dashboard shows an empty grid.

This command chains the whole pipeline, prints each step, and is safe to
re-run (every downstream command is idempotent: ingest skips on SHA-256
hit, extract uses GeminiCallCache, evaluate is idempotent on `(criterion,
bidder_code)`).

Usage::

    python manage.py bootstrap_demo                     # full pipeline
    python manage.py bootstrap_demo --skip-evaluate     # if OPA isn't running
    python manage.py bootstrap_demo --skip-synthetic    # already generated

Run `python manage.py start_opa` separately before invoking this command
unless --skip-evaluate is passed.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError


SYNTHETIC_DIR = Path(settings.BASE_DIR) / "synthetic"
RFP_FILENAME = "rfp_synthetic_construction_001.pdf"
BIDDER_DIRS = ("bidder_a", "bidder_b", "bidder_c")
DEMO_SNAPSHOT_PATH = Path(settings.BASE_DIR) / "db.sqlite3.demo"
LIVE_DB_PATH = Path(settings.BASE_DIR) / "db.sqlite3"


class Command(BaseCommand):
    help = "Run generate_synthetic + ingest_bundle ×4 + extract + evaluate end-to-end."

    def add_arguments(self, parser):
        parser.add_argument(
            "--skip-synthetic", action="store_true",
            help="Skip generate_synthetic (assume the dataset is already there).",
        )
        parser.add_argument(
            "--skip-extract", action="store_true",
            help="Skip extract_criteria + extract_facts.",
        )
        parser.add_argument(
            "--skip-evaluate", action="store_true",
            help="Skip evaluate_verdicts (use when OPA is not running).",
        )
        parser.add_argument(
            "--no-snapshot", action="store_true",
            help="Don't try to use db.sqlite3.demo even if it exists.",
        )
        parser.add_argument(
            "--restore-snapshot", action="store_true",
            help="Force-overwrite db.sqlite3 with db.sqlite3.demo, "
                 "even if the live DB already exists. Day-13 fix C1: lets "
                 "the operator reset to known-good demo state in one command.",
        )

    def handle(self, **opts):
        # Day-13 C1: --restore-snapshot is the loud path. If both files
        # exist and the user explicitly asks for a reset, copy and return.
        if opts["restore_snapshot"]:
            if not DEMO_SNAPSHOT_PATH.exists():
                raise CommandError(
                    f"--restore-snapshot requested but {DEMO_SNAPSHOT_PATH} does not exist. "
                    f"Run `python manage.py snapshot_demo_db` after a clean bootstrap to create one."
                )
            self.stdout.write(self.style.MIGRATE_HEADING(
                f"=== bootstrap_demo · --restore-snapshot ==="
            ))
            shutil.copyfile(DEMO_SNAPSHOT_PATH, LIVE_DB_PATH)
            self.stdout.write(self.style.SUCCESS(
                f"  overwrote {LIVE_DB_PATH} ← {DEMO_SNAPSHOT_PATH}"
            ))
            return

        # Day-13 C1: snapshot freshness check. If both DBs exist, compare
        # SHA-256 and warn loudly when they diverge — silent stale-state
        # was the demo-day risk Day-12 left open.
        if (not opts["no_snapshot"]
                and DEMO_SNAPSHOT_PATH.exists()
                and LIVE_DB_PATH.exists()):
            snap_sha = hashlib.sha256(DEMO_SNAPSHOT_PATH.read_bytes()).hexdigest()
            live_sha = hashlib.sha256(LIVE_DB_PATH.read_bytes()).hexdigest()
            if snap_sha != live_sha:
                self.stdout.write(self.style.WARNING(
                    f"\n⚠ Live DB diverges from db.sqlite3.demo snapshot:\n"
                    f"    live sha256 = {live_sha[:16]}…\n"
                    f"    snap sha256 = {snap_sha[:16]}…\n"
                    f"  The live DB will be used as-is. To restore the demo state, run:\n"
                    f"    python manage.py bootstrap_demo --restore-snapshot\n"
                    f"  Or to keep the live state silently, pass --no-snapshot.\n"
                ))

        # If a pre-warmed db.sqlite3.demo snapshot exists and the live DB is
        # missing, copy the snapshot over and short-circuit. This is the
        # "demo morning ritual" path — sub-second instead of ~80 seconds.
        if (not opts["no_snapshot"]
                and DEMO_SNAPSHOT_PATH.exists()
                and not LIVE_DB_PATH.exists()):
            self.stdout.write(self.style.MIGRATE_HEADING(
                f"=== bootstrap_demo · using db.sqlite3.demo snapshot ==="
            ))
            shutil.copyfile(DEMO_SNAPSHOT_PATH, LIVE_DB_PATH)
            self.stdout.write(self.style.SUCCESS(
                f"  copied {DEMO_SNAPSHOT_PATH} → {LIVE_DB_PATH}\n"
                f"  bootstrap_demo skipped (snapshot path)\n"
                f"  Login at http://127.0.0.1:8000/officer/  with officer / praman_demo_2026"
            ))
            return

        steps: list[tuple[str, list[str]]] = [
            ("create_default_officer", []),
        ]
        if not opts["skip_synthetic"]:
            steps.append(("generate_synthetic", ["--clean"]))

        rfp_path = SYNTHETIC_DIR / RFP_FILENAME
        if not rfp_path.exists() and not opts["skip_synthetic"]:
            # generate_synthetic will create it during the run; mark for ingest.
            pass
        steps.append(("ingest_bundle", [str(rfp_path)]))
        for bidder in BIDDER_DIRS:
            steps.append(("ingest_bundle", [str(SYNTHETIC_DIR / bidder)]))

        if not opts["skip_extract"]:
            steps.append(("extract_criteria", []))
            steps.append(("extract_facts", []))

        if not opts["skip_evaluate"]:
            steps.append(("evaluate_verdicts", []))

        for idx, (name, args) in enumerate(steps, start=1):
            self.stdout.write(self.style.MIGRATE_HEADING(
                f"\n=== bootstrap_demo · step {idx}/{len(steps)} · {name} {' '.join(args)} ==="
            ))
            try:
                call_command(name, *args)
            except (CommandError, Exception) as e:  # noqa: BLE001
                # On any sub-command failure, print a copy-pastable "resume
                # from here" block before re-raising so the operator doesn't
                # have to figure out where the pipeline stopped. (Day-11 A6.)
                resume_lines = [f"python manage.py {n} {' '.join(a)}".rstrip()
                                for (n, a) in steps[idx-1:]]
                self.stderr.write(self.style.WARNING(
                    f"\nbootstrap_demo aborted at step {idx}/{len(steps)} "
                    f"({name}): {type(e).__name__}: {e}"
                ))
                self.stderr.write(self.style.WARNING(
                    "Resume from this step by running each line below in order:"
                ))
                for line in resume_lines:
                    self.stderr.write(f"    {line}")
                if isinstance(e, CommandError):
                    raise
                raise CommandError(
                    f"bootstrap_demo aborted at step '{name}': "
                    f"{type(e).__name__}: {e}"
                ) from e

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=== bootstrap_demo complete ==="))
        self.stdout.write(
            "Login at http://127.0.0.1:8000/officer/  "
            "with username 'officer' and password 'praman_demo_2026'."
        )
