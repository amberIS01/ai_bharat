"""Officer-facing upload forms.

Two flavours:
  * `RFPUploadForm`            — single PDF, the tender notice.
  * `BidderBundleUploadForm`   — zip archive (or single PDF) for one bidder
                                  plus a bidder-code letter (A / B / C / ...).

Both validate:
  * extension against an explicit allowlist (.pdf for tender, .pdf or .zip
    for the bundle);
  * size against a hard cap (50 MB — matches the server-wide
    `DATA_UPLOAD_MAX_MEMORY_SIZE` set on Day 1).

We deliberately keep this as plain `forms.Form` (not `ModelForm`) because
the upload pipeline runs through `core/management/commands/ingest_bundle.py`
which has its own SHA-256 dedup, Document-row creation, and Docling-then-
Gemini-Vision dispatch — re-implementing any of that on the form would
duplicate logic that's already covered by 60+ pytest tests.
"""

from __future__ import annotations

import re
from pathlib import Path

from django import forms
from django.core.exceptions import ValidationError


MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB; mirrors settings.DATA_UPLOAD_MAX_MEMORY_SIZE
ALLOWED_TENDER_EXTS = {".pdf"}
ALLOWED_BUNDLE_EXTS = {".pdf", ".zip"}
BIDDER_CODE_RX = re.compile(r"^[A-Z]$")


def _check_size(f, *, label: str) -> None:
    if f.size > MAX_UPLOAD_BYTES:
        raise ValidationError(
            f"{label} is {f.size / (1024*1024):.1f} MB; the per-file cap is "
            f"{MAX_UPLOAD_BYTES // (1024*1024)} MB."
        )


def _check_extension(f, *, allowed: set[str], label: str) -> None:
    ext = Path(f.name).suffix.lower()
    if ext not in allowed:
        raise ValidationError(
            f"{label} extension {ext!r} not allowed. "
            f"Permitted: {', '.join(sorted(allowed))}."
        )


# ---------------------------------------------------------------------------
# RFP / tender upload
# ---------------------------------------------------------------------------

class RFPUploadForm(forms.Form):
    """A single PDF: the tender notice the officer wants to evaluate."""

    rfp_file = forms.FileField(
        label="Tender notice (PDF)",
        widget=forms.ClearableFileInput(attrs={"accept": ".pdf"}),
        help_text="The RFP / NIT document. Must be a PDF, max 50 MB.",
    )

    def clean_rfp_file(self):
        f = self.cleaned_data["rfp_file"]
        _check_extension(f, allowed=ALLOWED_TENDER_EXTS, label="RFP file")
        _check_size(f, label="RFP file")
        return f


# ---------------------------------------------------------------------------
# Bidder bundle upload
# ---------------------------------------------------------------------------

class BidderBundleUploadForm(forms.Form):
    """One bidder's submission: zip archive (or single PDF) + bidder code."""

    # Field-level max_length is wider so our regex in clean_bidder_code can
    # produce a friendlier "single uppercase letter" message instead of
    # Django's generic "ensure at most 1 character".
    bidder_code = forms.CharField(
        max_length=4,
        label="Bidder code",
        help_text="Single uppercase letter (A, B, C, ...).",
    )
    bundle_file = forms.FileField(
        label="Bidder bundle",
        widget=forms.ClearableFileInput(attrs={"accept": ".pdf,.zip"}),
        help_text="Zip archive containing the bidder's PDFs, OR a single PDF.",
    )

    def clean_bidder_code(self):
        code = (self.cleaned_data["bidder_code"] or "").upper().strip()
        if not BIDDER_CODE_RX.match(code):
            raise ValidationError(
                "Bidder code must be a single uppercase letter (A, B, C, ...)."
            )
        return code

    def clean_bundle_file(self):
        f = self.cleaned_data["bundle_file"]
        _check_extension(f, allowed=ALLOWED_BUNDLE_EXTS, label="Bundle")
        _check_size(f, label="Bundle")
        return f


# ---------------------------------------------------------------------------
# Override request form (officer overriding a Verdict)
# ---------------------------------------------------------------------------

from core.models import VerdictStatus


# ---------------------------------------------------------------------------
# Inline-edit form for criteria_review.html (Day-10)
# ---------------------------------------------------------------------------

EDITABLE_CRITERION_FIELDS = {"title", "requirement_text", "mandatory"}


class CriterionInlineEditForm(forms.Form):
    """Validates a single-field PATCH-style edit of a Criterion row.

    Only `title`, `requirement_text`, `mandatory` are editable. Code and
    type are intentionally NOT editable from the UI: `code` is a unique
    identifier and `type` (combined with `title`) drives Rego rule
    dispatch in `core.policy.parsers.rego_path_for_criterion`.
    """

    field = forms.ChoiceField(
        choices=[(f, f) for f in sorted(EDITABLE_CRITERION_FIELDS)],
    )
    value = forms.CharField(required=False, max_length=4000)

    def clean(self):
        cleaned = super().clean()
        field = cleaned.get("field")
        raw = (cleaned.get("value") or "").strip()
        if field == "title" and not raw:
            raise ValidationError("title cannot be empty.")
        if field == "requirement_text" and not raw:
            raise ValidationError("requirement_text cannot be empty.")
        if field == "mandatory":
            cleaned["value"] = raw.lower() in {"true", "1", "yes", "on", "mandatory"}
        elif field == "title":
            if len(raw) > 255:
                raise ValidationError("title is capped at 255 chars.")
            cleaned["value"] = raw
        else:
            cleaned["value"] = raw
        return cleaned


class OverrideRequestForm(forms.Form):
    """Officer-initiated override of a single Verdict."""

    requested_status = forms.ChoiceField(
        choices=VerdictStatus.choices,
        label="Requested verdict",
    )
    reason = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 4}),
        min_length=20,
        max_length=2000,
        label="Reason for override (≥ 20 characters)",
        help_text=(
            "Explain why the system's verdict should be changed. This text is "
            "preserved in the Merkle audit log and surfaces on every CVC / CAG "
            "review."
        ),
    )
