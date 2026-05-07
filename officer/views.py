"""Officer-facing views.

Day 7 builds the BACKEND plumbing — URL handlers, form processing, the
plumbing that ties Day 1-6's data layer to a browser. The TEMPLATES that
back these views are minimal here; Day 9-10 polishes them with Tailwind +
HTMX swaps + AG-Grid + react-pdf-highlighter.

HTMX awareness (from `django-htmx`'s middleware):

    * `request.htmx` is True when the request was issued by an HTMX swap.
    * Views that have an HTMX path return a *fragment* (just the bit that
      replaces the swap target) — saves a full re-render and keeps URL
      bars stable.
    * Views that lack an HTMX-only path just render the full template; HTMX
      can still consume them via `hx-target="body" hx-swap="innerHTML"`.

Auth:

    * Every view requires login; the demo user is set up by
      `python manage.py create_default_officer`.
    * `LOGIN_REDIRECT_URL` defaults to `/accounts/profile/`; we want the
      dashboard, so we set it explicitly in settings (Day-7 wiring).
"""

from __future__ import annotations

import hashlib
import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.management import call_command
from django.http import (
    FileResponse,
    Http404,
    HttpRequest,
    HttpResponse,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from core.audit import merkle_log
from core.models import (
    Criterion,
    Document,
    DocumentType,
    Fact,
    OverrideRequest,
    Verdict,
)
from core.policy.effective import (
    effective_override,
    effective_status,
)
from core.policy.opa_client import opa_health_check
from officer.forms import (
    BidderBundleUploadForm,
    CriterionInlineEditForm,
    OverrideRequestForm,
    RFPUploadForm,
)
from officer.rendering import build_evidence_payload, render_page_png

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bidder_codes_for_rfp(rfp: Document) -> list[str]:
    """Return the sorted distinct bidder codes that have docs in DB.

    The synthetic dataset has only one RFP, so for the demo this is fine.
    Day-12 polish will introduce a Bid model that links Bidders to RFPs
    explicitly.
    """
    return sorted({
        c for c in Document.objects.exclude(bidder_code="")
        .values_list("bidder_code", flat=True)
    })


def _signed_pdf_path_for_rfp(rfp: Document) -> Path | None:
    """Return the most recent signed evidence PDF for `rfp`, or None."""
    out_dir = Path(settings.BASE_DIR) / "media" / "evidence"
    if not out_dir.exists():
        return None
    matches = sorted(
        out_dir.glob(f"rfp{rfp.id}_evidence_*_signed.pdf"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return matches[0] if matches else None


def _signed_pdf_history_for_rfp(rfp: Document) -> list[dict]:
    """A8: return every signed evidence PDF for `rfp`, newest first.

    Each row carries the corresponding `evidence.signed` AuditEntry's
    head hash + seq so a CAG reviewer can see exactly what chain state
    each PDF sealed. Older PDFs are tagged with how many audit events
    have been appended since they were sealed.
    """
    from core.models import AuditEntry

    out_dir = Path(settings.BASE_DIR) / "media" / "evidence"
    if not out_dir.exists():
        return []
    paths = sorted(
        out_dir.glob(f"rfp{rfp.id}_evidence_*_signed.pdf"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not paths:
        return []

    # Map signed-PDF SHA-256 → AuditEntry so we can recover the chain head
    # at sign-time. The export_evidence command stores `signed_pdf_sha256`
    # in payload_json["data"]["signed_pdf_sha256"].
    entries_by_sha: dict[str, AuditEntry] = {}
    for e in AuditEntry.objects.filter(event_type="evidence.signed").order_by("-seq"):
        sha = e.payload_json.get("data", {}).get("signed_pdf_sha256")
        if sha and sha not in entries_by_sha:
            entries_by_sha[sha] = e

    head = merkle_log.head()
    head_seq = head.seq if head else 0

    rows = []
    for i, p in enumerate(paths):
        sha = hashlib.sha256(p.read_bytes()).hexdigest()
        entry = entries_by_sha.get(sha)
        seq_at_sign = entry.seq if entry else None
        events_since = (head_seq - seq_at_sign) if seq_at_sign is not None else None
        rows.append({
            "filename": p.name,
            "size_bytes": p.stat().st_size,
            "sha256": sha,
            "modified": datetime.fromtimestamp(
                p.stat().st_mtime, tz=timezone.utc
            ).isoformat(),
            "is_current": i == 0,
            "head_hash_at_sign": (
                entry.payload_json.get("data", {}).get("head_hash_at_build")
                if entry else None
            ),
            "seq_at_sign": seq_at_sign,
            "events_since_sign": events_since,
        })
    return rows


def _sha256_uploaded(uploaded) -> str:
    h = hashlib.sha256()
    for chunk in uploaded.chunks():
        h.update(chunk)
    uploaded.seek(0)
    return h.hexdigest()


def _save_upload_to_tmp(uploaded) -> Path:
    """Stream a Django UploadedFile to a temp file for ingest_bundle to consume."""
    suffix = Path(uploaded.name).suffix
    fd = tempfile.NamedTemporaryFile(prefix="praman_upload_", suffix=suffix, delete=False)
    try:
        for chunk in uploaded.chunks():
            fd.write(chunk)
        return Path(fd.name)
    finally:
        fd.close()


def _ingest_path(path: Path, *, bidder_code: str | None = None) -> None:
    """Wrap the `ingest_bundle` command. Pass --bidder-code if provided."""
    args = ["ingest_bundle", str(path)]
    if bidder_code:
        args += ["--bidder-code", bidder_code]
    call_command(*args)


# ---------------------------------------------------------------------------
# Dashboard / index
# ---------------------------------------------------------------------------

@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    import json as _json
    from django.db.models import Count

    rfps = Document.objects.filter(type=DocumentType.RFP).order_by("-created_at")
    head = merkle_log.head()
    opa_ok, opa_msg = opa_health_check()

    # Verdict distribution across all RFPs — counted through
    # effective_status() so approved overrides flip the bar.
    # Day-15 fix #2: dashboard counts must match grid/drilldown/PDF.
    verdict_counts: dict[str, int] = {"PASS": 0, "FAIL": 0, "ABSTAIN": 0}
    for v in Verdict.objects.all():
        eff = effective_status(v)
        verdict_counts[eff] = verdict_counts.get(eff, 0) + 1
    total_verdicts = sum(verdict_counts.values()) or 1
    verdict_distribution = {
        status: {
            "n": verdict_counts.get(status, 0),
            "pct": round(100 * verdict_counts.get(status, 0) / total_verdicts, 1),
        }
        for status in ("PASS", "FAIL", "ABSTAIN")
    }
    verdict_distribution["total"] = sum(verdict_counts.values())

    # Last 12 audit entries for the hero hashviz (same shape as signoff).
    audit_recent = merkle_log.recent(limit=12)
    audit_chain_for_viz = _json.dumps([
        {
            "seq": e.seq,
            "ts": e.ts.isoformat(),
            "event_type": e.event_type,
            "entity": e.payload_json.get("entity", ""),
            "entity_id": e.payload_json.get("entity_id"),
            "hash": e.this_hash,
        }
        for e in reversed(audit_recent)
    ])

    bidder_count = (
        Document.objects.exclude(bidder_code="")
        .values("bidder_code").distinct().count()
    )

    ctx = {
        "rfps": rfps,
        "audit_head_hash": head.this_hash if head else "(empty chain — no events yet)",
        "audit_count": head.seq if head else 0,
        "audit_chain_for_viz": audit_chain_for_viz,
        "verdict_distribution": verdict_distribution,
        "rfp_count": rfps.count(),
        "bidder_count": bidder_count,
        "opa_ok": opa_ok,
        "opa_msg": opa_msg,
        "officer_username": request.user.username,
    }
    return render(request, "officer/dashboard.html", ctx)


# ---------------------------------------------------------------------------
# Upload — RFP
# ---------------------------------------------------------------------------

@login_required
def upload_tender(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = RFPUploadForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded = form.cleaned_data["rfp_file"]
            sha = _sha256_uploaded(uploaded)
            tmp = _save_upload_to_tmp(uploaded)
            try:
                _ingest_path(tmp)
            finally:
                tmp.unlink(missing_ok=True)
            rfp = Document.objects.filter(sha256=sha, type=DocumentType.RFP).first()
            if rfp is None:
                messages.error(
                    request,
                    "Ingest produced no RFP Document — the file may not be a "
                    "parsable PDF, or the type-inference classified it as a "
                    "bidder doc. Check the filename and try again.",
                )
                return render(request, "officer/upload_tender.html", {"form": form})
            messages.success(
                request,
                f"RFP ingested as Document #{rfp.id} ({rfp.original_filename}).",
            )
            return redirect("officer:upload_bidder", rfp_id=rfp.id)
    else:
        form = RFPUploadForm()
    return render(request, "officer/upload_tender.html", {"form": form})


# ---------------------------------------------------------------------------
# Upload — bidder
# ---------------------------------------------------------------------------

@login_required
def upload_bidder(request: HttpRequest, rfp_id: int) -> HttpResponse:
    rfp = get_object_or_404(Document, id=rfp_id, type=DocumentType.RFP)
    if request.method == "POST":
        form = BidderBundleUploadForm(request.POST, request.FILES)
        if form.is_valid():
            tmp = _save_upload_to_tmp(form.cleaned_data["bundle_file"])
            try:
                _ingest_path(tmp, bidder_code=form.cleaned_data["bidder_code"])
            finally:
                tmp.unlink(missing_ok=True)
            messages.success(
                request,
                f"Bidder {form.cleaned_data['bidder_code']} bundle ingested.",
            )
            return redirect("officer:upload_bidder", rfp_id=rfp.id)
    else:
        form = BidderBundleUploadForm()
    return render(request, "officer/upload_bidder.html", {
        "form": form,
        "rfp": rfp,
        "bidder_codes": _bidder_codes_for_rfp(rfp),
    })


# ---------------------------------------------------------------------------
# Criteria review (after extract_criteria)
# ---------------------------------------------------------------------------

@login_required
def criteria_review(request: HttpRequest, rfp_id: int) -> HttpResponse:
    rfp = get_object_or_404(Document, id=rfp_id, type=DocumentType.RFP)
    criteria = list(Criterion.objects.filter(rfp=rfp).order_by("code"))
    return render(request, "officer/criteria_review.html", {
        "rfp": rfp,
        "criteria": criteria,
        "bidder_codes": _bidder_codes_for_rfp(rfp),
    })


@login_required
@require_POST
def criterion_inline_edit(request: HttpRequest, criterion_id: int) -> HttpResponse:
    """HTMX-driven single-field edit of a Criterion row.

    Validated by `CriterionInlineEditForm` (whitelist: title /
    requirement_text / mandatory). Code and type are not editable —
    `type` drives Rego dispatch, and `code` is the unique identifier.

    Renders the row partial back to the client so HTMX swaps the row
    in place without a full page reload. Audit signal fires on save.
    """
    criterion = get_object_or_404(Criterion, id=criterion_id)
    form = CriterionInlineEditForm(request.POST)
    if not form.is_valid():
        return render(
            request,
            "officer/_partials/criterion_row.html",
            {"c": criterion, "edit_error": "; ".join(
                f"{k}: {','.join(v)}" for k, v in form.errors.items()
            )},
            status=200,  # 200 so HTMX still swaps; the partial shows the error
        )
    field = form.cleaned_data["field"]
    setattr(criterion, field, form.cleaned_data["value"])
    criterion.save(update_fields=[field])
    return render(
        request,
        "officer/_partials/criterion_row.html",
        {"c": criterion},
    )


@login_required
@require_POST
def run_extraction(request: HttpRequest, rfp_id: int) -> HttpResponse:
    rfp = get_object_or_404(Document, id=rfp_id, type=DocumentType.RFP)
    try:
        call_command("extract_criteria", "--rfp-id", str(rfp.id))
        if _bidder_codes_for_rfp(rfp):
            call_command("extract_facts")
    except Exception as e:  # noqa: BLE001
        messages.error(request, f"Extraction failed: {type(e).__name__}: {e}")
        return redirect("officer:criteria_review", rfp_id=rfp.id)
    messages.success(request, "Criteria + facts extracted.")
    return redirect("officer:criteria_review", rfp_id=rfp.id)


# ---------------------------------------------------------------------------
# Evaluation grid
# ---------------------------------------------------------------------------

@login_required
@require_POST
def run_evaluation(request: HttpRequest, rfp_id: int) -> HttpResponse:
    rfp = get_object_or_404(Document, id=rfp_id, type=DocumentType.RFP)
    ok, msg = opa_health_check()
    if not ok:
        messages.error(request, msg)
        return redirect("officer:eval_grid", rfp_id=rfp.id)
    try:
        call_command("evaluate_verdicts")
    except Exception as e:  # noqa: BLE001
        messages.error(request, f"Evaluation failed: {type(e).__name__}: {e}")
    else:
        messages.success(request, "Verdicts evaluated.")
    return redirect("officer:eval_grid", rfp_id=rfp.id)


@login_required
def eval_grid(request: HttpRequest, rfp_id: int) -> HttpResponse:
    rfp = get_object_or_404(Document, id=rfp_id, type=DocumentType.RFP)
    criteria = list(Criterion.objects.filter(rfp=rfp).order_by("code"))
    bidder_codes = _bidder_codes_for_rfp(rfp)

    matrix: dict[str, dict[int, Verdict | None]] = {
        b: {c.id: None for c in criteria} for b in bidder_codes
    }
    latest_verdict_ts = None
    for v in (
        Verdict.objects.filter(criterion__rfp=rfp)
        .select_related("criterion")
        .prefetch_related("evidence_refs")
    ):
        if v.bidder_code in matrix and v.criterion_id in matrix[v.bidder_code]:
            matrix[v.bidder_code][v.criterion_id] = v
        if latest_verdict_ts is None or v.decided_at > latest_verdict_ts:
            latest_verdict_ts = v.decided_at

    # A3: surface a stale-verdict banner if a Criterion was edited after
    # the most recent Verdict was emitted. Catches the inline-edit footgun
    # where a juror toggles mandatory and the eval grid still shows the
    # OLD verdict. Compared as Django datetime objects (both UTC).
    verdicts_stale = False
    if latest_verdict_ts is not None:
        latest_crit_change = max(
            (c.created_at for c in criteria), default=None
        )
        # Criterion has no `updated_at`; falling back to created_at would
        # only catch new criteria. The cleaner signal is to look in the
        # audit log for `criterion.updated` events more recent than the
        # newest verdict.
        from core.models import AuditEntry
        recent_crit_update = (
            AuditEntry.objects
            .filter(event_type="criterion.updated")
            .filter(payload_json__data__rfp_id=rfp.id)
            .order_by("-ts").only("ts").first()
        )
        if recent_crit_update and recent_crit_update.ts > latest_verdict_ts:
            verdicts_stale = True

    summary = []
    for code in bidder_codes:
        row = {"PASS": 0, "FAIL": 0, "ABSTAIN": 0, "MISSING": 0}
        for c in criteria:
            v = matrix[code][c.id]
            if v is None:
                row["MISSING"] += 1
            else:
                # Day-15 fix #7: legend reflects effective status
                eff = effective_status(v)
                row[eff] = row.get(eff, 0) + 1
        summary.append({"bidder_code": code, **row})

    head = merkle_log.head()
    opa_ok, opa_msg = opa_health_check()
    return render(request, "officer/eval_grid.html", {
        "rfp": rfp,
        "criteria": criteria,
        "bidder_codes": bidder_codes,
        "matrix": matrix,
        "summary": summary,
        "audit_head_hash": head.this_hash if head else "",
        "verdicts_stale": verdicts_stale,
        "opa_ok": opa_ok,
        "opa_msg": opa_msg,
    })


# ---------------------------------------------------------------------------
# Verdict drill-down + override
# ---------------------------------------------------------------------------

@login_required
def verdict_detail(request: HttpRequest, verdict_id: int) -> HttpResponse:
    verdict = get_object_or_404(
        Verdict.objects.select_related("criterion").prefetch_related("evidence_refs"),
        id=verdict_id,
    )
    fact = Fact.objects.filter(
        criterion=verdict.criterion, bidder_code=verdict.bidder_code
    ).first()
    overrides = list(OverrideRequest.objects.filter(verdict=verdict).order_by("-ts"))
    # `verdict.rule_id` is non-blank for every Rego rule we author, but a
    # malformed rule (or a future contributor) could leave it whitespace-only.
    # Truthy check via Django's `{% if %}` would NOT catch "   "; do the strip
    # here so the template's empty-rule_id banner triggers correctly.
    has_rule_id = bool((verdict.rule_id or "").strip())
    # Day-15 fix #7: derive effective status from the latest APPROVED
    # override. The drilldown shows BOTH so the officer sees the full
    # provenance: Rego said X, officer overrode to Y.
    eff_status = effective_status(verdict)
    eff_override = effective_override(verdict)
    return render(request, "officer/verdict_detail.html", {
        "verdict": verdict,
        "fact": fact,
        "evidence_blocks": list(verdict.evidence_refs.select_related("document").all()),
        "overrides": overrides,
        "form": OverrideRequestForm(),
        "has_rule_id": has_rule_id,
        "effective_status": eff_status,
        "effective_override": eff_override,
        "is_overridden": eff_status != verdict.status,
    })


@login_required
@require_POST
def verdict_override(request: HttpRequest, verdict_id: int) -> HttpResponse:
    verdict = get_object_or_404(Verdict, id=verdict_id)
    form = OverrideRequestForm(request.POST)
    if not form.is_valid():
        messages.error(
            request,
            "Override rejected: " + "; ".join(
                f"{k}: {','.join(v)}" for k, v in form.errors.items()
            ),
        )
        return redirect("officer:verdict_detail", verdict_id=verdict.id)

    OverrideRequest.objects.create(
        verdict=verdict,
        officer=request.user,
        requested_status=form.cleaned_data["requested_status"],
        reason=form.cleaned_data["reason"],
    )
    messages.success(
        request,
        f"Override request filed: {verdict.status} → {form.cleaned_data['requested_status']}.",
    )
    return redirect("officer:verdict_detail", verdict_id=verdict.id)


# ---------------------------------------------------------------------------
# Sign-off + signed PDF download
# ---------------------------------------------------------------------------

@login_required
def signoff(request: HttpRequest, rfp_id: int) -> HttpResponse:
    rfp = get_object_or_404(Document, id=rfp_id, type=DocumentType.RFP)

    if request.method == "POST":
        try:
            call_command(
                "export_evidence",
                "--rfp-id", str(rfp.id),
                "--officer", request.user.get_full_name() or request.user.username,
            )
            messages.success(request, "Signed evidence PDF generated and audit log updated.")
        except Exception as e:  # noqa: BLE001
            messages.error(request, f"Sign-off failed: {type(e).__name__}: {e}")
        return redirect("officer:signoff", rfp_id=rfp.id)

    head = merkle_log.head()
    signed_pdf = _signed_pdf_path_for_rfp(rfp)
    pdf_meta = None
    if signed_pdf and signed_pdf.exists():
        sha = hashlib.sha256(signed_pdf.read_bytes()).hexdigest()
        pdf_meta = {
            "filename": signed_pdf.name,
            "size_bytes": signed_pdf.stat().st_size,
            "sha256": sha,
            "modified": datetime.fromtimestamp(
                signed_pdf.stat().st_mtime, tz=timezone.utc
            ).isoformat(),
        }
    # A5: walk the chain at render time so a juror who tampered with a row
    # in /admin/ can't slip past the timeline. verify_chain() is fast on
    # demo-scale data (~200 rows). Cache for the lifetime of this request.
    chain_ok, chain_problems = merkle_log.verify_chain()
    audit_recent = merkle_log.recent(limit=20)

    # Day-15 motion: feed the hash-chain SVG (drawn by praman-motion.js).
    # Reverse so the train reads left-to-right oldest → newest.
    import json as _json
    audit_chain_for_viz = _json.dumps([
        {
            "seq": e.seq,
            "ts": e.ts.isoformat(),
            "event_type": e.event_type,
            "entity": e.payload_json.get("entity", ""),
            "entity_id": e.payload_json.get("entity_id"),
            "hash": e.this_hash,
        }
        for e in reversed(audit_recent[:12])  # last 12 reads cleaner than 20
    ])

    return render(request, "officer/signoff.html", {
        "rfp": rfp,
        "audit_head_hash": head.this_hash if head else "",
        "audit_count": head.seq if head else 0,
        "pdf_meta": pdf_meta,
        "audit_recent": audit_recent,
        "audit_chain_for_viz": audit_chain_for_viz,
        "audit_chain_ok": chain_ok,
        "audit_chain_problems": chain_problems[:5],  # cap display
        "audit_chain_problems_total": len(chain_problems),
        "signed_pdf_history": _signed_pdf_history_for_rfp(rfp),
    })


@login_required
@require_GET
def evidence_pdf_download(request: HttpRequest, rfp_id: int) -> HttpResponse:
    rfp = get_object_or_404(Document, id=rfp_id, type=DocumentType.RFP)
    pdf_path = _signed_pdf_path_for_rfp(rfp)
    if not pdf_path or not pdf_path.exists():
        raise Http404("No signed evidence PDF for this RFP. Sign off first.")
    return FileResponse(
        pdf_path.open("rb"),
        as_attachment=True,
        filename=pdf_path.name,
        content_type="application/pdf",
    )


# ---------------------------------------------------------------------------
# Audit-status pill (HTMX-aware)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Day-9 endpoints: AG-Grid JSON + image overlay
# ---------------------------------------------------------------------------

@login_required
@require_GET
def eval_grid_json(request: HttpRequest, rfp_id: int) -> JsonResponse:
    """Feed AG-Grid the verdict matrix for `rfp_id`.

    Each row is one bidder; each criterion is a column whose value is a small
    object (`{verdict_id, status, rule_id, reason}`) the cell-renderer JS
    on eval_grid.html turns into a coloured pill.
    """
    rfp = get_object_or_404(Document, id=rfp_id, type=DocumentType.RFP)
    criteria = list(Criterion.objects.filter(rfp=rfp).order_by("code"))
    bidder_codes = _bidder_codes_for_rfp(rfp)

    verdicts_by_pair: dict[tuple[str, int], Verdict] = {
        (v.bidder_code, v.criterion_id): v
        for v in Verdict.objects.filter(criterion__rfp=rfp).select_related("criterion")
    }

    rows = []
    for bidder in bidder_codes:
        row = {"bidder_code": bidder, "_summary": {"PASS": 0, "FAIL": 0, "ABSTAIN": 0, "MISSING": 0}}
        for c in criteria:
            v = verdicts_by_pair.get((bidder, c.id))
            if v is None:
                row[c.code] = None
                row["_summary"]["MISSING"] += 1
            else:
                # Day-15 fix #7: surface the EFFECTIVE status (approved
                # override wins) so the grid doesn't lie about state.
                # Original Rego status preserved as `status_original`.
                eff = effective_status(v)
                row[c.code] = {
                    "verdict_id": v.id,
                    "status": eff,
                    "status_original": v.status,
                    "is_overridden": eff != v.status,
                    "rule_id": v.rule_id,
                    "reason": v.reason,
                }
                row["_summary"][eff] = row["_summary"].get(eff, 0) + 1
        rows.append(row)

    return JsonResponse({
        "rfp_id": rfp.id,
        "criteria": [
            {
                "id": c.id, "code": c.code, "title": c.title,
                "type": c.type, "mandatory": c.mandatory,
            }
            for c in criteria
        ],
        "bidder_codes": bidder_codes,
        "rows": rows,
    })


@login_required
@require_GET
def verdict_evidence_json(request: HttpRequest, verdict_id: int) -> JsonResponse:
    """Drill-down JSON: verdict + fact + every cited block with bbox in image pixels."""
    verdict = get_object_or_404(
        Verdict.objects.select_related("criterion").prefetch_related("evidence_refs"),
        id=verdict_id,
    )

    def _build_url(name: str, *args, **kwargs) -> str:
        return reverse(name, args=args, kwargs=kwargs)

    payload = build_evidence_payload(verdict, build_url=_build_url)
    return JsonResponse(payload)


@login_required
@require_GET
def document_page_png(request: HttpRequest, document_id: int, page_no: int) -> HttpResponse:
    """Serve a PNG render of one page of a Document.

    Auth-protected (login_required), unlike the raw /media/ static path.
    The Day-2 `gemini_vision_ocr.rasterize_pdf_page` and this rendering
    helper share a 200-DPI default, so any Gemini-Vision Block bbox lines
    up against this image without rescaling.
    """
    doc = get_object_or_404(Document, id=document_id)
    if not doc.file or not Path(doc.file.path).exists():
        raise Http404("Document file not on disk.")
    try:
        png_bytes, _w, _h = render_page_png(doc.file.path, page_no)
    except (FileNotFoundError, IndexError) as e:
        raise Http404(str(e)) from e
    resp = HttpResponse(png_bytes, content_type="image/png")
    # Aggressive cache OK — PDF byte content is immutable per (doc, page).
    resp["Cache-Control"] = "private, max-age=3600"
    return resp


# ---------------------------------------------------------------------------
# Audit-status pill (HTMX-aware)
# ---------------------------------------------------------------------------

@login_required
@require_GET
def audit_status(request: HttpRequest) -> HttpResponse:
    head = merkle_log.head()
    ok, problems = (
        merkle_log.verify_chain() if head else (True, [])
    )
    payload = {
        "count": head.seq if head else 0,
        "head_hash": head.this_hash if head else None,
        "verified": ok,
        "problems": problems,
    }
    if request.htmx:
        return render(request, "officer/_partials/audit_status_pill.html", payload)
    return JsonResponse(payload)
