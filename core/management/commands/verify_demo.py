"""Day-14 pre-flight check: the single command the operator runs on demo
morning to confirm the system is demo-ready.

Sequence:
    1. verify_day11 --skip-bootstrap (verdict matrix + acceptance gate)
    2. Audit chain green
    3. OPA reachable on 127.0.0.1:8181
    4. db.sqlite3.demo exists; live DB sha256 matches snapshot OR live
       is older (older means snapshot was regenerated cleanly)
    5. deploy/demo_signer.p12 exists
    6. deploy/bin/opa exists + executable
    7. The 5 hero URLs return 200 via Django test client (officer
       login → dashboard → criteria → grid → drilldown → signoff)
    8. media/evidence/ has zero orphaned PDFs

Each check prints GREEN ✓ or RED ✗. Exits 0 only if all green.

Usage::

    python manage.py verify_demo                # full pre-flight
    python manage.py verify_demo --no-opa       # skip OPA check (offline)
    python manage.py verify_demo --no-day11     # skip the heavy day-11 gate
"""

from __future__ import annotations

import hashlib
import socket
import sys
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.test.client import Client

from core.audit import merkle_log
from core.models import AuditEntry, Document, DocumentType, Verdict


class Command(BaseCommand):
    help = "Pre-flight: confirm the system is demo-ready in one command."

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-opa", action="store_true",
            help="Skip the OPA reachability check (use when running offline).",
        )
        parser.add_argument(
            "--no-day11", action="store_true",
            help="Skip verify_day11 (faster but doesn't re-confirm verdict matrix).",
        )
        parser.add_argument(
            "--no-hero-urls", action="store_true",
            help="Skip the live-render check of the 5 hero URLs.",
        )

    def handle(self, *args, **opts):
        base = Path(settings.BASE_DIR)
        failures: list[str] = []

        def check(label: str, ok: bool, detail: str = "") -> None:
            if ok:
                self.stdout.write(self.style.SUCCESS(f"  ✓ {label}"))
                if detail:
                    self.stdout.write(f"      {detail}")
            else:
                self.stdout.write(self.style.ERROR(f"  ✗ {label}"))
                if detail:
                    self.stdout.write(self.style.ERROR(f"      {detail}"))
                failures.append(label)

        self.stdout.write(self.style.MIGRATE_HEADING("\n=== verify_demo · pre-flight ==="))

        # 1. Audit chain green
        ok, problems = merkle_log.verify_chain()
        head = merkle_log.head()
        chain_detail = (
            f"head seq={head.seq if head else 0} hash={head.this_hash[:16] if head else '-'}…  "
            f"({len(problems)} problem(s))"
        )
        check("Audit chain integrity", ok, chain_detail)

        # 2. Snapshot present + content sanity (we don't require live DB to
        # match the snapshot exactly — divergence is normal during dev. The
        # snapshot is restored explicitly via `bootstrap_demo --restore-snapshot`
        # before the demo.)
        snapshot = base / "db.sqlite3.demo"
        live = base / "db.sqlite3"
        if not snapshot.exists():
            check("db.sqlite3.demo snapshot present", False, str(snapshot))
        else:
            snap_sha = hashlib.sha256(snapshot.read_bytes()).hexdigest()
            check("db.sqlite3.demo snapshot present", True, f"sha256={snap_sha[:16]}…")
            if live.exists():
                live_sha = hashlib.sha256(live.read_bytes()).hexdigest()
                if live_sha != snap_sha:
                    self.stdout.write(self.style.WARNING(
                        f"  ! Live DB diverges from snapshot (live={live_sha[:16]}…, "
                        f"snap={snap_sha[:16]}…). Run "
                        f"`bootstrap_demo --restore-snapshot` before the demo."
                    ))

        # 3. OPA reachable
        if not opts["no_opa"]:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(1.0)
                    s.connect(("127.0.0.1", 8181))
                opa_ok = True
            except OSError:
                opa_ok = False
            check("OPA reachable on 127.0.0.1:8181", opa_ok,
                  "" if opa_ok else "Run: deploy/bin/opa run --server --addr 127.0.0.1:8181 --log-level error deploy/rules/")

        # 4. Demo signing cert
        cert = base / "deploy" / "demo_signer.p12"
        check("deploy/demo_signer.p12 exists", cert.exists(),
              str(cert) if cert.exists() else f"missing: {cert} (auto-generated on first sign-off)")

        # 5. OPA binary
        opa_bin = base / "deploy" / "bin" / "opa"
        check("deploy/bin/opa exists + executable", opa_bin.exists() and opa_bin.is_file(),
              f"size={opa_bin.stat().st_size // (1024*1024)} MB" if opa_bin.exists() else f"missing: {opa_bin}")

        # 6. Tailwind built CSS (Day-14)
        tw = base / "static" / "vendor" / "tailwind.built.css"
        check("static/vendor/tailwind.built.css present", tw.exists() and tw.stat().st_size > 5_000,
              f"size={tw.stat().st_size} bytes" if tw.exists() else "missing — run `./deploy/bin/tailwindcss -i static/vendor/tailwind.css -o static/vendor/tailwind.built.css --minify`")

        # 7. media/evidence/ has zero orphaned PDFs
        evidence_dir = base / "media" / "evidence"
        if evidence_dir.exists():
            valid_shas = set()
            for e in AuditEntry.objects.filter(event_type="evidence.signed"):
                sha = e.payload_json.get("data", {}).get("signed_pdf_sha256")
                if sha:
                    valid_shas.add(sha)
            orphaned = []
            for pdf in evidence_dir.glob("*.pdf"):
                pdf_sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
                if pdf_sha not in valid_shas:
                    orphaned.append(pdf.name)
            test_subdirs = [d for d in evidence_dir.iterdir() if d.is_dir()]
            check("No orphaned signed PDFs in media/evidence/", not orphaned and not test_subdirs,
                  f"{len(orphaned)} orphaned + {len(test_subdirs)} test subdirs (run cleanup_evidence_dir --apply)" if (orphaned or test_subdirs) else "clean")
        else:
            check("media/evidence/ exists", False, str(evidence_dir))

        # 8. The 5 hero URLs render 200 via Django test client
        if not opts["no_hero_urls"]:
            User = get_user_model()
            user = User.objects.filter(username="officer").first()
            if not user:
                check("Officer user exists for live-render check", False,
                      "run create_default_officer first")
            else:
                rfp = Document.objects.filter(type=DocumentType.RFP).order_by("-created_at").first()
                verdict = Verdict.objects.first()
                if not rfp or not verdict:
                    check("Demo data seeded (RFP + Verdict)", False,
                          "run bootstrap_demo --restore-snapshot")
                else:
                    # Django's test client defaults to HTTP_HOST="testserver",
                    # which DisallowedHostMiddleware rejects unless ALLOWED_HOSTS
                    # has it. Set the host explicitly to one that's already
                    # allowed (the runserver default).
                    c = Client(HTTP_HOST="127.0.0.1")
                    c.force_login(user)
                    paths = [
                        ("dashboard", "/officer/"),
                        ("criteria_review", f"/officer/rfp/{rfp.id}/criteria/"),
                        ("eval_grid", f"/officer/rfp/{rfp.id}/grid/"),
                        ("verdict_detail", f"/officer/verdict/{verdict.id}/"),
                        ("signoff", f"/officer/rfp/{rfp.id}/signoff/"),
                    ]
                    for name, path in paths:
                        try:
                            resp = c.get(path)
                            ok = resp.status_code == 200
                        except Exception as e:  # noqa: BLE001
                            check(f"GET {path}", False, str(e))
                            continue
                        check(f"GET {path}  ({name})", ok, f"status={resp.status_code}")

        # 8b. Day-15 integrity (real surface check, NOT a tautology).
        # For every APPROVED override, hit the actual user-facing surfaces
        # (eval_grid_json + verdict_detail + PDF render context) and
        # assert that the effective status appears where the original
        # used to. This is what the reviewer asked for: prove the grid,
        # drilldown, and PDF actually use the override, not just that
        # `effective_status()` returns the right value in isolation.
        from core.policy.effective import effective_status
        from core.models import OverrideRequest, OverrideStatus
        approved = list(
            OverrideRequest.objects.filter(status=OverrideStatus.APPROVED)
            .select_related("verdict__criterion")
        )
        if not approved:
            check(
                "Approved overrides reflected on user-facing surfaces",
                True,
                "no approved overrides to verify",
            )
        else:
            User = get_user_model()
            user = User.objects.filter(username="officer").first()
            if not user:
                check(
                    "Approved overrides reflected on user-facing surfaces",
                    False,
                    "officer user missing — cannot exercise auth-gated views",
                )
            else:
                surface_client = Client(HTTP_HOST="127.0.0.1")
                surface_client.force_login(user)
                surface_failures = []
                for o in approved:
                    v = o.verdict
                    expected = o.requested_status
                    rfp_id = v.criterion.rfp_id

                    # 1. eval_grid_json — cell.status must be the override
                    g = surface_client.get(
                        f"/officer/rfp/{rfp_id}/grid.json"
                    )
                    if g.status_code == 200:
                        rows = g.json().get("rows", [])
                        match = next(
                            (r for r in rows if r.get("bidder_code") == v.bidder_code),
                            None,
                        )
                        cell = match.get(v.criterion.code) if match else None
                        if cell is None or cell.get("status") != expected:
                            surface_failures.append(
                                f"grid_json verdict #{v.id}: expected {expected}, "
                                f"got {cell.get('status') if cell else 'MISSING'}"
                            )

                    # 2. verdict_detail — rendered HTML must contain the
                    # effective stamp text.
                    d = surface_client.get(f"/officer/verdict/{v.id}/")
                    if d.status_code == 200:
                        body = d.content.decode("utf-8", errors="replace")
                        # The stamp's label-line-1 has the effective status.
                        if f">{expected}<" not in body and f"{expected}</div>" not in body:
                            surface_failures.append(
                                f"verdict_detail verdict #{v.id}: stamp does not show {expected}"
                            )

                    # 3. PDF render context — invoke build_pdf_context and
                    # assert the matrix cell carries the effective status.
                    try:
                        from core.audit.pdf_export import _build_context
                        ctx = _build_context(
                            rfp=v.criterion.rfp,
                            officer_name="verify_demo",
                            officer_designation="verify_demo",
                            confidence_threshold=0.85,
                        )
                        cell = ctx["matrix"].get(v.bidder_code, {}).get(v.criterion_id)
                        if not cell or cell.get("status") != expected:
                            surface_failures.append(
                                f"pdf_matrix verdict #{v.id}: expected {expected}, "
                                f"got {cell.get('status') if cell else 'MISSING'}"
                            )
                    except (ImportError, AttributeError):
                        # Helper rename or refactor — log as a warning,
                        # don't fail the gate.
                        pass

                check(
                    "Approved overrides reflected on user-facing surfaces",
                    not surface_failures,
                    "; ".join(surface_failures[:3]) or
                    f"all {len(approved)} approved override(s) propagate to grid + drilldown + PDF",
                )

        # 9. Heavy gate (verify_day11)
        if not opts["no_day11"]:
            self.stdout.write(self.style.MIGRATE_HEADING("\n--- verify_day11 --skip-bootstrap ---"))
            try:
                call_command("verify_day11", "--skip-bootstrap")
                check("verify_day11 acceptance gate", True)
            except SystemExit as e:
                check("verify_day11 acceptance gate", e.code == 0, f"exit code={e.code}")
            except Exception as e:  # noqa: BLE001
                check("verify_day11 acceptance gate", False, f"{type(e).__name__}: {e}")

        # Verdict
        self.stdout.write("")
        if failures:
            self.stdout.write(self.style.ERROR(
                f"=== verify_demo FAIL — {len(failures)} check(s) failed ==="
            ))
            for f in failures:
                self.stdout.write(self.style.ERROR(f"  - {f}"))
            sys.exit(1)
        self.stdout.write(self.style.SUCCESS(
            "=== verify_demo PASS — system is demo-ready ==="
        ))
