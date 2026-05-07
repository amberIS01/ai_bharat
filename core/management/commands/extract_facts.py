"""Day-3 management command: extract facts for one or all bidders.

Usage:
    python manage.py extract_facts                  # all bidders that have docs ingested
    python manage.py extract_facts --bidder A
    python manage.py extract_facts --bidder C       # the noisy-GST one
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from core.extraction.facts_extractor import extract_facts_for_bidder
from core.models import Document, Fact


class Command(BaseCommand):
    help = "Extract facts for each (criterion × bidder) pair via Gemini 2.5 Flash."

    def add_arguments(self, parser):
        parser.add_argument(
            "--bidder", type=str, default=None,
            help="Single bidder code (A / B / C). Default: every bidder with documents in DB.",
        )

    def handle(self, *args, **opts):
        if opts.get("bidder"):
            codes = [opts["bidder"].upper().strip()]
        else:
            codes = sorted(
                {c for c in Document.objects.exclude(bidder_code="").values_list("bidder_code", flat=True)}
            )
        if not codes:
            self.stdout.write(self.style.ERROR(
                "No bidder documents in DB. Ingest at least one bundle first."
            ))
            return

        for code in codes:
            self.stdout.write(self.style.MIGRATE_HEADING(f"Bidder {code}"))
            facts = extract_facts_for_bidder(code)
            for f in facts:
                conf = f.ocr_confidence
                badge = "OK " if conf >= 0.85 else "LOW"
                v = (f.value or "(absent)")[:60]
                self.stdout.write(
                    f"  [{badge} {conf:.2f}] {f.criterion.code} {f.criterion.title:<40s} -> {v}"
                )

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"=== Total Fact rows in DB: {Fact.objects.count()} ==="))
