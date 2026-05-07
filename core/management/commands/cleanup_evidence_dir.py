"""Day-14 management command: clean up `media/evidence/` before demo.

The Day-12 multi-PDF history table on the signoff page reads from
`media/evidence/rfp{id}_evidence_*_signed.pdf`. After many days of
dev + the Day-6 / Day-11 acceptance-gate runs, this directory
accumulates:

- Test-run subdirectories (e.g. `verify_day6/`) that aren't part of
  the demo.
- Signed PDFs with no matching `evidence.signed` AuditEntry (orphaned
  after a DB reset / restore).
- Stale demo PDFs older than 24 hours.

Usage::

    python manage.py cleanup_evidence_dir              # dry-run by default
    python manage.py cleanup_evidence_dir --apply      # actually delete
    python manage.py cleanup_evidence_dir --apply --max-age-hours 48

The command never touches the live `db.sqlite3` or the snapshot.
"""

from __future__ import annotations

import hashlib
import shutil
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from core.models import AuditEntry


class Command(BaseCommand):
    help = "Remove orphaned and stale signed-PDF artefacts from media/evidence/."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply", action="store_true",
            help="Actually delete files. Default is dry-run.",
        )
        parser.add_argument(
            "--max-age-hours", type=int, default=24,
            help="Delete files older than this many hours (default: 24).",
        )
        parser.add_argument(
            "--keep-test-dirs", action="store_true",
            help="Keep verify_day6/ and similar test subdirectories.",
        )

    def handle(self, *args, **opts):
        evidence_dir = Path(settings.BASE_DIR) / "media" / "evidence"
        if not evidence_dir.exists():
            self.stdout.write(self.style.NOTICE(f"  {evidence_dir} does not exist — nothing to clean."))
            return

        apply = opts["apply"]
        max_age_hours = opts["max_age_hours"]
        cutoff = datetime.now(tz=timezone.utc).timestamp() - (max_age_hours * 3600)

        # Build the set of signed-PDF SHA-256s that have a matching audit entry.
        # Anything in the directory whose sha256 isn't in this set is orphaned.
        valid_shas: set[str] = set()
        for e in AuditEntry.objects.filter(event_type="evidence.signed"):
            sha = e.payload_json.get("data", {}).get("signed_pdf_sha256")
            if sha:
                valid_shas.add(sha)

        to_delete_files: list[Path] = []
        to_delete_dirs: list[Path] = []

        # Pass 1: handle test subdirectories (verify_day6/ etc.)
        if not opts["keep_test_dirs"]:
            for entry in evidence_dir.iterdir():
                if entry.is_dir():
                    to_delete_dirs.append(entry)

        # Pass 2: orphaned + stale signed PDFs at the top level
        for pdf in evidence_dir.glob("*.pdf"):
            mtime = pdf.stat().st_mtime
            sha = hashlib.sha256(pdf.read_bytes()).hexdigest()

            reason = None
            if sha not in valid_shas:
                reason = f"orphaned (no evidence.signed entry for sha256={sha[:12]}…)"
            elif mtime < cutoff:
                age_hours = (datetime.now(tz=timezone.utc).timestamp() - mtime) / 3600
                reason = f"stale ({age_hours:.1f}h old, cutoff is {max_age_hours}h)"

            if reason:
                to_delete_files.append((pdf, reason))

        # Report
        self.stdout.write(self.style.MIGRATE_HEADING("=== cleanup_evidence_dir ==="))
        self.stdout.write(f"  evidence_dir:    {evidence_dir}")
        self.stdout.write(f"  valid SHAs:      {len(valid_shas)}")
        self.stdout.write(f"  files to delete: {len(to_delete_files)}")
        self.stdout.write(f"  dirs  to delete: {len(to_delete_dirs)}")
        self.stdout.write(f"  mode:            {'APPLY' if apply else 'dry-run'}")

        for pdf, reason in to_delete_files:
            self.stdout.write(f"    - {pdf.name}: {reason}")
        for d in to_delete_dirs:
            self.stdout.write(f"    - {d.name}/  (test subdir)")

        if not apply:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING(
                "Pass --apply to actually delete. (Default is dry-run.)"
            ))
            return

        # Apply
        for pdf, _ in to_delete_files:
            pdf.unlink()
        for d in to_delete_dirs:
            shutil.rmtree(d, ignore_errors=True)

        self.stdout.write(self.style.SUCCESS(
            f"=== cleaned: {len(to_delete_files)} file(s) + {len(to_delete_dirs)} dir(s) ==="
        ))
