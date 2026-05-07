"""Generate the self-signed PKCS#12 used to sign the Day-6 evidence PDF.

Idempotent: re-running on an existing file is a no-op.
The `.p12` glob is gitignored.
"""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand

from core.audit.cert_gen import (
    DEFAULT_P12_PATH,
    DEFAULT_PASSWORD,
    generate_demo_signing_cert,
    load_demo_cert,
)


class Command(BaseCommand):
    help = "Generate (or report) the demo X.509 signing cert at deploy/demo_signer.p12."

    def add_arguments(self, parser):
        parser.add_argument(
            "--regenerate", action="store_true",
            help="Delete the existing PKCS#12 file and create a fresh one.",
        )

    def handle(self, *args, **opts):
        path = settings.BASE_DIR / DEFAULT_P12_PATH
        existed = path.exists()
        if opts["regenerate"] and existed:
            path.unlink()
            self.stdout.write(self.style.WARNING(f"Deleted existing {path}."))
            existed = False

        result = generate_demo_signing_cert(path, password=DEFAULT_PASSWORD)
        action = "Already present" if existed else "Generated"
        self.stdout.write(self.style.SUCCESS(f"{action}: {result}"))

        # Print cert summary so the operator can sanity-check.
        _, cert, _ = load_demo_cert(result, password=DEFAULT_PASSWORD)
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Certificate"))
        self.stdout.write(f"  subject:    {cert.subject.rfc4514_string()}")
        self.stdout.write(f"  serial:     {cert.serial_number}")
        self.stdout.write(f"  not_before: {cert.not_valid_before_utc.isoformat()}")
        self.stdout.write(f"  not_after:  {cert.not_valid_after_utc.isoformat()}")
        self.stdout.write("")
        self.stdout.write(self.style.WARNING(
            "DEMO USE ONLY — production deployment must use CCA India Class-3 DSC "
            "via PKCS#11 (eMudhra ProxKey, ePass2003, etc)."
        ))
