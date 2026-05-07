"""Day-6 acceptance gate: build + sign an evidence PDF, verify the signature,
confirm the audit-chain head hash is embedded, then tamper-test.

Sequence:
    1. Run `export_evidence` to build + sign + log.
    2. `verify_signed_pdf` says intact & subject matches our demo signer.
    3. Extract text from the signed PDF, confirm the head hash appears.
    4. Tamper test: flip one byte in the middle of the signed PDF on disk,
       re-verify, expect signature_intact=False. Restore, expect green.
    5. Confirm an `evidence.signed` audit entry exists and references the
       same head hash that's embedded in the PDF.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand

from core.audit.signer import verify_signed_pdf
from core.models import AuditEntry


class Command(BaseCommand):
    help = "Day-6 acceptance gate for the signed evidence PDF."

    def handle(self, *args, **opts):
        failures: list[str] = []

        self.stdout.write(self.style.MIGRATE_HEADING("[1/4] Building + signing evidence PDF ..."))
        out_dir = Path(settings.BASE_DIR) / "media" / "evidence" / "verify_day6"
        out_dir.mkdir(parents=True, exist_ok=True)
        # Wipe prior verify_day6 outputs so we operate on a fresh file.
        for old in out_dir.glob("*.pdf"):
            old.unlink()
        call_command("export_evidence", "--output-dir", str(out_dir))

        signed_pdfs = sorted(out_dir.glob("*_signed.pdf"))
        if not signed_pdfs:
            self.stdout.write(self.style.ERROR("export_evidence did not produce a signed PDF."))
            sys.exit(1)
        signed_pdf = signed_pdfs[-1]

        # ------------------------------------------------------------------
        # [2/4] Signature verification
        # ------------------------------------------------------------------
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("[2/4] Verifying signature ..."))
        status = verify_signed_pdf(signed_pdf)
        self.stdout.write(f"  signature_intact: {status['signature_intact']}")
        self.stdout.write(f"  signer_subject:   {status['signer_subject']}")
        self.stdout.write(f"  signed_at:        {status['signed_at']}")
        if not status["signature_intact"]:
            failures.append(
                f"Signature is not intact on a freshly-built PDF. errors={status['errors']!r}"
            )
        if "Praman Demo Officer" not in status["signer_subject"]:
            failures.append(
                f"Unexpected signer subject: {status['signer_subject']!r}"
            )

        # ------------------------------------------------------------------
        # [3/4] Audit head-hash binding — the head hash is embedded as text
        #        in the PDF, so any post-signing edit invalidates the signature.
        # ------------------------------------------------------------------
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("[3/4] Checking head-hash embedding ..."))
        signed_entry = (
            AuditEntry.objects.filter(event_type="evidence.signed")
            .order_by("-seq").first()
        )
        if not signed_entry:
            failures.append("No `evidence.signed` AuditEntry found after export.")
        else:
            head_at_build = signed_entry.payload_json["data"].get("head_hash_at_build")
            self.stdout.write(f"  head_hash_at_build (from audit entry): {head_at_build}")

            # Extract PDF text and confirm the hex hash appears.
            import fitz
            with fitz.open(str(signed_pdf)) as pdf:
                pdf_text = "\n".join(page.get_text() for page in pdf)
            if head_at_build and head_at_build in pdf_text:
                self.stdout.write(self.style.SUCCESS(
                    f"  head hash {head_at_build[:16]}... is embedded in the PDF text ✓"
                ))
            elif head_at_build:
                # WeasyPrint sometimes splits long monospace strings across spans;
                # fall back to checking a substantial prefix.
                if head_at_build[:40] in pdf_text.replace("\n", "").replace(" ", ""):
                    self.stdout.write(self.style.SUCCESS(
                        "  head hash embedded (whitespace-tolerant match) ✓"
                    ))
                else:
                    failures.append(
                        f"Head hash {head_at_build} is NOT in the PDF text — the cryptographic "
                        f"binding is broken."
                    )

        # ------------------------------------------------------------------
        # [4/4] Tamper test
        # ------------------------------------------------------------------
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("[4/4] Tamper test ..."))
        original_bytes = signed_pdf.read_bytes()

        # Flip one byte in the middle of the PDF body (avoid the trailer
        # to keep the file structurally readable so signature-validation
        # actually runs and fails on byte mismatch rather than parse error).
        mid = len(original_bytes) // 2
        # Choose a byte in the body, but try to land on a printable char
        # to actually mutate document content.
        offset = mid
        for delta in range(0, len(original_bytes) // 2):
            cand = mid + delta
            if 0x20 <= original_bytes[cand] <= 0x7E:
                offset = cand
                break
        tampered = bytearray(original_bytes)
        tampered[offset] ^= 0x01  # flip lowest bit
        signed_pdf.write_bytes(bytes(tampered))

        tampered_status = verify_signed_pdf(signed_pdf)
        if tampered_status["signature_intact"]:
            failures.append(
                "Tamper test FAILED: byte was flipped at offset "
                f"{offset} but signature still verifies as intact."
            )
        else:
            self.stdout.write(self.style.SUCCESS(
                f"  byte flip at offset {offset} detected by signature verification ✓"
            ))

        # Restore — write original bytes back.
        signed_pdf.write_bytes(original_bytes)
        restored_status = verify_signed_pdf(signed_pdf)
        if not restored_status["signature_intact"]:
            failures.append(
                "Restore FAILED: signature verification still fails after restoring "
                "the original bytes."
            )
        else:
            self.stdout.write(self.style.SUCCESS("  restored bytes verify intact again ✓"))

        # ------------------------------------------------------------------
        # Verdict
        # ------------------------------------------------------------------
        self.stdout.write("")
        if failures:
            self.stdout.write(self.style.ERROR("=== FAIL ==="))
            for f in failures:
                self.stdout.write(self.style.ERROR(f"  - {f}"))
            sys.exit(1)
        self.stdout.write(self.style.SUCCESS(
            "=== PASS — Day-6 evidence-PDF signing verified end-to-end ==="
        ))
        # Compute an attached SHA-256 so the demo deck can quote it.
        if signed_pdf.exists():
            sha = hashlib.sha256(signed_pdf.read_bytes()).hexdigest()
            self.stdout.write(self.style.SUCCESS(f"Signed PDF SHA-256: {sha}"))
            self.stdout.write(f"Signed PDF: {signed_pdf}")
