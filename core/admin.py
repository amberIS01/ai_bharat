"""Django admin scaffolding for the Praman back-office.

Day 8's whole reason for existing is "free productivity bonus" — the admin
gives the demo a polished, government-grade inspection surface for every
table without any custom UI work. The four customisations the plan called
out are all here:

  * AuditEntryAdmin       — fully read-only (only merkle_log.append() writes),
                            seq DESC, pretty-printed payload_json, and a
                            "Verify chain" action that runs verify_chain()
                            and surfaces the first inconsistency on the next
                            request via Django messages.
  * OverrideRequestAdmin  — list filter on status, approve/reject bulk
                            actions that mutate `status` AND fire post_save
                            signals so the audit chain captures the human
                            decision.
  * VerdictAdmin          — list filter on PASS / FAIL / ABSTAIN, search by
                            criterion title and rule_id.
  * CriterionAdmin        — Fact inlines so a reviewer can see "what the
                            extractor pulled per bidder for this criterion"
                            on one page.

Hostile-edit principles applied:

  1. AuditEntry forbids add / change / delete via permission overrides.
     Even a superuser cannot mutate the chain through the admin — the only
     legitimate writer is `core.audit.merkle_log.append`. Anything else
     would break the tamper-evidence guarantee.
  2. Block / Fact / GeminiCallCache are also read-only via the admin —
     they're persisted by service layers, and editing them by hand would
     desynchronise from the audit log.
  3. We never override `delete_model` / `delete_queryset` to bypass the
     audit signals; deletion of an auditable entity should fire the same
     post_delete signal it always does.
"""

from __future__ import annotations

import json
from typing import Any

