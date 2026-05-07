"""Convenience: print the command to start OPA in the foreground.

We don't actually exec OPA from this command because Django would then
hold the foreground forever and any subsequent management command in the
same shell would be blocked. Instead, this prints the exact command for
the user to run in a separate terminal.

Usage:
    python manage.py start_opa
"""

from __future__ import annotations

import os
import socket
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings
from django.core.management.base import BaseCommand

BASE_DIR: Path = settings.BASE_DIR
OPA_BINARY = BASE_DIR / "deploy" / "bin" / "opa"
RULES_DIR = BASE_DIR / "deploy" / "rules"


def _is_opa_up(url: str, timeout: float = 0.5) -> bool:
    parsed = urlparse(url)
    host, port = parsed.hostname or "127.0.0.1", parsed.port or 8181
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        try:
            s.connect((host, port))
            return True
        except OSError:
            return False


class Command(BaseCommand):
    help = "Print the command line to start the OPA HTTP server with Praman rules."

    def handle(self, *args, **opts):
        if not OPA_BINARY.exists():
            self.stdout.write(self.style.ERROR(
                f"OPA binary not found at {OPA_BINARY}. Run:\n"
                "  curl -sSL -o deploy/bin/opa https://openpolicyagent.org/downloads/latest/opa_linux_amd64\n"
                "  chmod +x deploy/bin/opa"
            ))
            return

        url = settings.OPA_URL
        addr = f"{urlparse(url).hostname or '127.0.0.1'}:{urlparse(url).port or 8181}"

        cmd = f"./deploy/bin/opa run --server --addr {addr} {RULES_DIR.relative_to(BASE_DIR)}/"

        if _is_opa_up(url):
            self.stdout.write(self.style.SUCCESS(
                f"OPA is already responding at {url}.\nRules directory: {RULES_DIR}"
            ))
        else:
            self.stdout.write(self.style.WARNING(
                f"OPA not reachable at {url}. Run this in another terminal:\n"
            ))
            self.stdout.write(f"  cd {BASE_DIR}")
            self.stdout.write(f"  {cmd}")
            self.stdout.write("")
            self.stdout.write("Then re-run `python manage.py evaluate_verdicts`.")
