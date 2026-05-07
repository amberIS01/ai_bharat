"""Day-3 management command: run criteria extraction on the (only) ingested RFP.

Usage:
    python manage.py extract_criteria
    python manage.py extract_criteria --rfp-id 7
    python manage.py extract_criteria --replace
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from core.extraction.criteria_extractor import extract_criteria
from core.models import Criterion, Document, DocumentType


class Command(BaseCommand):
    help = "Extract eligibility criteria from an ingested RFP via Gemini 2.5 Pro."

    def add_arguments(self, parser):
        parser.add_argument(
            "--rfp-id", type=int, default=None,
            help="Document.id of the RFP to extract from. Defaults to the most recent RFP.",
        )
        parser.add_argument(
            "--replace", action="store_true",
            help="Delete existing Criterion rows for this RFP before re-extracting.",
        )

    def handle(self, *args, **opts):
        rfp_id = opts.get("rfp_id")
        if rfp_id:
            try:
                rfp = Document.objects.get(id=rfp_id, type=DocumentType.RFP)
            except Document.DoesNotExist as e:
                raise CommandError(f"No RFP with id={rfp_id}") from e
        else:
            rfp = Document.objects.filter(type=DocumentType.RFP).order_by("-created_at").first()
            if not rfp:
                raise CommandError("No RFP in DB. Run `python manage.py ingest_bundle <rfp.pdf>` first.")

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"Extracting criteria from RFP id={rfp.id} ({rfp.original_filename}) ..."
        ))
        criteria = extract_criteria(rfp, replace=opts["replace"])

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"=== {len(criteria)} criteria persisted ==="))
        for c in criteria:
            flag = "M" if c.mandatory else "O"
            cite = c.source_clause_block.block_index if c.source_clause_block else "-"
            self.stdout.write(
                f"  {c.code} [{flag}/{c.type}] {c.title}  (source_block={cite})"
            )
        self.stdout.write("")
        self.stdout.write(f"Total Criterion rows in DB: {Criterion.objects.count()}")
