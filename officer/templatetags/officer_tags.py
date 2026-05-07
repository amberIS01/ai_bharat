"""Template filters + tags used by the officer-app templates.

Django's default template language has no dict-access-by-key syntax. We
need it for the bidder × criterion verdict matrix in eval_grid.html
(`matrix[bidder][criterion_id]`). Adding a filter here is the standard
Django answer.

Day-10 also adds an inclusion tag `sidebar_nav` that renders the 5-step
hero-screen progress rail in `_partials/sidebar_nav.html`. The tag reads
`request.resolver_match` to figure out the active step and surfaces the
RFP id from URL kwargs (or falls back to the most recent RFP) so the
sidebar links are always navigable.
"""

from __future__ import annotations

from django import template

register = template.Library()


@register.filter(name="get")
def get_item(d, key):
    """Look up `key` in a dict-like object. Returns None if missing.

    Usage in templates::

        {{ matrix|get:bidder|get:c.id }}

    Falls back to attribute access for non-dict objects so the same filter
    works on namedtuples / dataclasses.
    """
    if d is None:
        return None
    if isinstance(d, dict):
        return d.get(key)
    return getattr(d, key, None)


# ---------------------------------------------------------------------------
# Sidebar nav (Day-10)
# ---------------------------------------------------------------------------

# Map URL name → step key. Pages not in this map render the sidebar with
# no step highlighted (e.g. /admin/, login).
_URL_TO_STEP = {
    "dashboard": "dashboard",
    "upload_tender": "upload",
    "upload_bidder": "upload",
    "criteria_review": "criteria",
    "run_extraction": "criteria",
    "eval_grid": "eval",
    "run_evaluation": "eval",
    "verdict_detail": "drilldown",
    "verdict_override": "drilldown",
    "signoff": "signoff",
    "evidence_pdf_download": "signoff",
}


@register.inclusion_tag("officer/_partials/sidebar_nav.html", takes_context=True)
def sidebar_nav(context):
    """Render the five-step officer progress rail.

    Highlights the current step (from `request.resolver_match.url_name`) and
    surfaces the active rfp_id (from URL kwargs, or the latest RFP in DB).
    """
    request = context.get("request")
    current_step = "dashboard"
    rfp_id = None

    if request is not None and getattr(request, "resolver_match", None):
        url_name = request.resolver_match.url_name
        current_step = _URL_TO_STEP.get(url_name, current_step)
        rfp_id = request.resolver_match.kwargs.get("rfp_id")

    # If we're on a page without rfp_id in the URL (e.g. dashboard,
    # verdict_detail), use the rfp from context if the view passed one,
    # otherwise pick the most recent RFP so the sidebar links still work.
    if rfp_id is None:
        rfp_obj = context.get("rfp")
        if rfp_obj is not None and getattr(rfp_obj, "id", None):
            rfp_id = rfp_obj.id

    # Verdict detail: dig the rfp out of the verdict.
    if rfp_id is None and "verdict" in context:
        v = context["verdict"]
        rfp_id = getattr(getattr(v, "criterion", None), "rfp_id", None)

    if rfp_id is None:
        # Final fallback — keeps the sidebar usable when navigating via /admin/.
        from core.models import Document, DocumentType
        latest = (
            Document.objects.filter(type=DocumentType.RFP)
            .order_by("-created_at").only("id").first()
        )
        rfp_id = latest.id if latest else None

    return {
        "current_step": current_step,
        "rfp_id": rfp_id,
    }