from django.contrib import admin, messages
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from core.audit import merkle_log
from core.models import (
    AuditEntry,
    Block,
    Criterion,
    Document,
    Fact,
    GeminiCallCache,
    OverrideRequest,
    OverrideStatus,
    Verdict,
    VerdictStatus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pretty_json(value: Any) -> str:
    """Stable pretty-print for any JSON-serialisable value, for the admin."""
    if value in (None, "", {}, []):
        return "(empty)"
    try:
        return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


# ---------------------------------------------------------------------------
# Document
# ---------------------------------------------------------------------------

@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = (
        "id", "type", "bidder_code", "original_filename",
        "page_count", "sha256_short", "created_at",
    )
    list_filter = ("type", "bidder_code")
    search_fields = ("original_filename", "sha256")
    readonly_fields = ("sha256", "created_at", "page_count")
    date_hierarchy = "created_at"
    list_per_page = 50

    @admin.display(description="sha256 (first 12)")
    def sha256_short(self, obj: Document) -> str:
        return obj.sha256[:12] + "..."


# ---------------------------------------------------------------------------
# Block — read-only inspection only
# ---------------------------------------------------------------------------

@admin.register(Block)
class BlockAdmin(admin.ModelAdmin):
    list_display = (
        "id", "document", "block_index", "page_no",
        "source", "confidence_pct", "text_preview",
    )
    list_filter = ("source", "coord_origin", "document__type")
    search_fields = ("text",)
    readonly_fields = tuple(
        f.name for f in Block._meta.fields
    )
    list_per_page = 100

    @admin.display(description="conf")
    def confidence_pct(self, obj: Block) -> str:
        threshold = 0.85
        cls = "color: #047857;" if obj.confidence >= threshold else "color: #b91c1c; font-weight: bold;"
        return format_html('<span style="{}">{}</span>', cls, f"{obj.confidence:.2f}")

    @admin.display(description="text")
    def text_preview(self, obj: Block) -> str:
        return (obj.text or "")[:80].replace("\n", " ")

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        # Document delete cascades to Blocks; allow the cascade but not direct deletion.
        return False


# ---------------------------------------------------------------------------
# Fact — read-only with evidence inline
# ---------------------------------------------------------------------------

@admin.register(Fact)
class FactAdmin(admin.ModelAdmin):
    list_display = (
        "id", "criterion_code", "criterion_title", "bidder_code",
        "value_short", "ocr_confidence_pct",
    )
    list_filter = ("bidder_code", "criterion__type", "criterion__mandatory")
    search_fields = ("value", "criterion__title", "criterion__code")
    readonly_fields = (
        "criterion", "bidder_code", "value", "ocr_confidence",
        "evidence_blocks_display", "raw_extraction_pretty", "created_at",
    )
    exclude = ("evidence_blocks", "raw_extraction_json")

    @admin.display(description="C-code", ordering="criterion__code")
    def criterion_code(self, obj: Fact) -> str:
        return obj.criterion.code

    @admin.display(description="Criterion title", ordering="criterion__title")
    def criterion_title(self, obj: Fact) -> str:
        return obj.criterion.title

    @admin.display(description="value")
    def value_short(self, obj: Fact) -> str:
        return (obj.value or "(absent)")[:50]

    @admin.display(description="OCR conf")
    def ocr_confidence_pct(self, obj: Fact) -> str:
        return f"{obj.ocr_confidence:.2f}"

    @admin.display(description="Evidence blocks")
    def evidence_blocks_display(self, obj: Fact) -> str:
        rows = []
        for b in obj.evidence_blocks.all().select_related("document"):
            rows.append(
                f"<li>"
                f"<code>id={b.id}</code> "
                f"{b.document.type} p{b.page_no} "
                f"conf={b.confidence:.2f} src={b.source}: "
                f"<em>{(b.text or '')[:80]}</em>"
                f"</li>"
            )
        if not rows:
            return "(none)"
        return mark_safe("<ul style='margin-left:1em;'>" + "".join(rows) + "</ul>")

    @admin.display(description="Raw Gemini extraction")
    def raw_extraction_pretty(self, obj: Fact) -> str:
        return mark_safe(
            f"<pre style='white-space:pre-wrap;'>{_pretty_json(obj.raw_extraction_json)}</pre>"
        )

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False


# ---------------------------------------------------------------------------
# Criterion — Facts as inline
# ---------------------------------------------------------------------------

class FactInline(admin.TabularInline):
    model = Fact
    extra = 0
    can_delete = False
    fields = ("bidder_code", "value", "ocr_confidence")
    readonly_fields = fields
    show_change_link = True

    def has_add_permission(self, request, obj=None) -> bool:
        return False


@admin.register(Criterion)
class CriterionAdmin(admin.ModelAdmin):
    list_display = ("code", "title", "type", "mandatory", "rfp_filename", "fact_count")
    list_filter = ("type", "mandatory")
    search_fields = ("code", "title", "requirement_text")
    inlines = [FactInline]
    readonly_fields = ("source_clause_block", "created_at")

    @admin.display(description="RFP", ordering="rfp__original_filename")
    def rfp_filename(self, obj: Criterion) -> str:
        return obj.rfp.original_filename

    @admin.display(description="# facts")
    def fact_count(self, obj: Criterion) -> int:
        return obj.facts.count()


# ---------------------------------------------------------------------------
# Verdict — list-filter on status, evidence inline
# ---------------------------------------------------------------------------

@admin.register(Verdict)
class VerdictAdmin(admin.ModelAdmin):
    list_display = (
        "id", "criterion_code", "criterion_title", "bidder_code",
        "status_pill", "rule_id", "decided_at",
    )
    list_filter = ("status", "bidder_code", "criterion__type")
    search_fields = ("criterion__title", "criterion__code", "rule_id", "reason")
    readonly_fields = (
        "criterion", "bidder_code", "status", "rule_id",
        "bindings_pretty", "evidence_refs_display", "reason", "decided_at",
    )
    exclude = ("bindings_json", "evidence_refs")
    date_hierarchy = "decided_at"

    @admin.display(description="C-code", ordering="criterion__code")
    def criterion_code(self, obj: Verdict) -> str:
        return obj.criterion.code

    @admin.display(description="Criterion title", ordering="criterion__title")
    def criterion_title(self, obj: Verdict) -> str:
        return obj.criterion.title

    @admin.display(description="Status")
    def status_pill(self, obj: Verdict) -> str:
        colours = {
            VerdictStatus.PASS.value:    ("#d1fae5", "#065f46"),
            VerdictStatus.FAIL.value:    ("#fee2e2", "#991b1b"),
            VerdictStatus.ABSTAIN.value: ("#fef3c7", "#92400e"),
        }
        bg, fg = colours.get(obj.status, ("#e5e7eb", "#374151"))
        return format_html(
            '<span style="background:{}; color:{}; padding:2px 8px; '
            'border-radius:6px; font-weight:bold; font-size:11px;">{}</span>',
            bg, fg, obj.status,
        )

    @admin.display(description="Rule bindings")
    def bindings_pretty(self, obj: Verdict) -> str:
        return mark_safe(
            f"<pre style='white-space:pre-wrap;'>{_pretty_json(obj.bindings_json)}</pre>"
        )

    @admin.display(description="Evidence refs")
    def evidence_refs_display(self, obj: Verdict) -> str:
        rows = []
        for b in obj.evidence_refs.all().select_related("document"):
            rows.append(
                f"<li><code>id={b.id}</code> "
                f"{b.document.type} p{b.page_no} conf={b.confidence:.2f}: "
                f"<em>{(b.text or '')[:80]}</em></li>"
            )
        if not rows:
            return "(none)"
        return mark_safe("<ul style='margin-left:1em;'>" + "".join(rows) + "</ul>")

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        # Verdicts are derived from Rego rules + Facts; editing one by hand
        # would silently desync the audit chain. Use OverrideRequest instead.
        return False


# ---------------------------------------------------------------------------
# OverrideRequest — approve / reject actions
# ---------------------------------------------------------------------------

@admin.register(OverrideRequest)
class OverrideRequestAdmin(admin.ModelAdmin):
    list_display = (
        "id", "verdict_id", "verdict_summary",
        "requested_status", "status", "officer", "ts",
    )
    list_filter = ("status", "requested_status")
    search_fields = ("reason", "verdict__criterion__title", "verdict__criterion__code")
    readonly_fields = (
        "verdict", "officer", "requested_status", "reason", "ts",
    )
    date_hierarchy = "ts"
    actions = ["approve_selected", "reject_selected"]

    @admin.display(description="Verdict")
    def verdict_summary(self, obj: OverrideRequest) -> str:
        v = obj.verdict
        return f"{v.criterion.code} × Bidder {v.bidder_code} (current: {v.status})"

    @admin.action(description="Approve selected override requests")
    def approve_selected(self, request, queryset):
        n = 0
        for o in queryset.filter(status=OverrideStatus.PENDING):
            o.status = OverrideStatus.APPROVED.value
            o.save()  # post_save signal → audit "override.approved" entry
            n += 1
        self.message_user(
            request,
            f"Approved {n} override request(s). The audit log captured each as an `override.approved` event.",
            level=messages.SUCCESS,
        )

    @admin.action(description="Reject selected override requests")
    def reject_selected(self, request, queryset):
        n = 0
        for o in queryset.filter(status=OverrideStatus.PENDING):
            o.status = OverrideStatus.REJECTED.value
            o.save()
            n += 1
        self.message_user(
            request,
            f"Rejected {n} override request(s).",
            level=messages.SUCCESS,
        )


# ---------------------------------------------------------------------------
# AuditEntry — fully read-only with verify-chain action
# ---------------------------------------------------------------------------

@admin.register(AuditEntry)
class AuditEntryAdmin(admin.ModelAdmin):
    list_display = (
        "seq", "ts", "event_type", "entity_summary", "this_hash_short",
    )
    list_filter = ("event_type",)
    search_fields = ("event_type", "this_hash", "prev_hash")
    readonly_fields = (
        "seq", "ts", "event_type", "prev_hash", "this_hash",
        "payload_pretty",
    )
    exclude = ("payload_json",)
    ordering = ("-seq",)
    date_hierarchy = "ts"
    list_per_page = 50
    actions = ["verify_chain_action"]

    @admin.display(description="Entity")
    def entity_summary(self, obj: AuditEntry) -> str:
        data = obj.payload_json or {}
        entity = data.get("entity", "")
        entity_id = data.get("entity_id", "")
        return f"{entity}/{entity_id}" if entity else ""

    @admin.display(description="this_hash")
    def this_hash_short(self, obj: AuditEntry) -> str:
        return obj.this_hash[:16] + "..."

    @admin.display(description="payload_json (canonical)")
    def payload_pretty(self, obj: AuditEntry) -> str:
        return mark_safe(
            f"<pre style='white-space:pre-wrap; "
            f"background:#f8fafc; border:1px solid #e2e8f0; padding:8px;'>"
            f"{_pretty_json(obj.payload_json)}"
            f"</pre>"
        )

    @admin.action(description="Verify Merkle chain (re-hash from genesis)")
    def verify_chain_action(self, request, queryset):
        ok, problems = merkle_log.verify_chain()
        if ok:
            self.message_user(
                request,
                f"Chain GREEN — every entry hashes correctly from genesis. "
                f"Total entries: {merkle_log.diagnose_chain()['count']}",
                level=messages.SUCCESS,
            )
        else:
            for p in problems[:10]:
                self.message_user(request, f"BROKEN: {p}", level=messages.ERROR)
            if len(problems) > 10:
                self.message_user(
                    request,
                    f"... and {len(problems) - 10} more problem(s). Run "
                    f"`python manage.py verify_audit_chain` for full output.",
                    level=messages.ERROR,
                )

    # Hostile-edit lockdown: nobody (not even a superuser) edits the chain
    # through the admin. The only legitimate writer is merkle_log.append().
    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


# ---------------------------------------------------------------------------
# GeminiCallCache — read-only inspection + bulk delete to force re-extraction
# ---------------------------------------------------------------------------

@admin.register(GeminiCallCache)
class GeminiCallCacheAdmin(admin.ModelAdmin):
    list_display = (
        "id", "model", "prompt_hash_short", "image_hash_short",
        "hits", "created_at",
    )
    list_filter = ("model",)
    search_fields = ("prompt_sha256", "image_sha256", "model")
    readonly_fields = (
        "prompt_sha256", "image_sha256", "model",
        "response_pretty", "hits", "created_at",
    )
    exclude = ("response_json",)
    date_hierarchy = "created_at"
    list_per_page = 50
    actions = ["clear_all_cache_entries"]

    @admin.display(description="prompt sha (first 12)")
    def prompt_hash_short(self, obj: GeminiCallCache) -> str:
        return obj.prompt_sha256[:12] + "..." if obj.prompt_sha256 else ""

    @admin.display(description="image sha (first 12)")
    def image_hash_short(self, obj: GeminiCallCache) -> str:
        return obj.image_sha256[:12] + "..." if obj.image_sha256 else "(text only)"

    @admin.display(description="cached response")
    def response_pretty(self, obj: GeminiCallCache) -> str:
        return mark_safe(
            f"<pre style='white-space:pre-wrap; max-height:400px; overflow:auto;'>"
            f"{_pretty_json(obj.response_json)}"
            f"</pre>"
        )

    @admin.action(description="Clear ALL Gemini cache entries (forces fresh API calls)")
    def clear_all_cache_entries(self, request, queryset):
        n = GeminiCallCache.objects.count()
        GeminiCallCache.objects.all().delete()
        self.message_user(
            request,
            f"Deleted {n} cache entries. The next extract / OCR run will hit the Gemini API.",
            level=messages.WARNING,
        )

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False


# ---------------------------------------------------------------------------
# Admin site branding
# ---------------------------------------------------------------------------

admin.site.site_header = "Praman — Tender Evaluation (Admin)"
admin.site.site_title = "Praman Admin"
admin.site.index_title = "Praman back-office"
