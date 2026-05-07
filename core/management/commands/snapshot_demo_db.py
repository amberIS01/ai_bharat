"""Day-12 management command: snapshot the current db.sqlite3 to a versioned
sidecar that can be committed to git.

The demo-day risk is that a fresh git clone + `bootstrap_demo` cold path
takes ~80 seconds (mostly Gemini API calls + Docling parsing). If venue
WiFi is flaky, that's catastrophic. By committing a pre-warmed
`db.sqlite3.demo` to the repo, the operator can `cp db.sqlite3.demo
db.sqlite3` and be demo-ready in under a second.

The command refuses to snapshot if the chain isn't green or the
expected verdict matrix doesn't match — that protects the committed
snapshot from including a buggy state.

Usage::

    python manage.py snapshot_demo_db        # write db.sqlite3.demo
    python manage.py snapshot_demo_db --force  # skip the chain check
"""

from __future__ import annotations

import hashlib
import shutil
import sys
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.audit import merkle_log


class Command(BaseCommand):
    help = "Copy db.sqlite3 → db.sqlite3.demo for committing as a demo snapshot."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force", action="store_true",
            help="Skip the audit-chain green-bar check before snapshotting.",
        )
        parser.add_argument(
            "--target", default="db.sqlite3.demo",
            help="Filename (relative to BASE_DIR) for the snapshot. Default: db.sqlite3.demo",
        )

    def handle(self, *args, **opts):
        base = Path(settings.BASE_DIR)
        src = base / "db.sqlite3"
        dst = base / opts["target"]

        if not src.exists():
            raise CommandError(f"Source DB not found at {src}.")

        if not opts["force"]:
            ok, problems = merkle_log.verify_chain()
            if not ok:
                raise CommandError(
                    "Refusing to snapshot a DB whose audit chain is broken. "
                    f"{len(problems)} problem(s): {problems[:3]}. "
                    "Pass --force to override."
                )

        shutil.copyfile(src, dst)
        sha = hashlib.sha256(dst.read_bytes()).hexdigest()
        size_mb = dst.stat().st_size / (1024 * 1024)

        self.stdout.write(self.style.SUCCESS(
            f"=== Snapshot written ==="
        ))
        self.stdout.write(f"  source:   {src}")
        self.stdout.write(f"  target:   {dst}")
        self.stdout.write(f"  size:     {size_mb:.2f} MB")
        self.stdout.write(f"  sha256:   {sha}")
        self.stdout.write("")
        self.stdout.write(
            "Commit this file to git so a fresh clone is demo-ready in <1 second:\n"
            f"    git add {opts['target']}\n"
            f"    git commit -m 'Update demo DB snapshot'"
        )
