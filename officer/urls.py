"""URL routing for the Praman officer app.

Mounted at /officer/ in praman/urls.py. The dashboard at /officer/ lists the
RFPs in the database and routes the operator through the five hero screens.

The URL names are stable (kept short and snake_case) so the templates can
reference them via `{% url 'officer:eval_grid' rfp.id %}` without
reformatting if we move the views around later.
"""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "officer"

urlpatterns = [
    # Dashboard / index
    path("", views.dashboard, name="dashboard"),

    # Hero screen 1 — uploads
    path("upload/tender/", views.upload_tender, name="upload_tender"),
    path("upload/bidder/<int:rfp_id>/", views.upload_bidder, name="upload_bidder"),

    # Hero screen 2 — criteria review
    path("rfp/<int:rfp_id>/criteria/", views.criteria_review, name="criteria_review"),

    # Run extraction + evaluation
    path("rfp/<int:rfp_id>/extract/", views.run_extraction, name="run_extraction"),
    path("rfp/<int:rfp_id>/evaluate/", views.run_evaluation, name="run_evaluation"),

    # Day-10 inline edit (HTMX swap target)
    path("criterion/<int:criterion_id>/edit/",
         views.criterion_inline_edit, name="criterion_inline_edit"),

    # Hero screen 3 — bidder × criterion verdict matrix
    path("rfp/<int:rfp_id>/grid/", views.eval_grid, name="eval_grid"),

    # Hero screen 4 — drill-down for one verdict
    path("verdict/<int:verdict_id>/", views.verdict_detail, name="verdict_detail"),
    path("verdict/<int:verdict_id>/override/", views.verdict_override, name="verdict_override"),

    # Hero screen 5 — sign-off + signed PDF download
    path("rfp/<int:rfp_id>/signoff/", views.signoff, name="signoff"),
    path("rfp/<int:rfp_id>/evidence.pdf", views.evidence_pdf_download, name="evidence_pdf_download"),

    # Audit-chain status (used by the dashboard health pill)
    path("audit/status/", views.audit_status, name="audit_status"),

    # Day-9 endpoints — JSON for the AG-Grid + drill-down image overlay.
    path("rfp/<int:rfp_id>/grid.json", views.eval_grid_json, name="eval_grid_json"),
    path("verdict/<int:verdict_id>/evidence.json",
         views.verdict_evidence_json, name="verdict_evidence_json"),
    path("document/<int:document_id>/page/<int:page_no>.png",
         views.document_page_png, name="document_page_png"),
]
