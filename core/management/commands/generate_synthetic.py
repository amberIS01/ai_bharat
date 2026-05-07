"""Django management command: generate the Praman synthetic demo dataset.

Usage:
    python manage.py generate_synthetic            # idempotent regeneration
    python manage.py generate_synthetic --clean    # wipe synthetic/ first

Output goes to settings.SYNTHETIC_DIR (default: <BASE_DIR>/synthetic/).
"""

import json

from django.conf import settings
from django.core.management.base import BaseCommand

from core.synthetic.builder import build_all


class Command(BaseCommand):
    help = "Generate the Praman synthetic RFP + 3 bidder bundles."

    def add_arguments(self, parser):
        parser.add_argument(
            "--clean",
            action="store_true",
            help="Delete the synthetic/ output directory before regenerating.",
        )

    def handle(self, *args, **opts):
        out_dir = settings.SYNTHETIC_DIR
        self.stdout.write(self.style.WARNING(
            f"Generating synthetic demo dataset in {out_dir} (clean={opts['clean']}) ..."
        ))
        manifest = build_all(out_dir, clean=opts["clean"])
        self.stdout.write(self.style.SUCCESS("Done. Manifest:"))
        self.stdout.write(json.dumps(manifest, indent=2))
