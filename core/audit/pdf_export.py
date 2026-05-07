"""Build the Day-6 evidence PDF that Praman exports for CVC / CAG / RTI.

`build_evidence_pdf(rfp, output_path)` renders a multi-page PDF containing:

  1. Cover page — tender meta, audit head hash (the cryptographic anchor)
  2. Verdict matrix — Bidder × Criterion grid colour-coded PASS / FAIL / ABSTAIN
  3. Per-verdict detail — rule_id, bindings, extracted value, evidence bboxes
  4. Audit log summary — head hash, count, breakdown by event_type
  5. Officer sign-off frame — where pyHanko's visible signature widget lands
  6. Demo-signature disclosure — explicit "self-signed, prod uses CCA Class-3 DSC"

The PDF text contains the audit chain's head hash. pyHanko's signature on
the same PDF then covers those bytes, binding "audit chain at sign-time = X"
into a single cryptographic claim a CVC auditor can verify offline.

WeasyPrint renders the Jinja2 template at `core/audit/templates/evidence_pdf.html`.
The pyHanko signing pass adds a visible signature WIDGET on top — the signing
pipeline (`core/audit/signer.py`) does NOT regenerate the PDF, it incrementally
mutates the existing one so the cryptographic binding holds.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jinja2
from weasyprint import HTML

from core.audit.merkle_log import GENESIS_PREV_HASH, diagnose_chain
from core.models import (
    AuditEntry,
    Criterion,
    Document,
    DocumentType,
    Fact,
    Verdict,
)

TEMPLATE_DIR = Path(__file__).parent / "templates"
DEFAULT_OFFICER_NAME = "Demo Officer (Self-Signed)"
DEFAULT_OFFICER_DESIGNATION = "Praman Demo — Procurement Cell"


def _bindings_pretty(bindings: dict | None) -> str:
    """Pretty-print the Rego bindings dict for the per-verdict card."""
    if not bindings:
        return ""
    try:
        return json.dumps(bindings, indent=2, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return str(bindings)


def _build_context(
    rfp: Document,
    *,
    officer_name: str,
    officer_designation: str,
    confidence_threshold: float,
) -> dict[str, Any]:
    """Assemble all template variables in one place."""
    criteria = list(
        Criterion.objects.filter(rfp=rfp).order_by("code")
    )
    criteria_by_code = {c.code: c for c in criteria}

    bidder_codes = sorted({
        v.bidder_code for v in Verdict.objects.filter(criterion__rfp=rfp)
    })

    # Build matrix[bidder][criterion_id] = dict with effective status.
    # The PDF must show the effective (override-applied) status, NOT the
    # raw Rego output, otherwise a sealed PDF can disagree with the live
    # grid + drilldown after an officer override is approved.
    matrix: dict[str, dict[int, dict[str, Any]]] = {b: {} for b in bidder_codes}
    verdict_qs = (
        Verdict.objects.filter(criterion__rfp=rfp)
        .select_related("criterion")
        .prefetch_related("evidence_refs")
        .order_by("bidder_code", "criterion__code")
    )
    verdicts_for_template: list[dict[str, Any]] = []
    from core.policy.effective import effective_override, effective_status as _eff
    for v in verdict_qs:
        eff = _eff(v)
        eff_override = effective_override(v)
        matrix[v.bidder_code][v.criterion_id] = {
            "status": eff,
            "status_original": v.status,
            "is_overridden": eff != v.status,
        }

        # Look up the underlying Fact (for extracted value + confidence)
        fact = Fact.objects.filter(criterion=v.criterion, bidder_code=v.bidder_code).first()
        evidence = [
            {
                "document_id": e.document_id,
                "page_no": e.page_no,
                "bbox_l": e.bbox_l,
                "bbox_t": e.bbox_t,
                "bbox_r": e.bbox_r,
                "bbox_b": e.bbox_b,
                "confidence": float(e.confidence),
                "source": e.source,
            }
            for e in v.evidence_refs.all()
        ]
        # Day-15 fix #7: PDF must reflect the EFFECTIVE status (approved
        # override wins) so the sealed document is consistent with the
        # live grid + drilldown. Original Rego status is also surfaced
        # so the PDF preserves the full provenance.
        # (eff + eff_override already computed above for the matrix.)
        verdicts_for_template.append({
            "criterion": v.criterion,
            "bidder_code": v.bidder_code,
            "status": eff,
            "status_original": v.status,
            "is_overridden": eff != v.status,
            "override_officer": str(eff_override.officer) if eff_override else "",
            "override_ts": eff_override.ts.isoformat() if eff_override else "",
            "override_reason": eff_override.reason if eff_override else "",
            "rule_id": v.rule_id,
            "reason": v.reason,
            "fact_value": fact.value if fact else "",
            "fact_confidence": float(fact.ocr_confidence) if fact else 0.0,
            "bindings": v.bindings_json,
            "bindings_pretty": _bindings_pretty(v.bindings_json),
            "evidence_refs": evidence,
        })

    # Audit chain summary
    info = diagnose_chain()
    by_type_sorted = sorted(
        info["by_event_type"].items(), key=lambda kv: (-kv[1], kv[0])
    )

    # Tender meta — pull from the RFP Document itself + the parsed blocks if present.
    tender_no = rfp.original_filename.replace(".pdf", "") if rfp.original_filename else f"rfp-{rfp.id}"
    # Pull the synthetic RFP's tender number / title heuristically — the
    # synthetic data is pinned to one RFP context; in production this would
    # come from the parsed RFP's metadata.
    tender_no = "PRAMAN/DEMO/2026-27/T/001"
    tender_title = "Construction of Demo Administrative Office Complex at Synthetic CRPF Battalion"
    estimated_cost = "Rs. 5,25,00,000/- (Rupees Five Crore Twenty-Five Lakh only)"

    return {
        "tender_no": tender_no,
        "tender_title": tender_title,
        "estimated_cost": estimated_cost,
        "bidder_count": len(bidder_codes),
        "criterion_count": len(criteria),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "confidence_threshold": confidence_threshold,
        "criteria": criteria,
        "criteria_by_code": criteria_by_code,
        "bidder_codes": bidder_codes,
        "matrix": matrix,
        "verdicts": verdicts_for_template,
        "head_hash": info["head_hash"],
        "head_seq": info["head_seq"],
        "genesis_prev_hash": GENESIS_PREV_HASH,
        "audit_entry_count": info["count"],
        "audit_by_type": by_type_sorted,
        "officer_name": officer_name,
        "officer_designation": officer_designation,
    }


def build_evidence_pdf(
    rfp: Document,
    output_path: Path | str,
    *,
    officer_name: str = DEFAULT_OFFICER_NAME,
    officer_designation: str = DEFAULT_OFFICER_DESIGNATION,
    confidence_threshold: float = 0.85,
) -> Path:
    """Render the evidence PDF (unsigned). Returns the output path on success."""
    if rfp.type != DocumentType.RFP:
        raise ValueError(f"build_evidence_pdf needs an RFP Document, got type={rfp.type}")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=jinja2.select_autoescape(["html", "xml"]),
    )
    template = env.get_template("evidence_pdf.html")
    context = _build_context(
        rfp,
        officer_name=officer_name,
        officer_designation=officer_designation,
        confidence_threshold=confidence_threshold,
    )
    html_str = template.render(**context)

    HTML(string=html_str, base_url=str(TEMPLATE_DIR)).write_pdf(str(output_path))
    return output_path
