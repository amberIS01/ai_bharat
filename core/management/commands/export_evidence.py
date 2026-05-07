"""Day-6 management command: build the evidence PDF, sign it, and append a
matching `evidence.signed` audit entry.

Usage:
    python manage.py export_evidence
    python manage.py export_evidence --rfp-id 7 --officer "Demo Officer A"
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.audit import merkle_log
from core.audit.pdf_export import (
    DEFAULT_OFFICER_DESIGNATION,
    DEFAULT_OFFICER_NAME,
    build_evidence_pdf,
)
from core.audit.signer import sign_evidence_pdf
from core.models import (
    Block,
    Document,
    DocumentType,
    OverrideRequest,
    Verdict,
)


class Command(BaseCommand):
    help = "Build + sign the evidence PDF and append `evidence.signed` audit entry."

    def add_arguments(self, parser):
        parser.add_argument("--rfp-id", type=int, default=None)
        parser.add_argument("--officer", type=str, default=DEFAULT_OFFICER_NAME)
        parser.add_argument(
            "--designation", type=str, default=DEFAULT_OFFICER_DESIGNATION,
        )
        parser.add_argument(
            "--output-dir", type=str, default=None,
            help="Directory for the signed PDF. Default: <BASE_DIR>/media/evidence/.",
        )

    def handle(self, *args, **opts):
        if opts["rfp_id"]:
            try:
                rfp = Document.objects.get(id=opts["rfp_id"], type=DocumentType.RFP)
            except Document.DoesNotExist as e:
                raise CommandError(f"No RFP with id={opts['rfp_id']}") from e
        else:
            rfp = (
                Document.objects.filter(type=DocumentType.RFP)
                .order_by("-created_at").first()
            )
            if not rfp:
                raise CommandError("No RFP in DB. Ingest one first.")

        # ---- Day-11 sign-off integrity gates --------------------------------
        # A2: Refuse to sign if any Verdict for this RFP has a whitespace-only
        # rule_id (explainability gap) AND no approved OverrideRequest. The
        # banner on verdict_detail.html is advisory; this is the enforcement.
        bad_verdicts = []
        for v in Verdict.objects.filter(criterion__rfp=rfp).select_related("criterion"):
            if (v.rule_id or "").strip():
                continue
            has_approved_override = OverrideRequest.objects.filter(
                verdict=v, status="APPROVED",
            ).exists()
            if not has_approved_override:
                bad_verdicts.append(v)
        if bad_verdicts:
            sample = bad_verdicts[0]
            raise CommandError(
                f"Refusing to sign: {len(bad_verdicts)} Verdict(s) have empty "
                f"rule_id and no approved override. First example: "
                f"Verdict #{sample.id} ({sample.criterion.code} × Bidder "
                f"{sample.bidder_code}). File an override or fix the Rego rule."
            )

        # A4: Recompute SHA-256 of the RFP file on disk and refuse if it
        # differs from the stored Document.sha256. Same for any Bidder
        # Document that has Blocks cited by a Verdict in this matrix.
        cited_doc_ids = set()
        cited_doc_ids.add(rfp.id)
        cited_doc_ids.update(
            Block.objects
            .filter(verdicts_citing_this_block__criterion__rfp=rfp)
            .values_list("document_id", flat=True)
        )
        for doc in Document.objects.filter(id__in=cited_doc_ids):
            if not doc.file or not Path(doc.file.path).exists():
                continue  # Document file gone; let the PDF build phase report it.
            on_disk_sha = hashlib.sha256(Path(doc.file.path).read_bytes()).hexdigest()
            if on_disk_sha != doc.sha256:
                raise CommandError(
                    f"RFP/bidder file integrity broken — Document #{doc.id} "
                    f"({doc.original_filename}) on disk has SHA-256 "
                    f"{on_disk_sha[:12]}…, stored {doc.sha256[:12]}…. "
                    f"Re-ingest with --force-cascade before signing."
                )

        # ---- end integrity gates --------------------------------------------

        out_dir = Path(opts["output_dir"]) if opts["output_dir"] else (
            Path(settings.BASE_DIR) / "media" / "evidence"
        )
        out_dir.mkdir(parents=True, exist_ok=True)

        ts_token = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        unsigned_path = out_dir / f"rfp{rfp.id}_evidence_{ts_token}_unsigned.pdf"
        signed_path = out_dir / f"rfp{rfp.id}_evidence_{ts_token}_signed.pdf"

        # 1) Build unsigned PDF
        self.stdout.write(self.style.MIGRATE_HEADING("[1/3] Rendering evidence PDF ..."))
        head_at_build = merkle_log.head()
        head_hash_at_build = head_at_build.this_hash if head_at_build else "(empty chain)"
        build_evidence_pdf(
            rfp,
            unsigned_path,
            officer_name=opts["officer"],
            officer_designation=opts["designation"],
        )
        self.stdout.write(f"    unsigned PDF: {unsigned_path} ({unsigned_path.stat().st_size} bytes)")

        # 2) Sign with pyHanko
        self.stdout.write(self.style.MIGRATE_HEADING("[2/3] Signing PDF (pyHanko PAdES-B, self-signed) ..."))
        sign_evidence_pdf(
            unsigned_path, signed_path,
            signer_label=f"{opts['officer']} — {opts['designation']}",
        )
        signed_size = signed_path.stat().st_size
        self.stdout.write(f"    signed PDF:   {signed_path} ({signed_size} bytes)")

        # 3) Audit log entry — the "evidence.signed" event closes the loop:
        #    the audit chain itself records that an evidence PDF was generated
        #    and signed, embedding the head hash that lives inside the PDF.
        self.stdout.write(self.style.MIGRATE_HEADING("[3/3] Appending evidence.signed audit entry ..."))
        signed_pdf_sha256 = hashlib.sha256(signed_path.read_bytes()).hexdigest()
        entry = merkle_log.append(
            "evidence.signed",
            "evidence",
            None,
            {
                "rfp_id": rfp.id,
                "rfp_filename": rfp.original_filename,
                "officer": opts["officer"],
                "designation": opts["designation"],
                "head_hash_at_build": head_hash_at_build,
                "signed_pdf_path": str(signed_path),
                "signed_pdf_sha256": signed_pdf_sha256,
                "signed_pdf_bytes": signed_size,
            },
        )

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=== Evidence PDF signed ==="))
        self.stdout.write(f"  output:           {signed_path}")
        self.stdout.write(f"  signed PDF SHA256: {signed_pdf_sha256}")
        self.stdout.write(f"  audit entry seq:  {entry.seq}")
        self.stdout.write(f"  new chain head:   {entry.this_hash}")
        self.stdout.write(self.style.WARNING(
            "Demo signature — production deployment uses CCA India Class-3 DSC via PKCS#11."
        ))
