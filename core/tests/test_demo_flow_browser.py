"""Day-14 browser smoke test for the 5-screen demo flow.

The hostile audit on 2026-05-03 flagged that AG-Grid initialization,
the drilldown citation cards, and the HTMX inline-edit toggle have
all been exercised only via curl. No human has clicked through the
full flow in a real browser end-to-end.

This test drives Chromium via Playwright through the same shotlist the
team will record. It is **gracefully skipped** if Playwright isn't
installed (`pip install playwright && playwright install chromium`)
so the regular unit-test run isn't impacted.

Run manually:

    pip install playwright
    playwright install chromium
    python manage.py runserver 127.0.0.1:18765 &  # dev server on a fixed port
    python manage.py start_opa &
    pytest core/tests/test_demo_flow_browser.py -s
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# Skip the entire module if Playwright isn't installed. Day-14 ships
# the file as documentation of the expected click-through; the team
# runs it on each dry-run machine.
playwright = pytest.importorskip("playwright.sync_api")

# Allow the operator to override the dev server URL.
DEMO_URL = os.environ.get("PRAMAN_DEMO_URL", "http://127.0.0.1:8000")
DEMO_USER = os.environ.get("PRAMAN_DEMO_USER", "officer")
DEMO_PWD = os.environ.get("PRAMAN_DEMO_PWD", "praman_demo_2026")
SCREENSHOT_DIR = Path("docs/screenshots")


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def page(browser):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    ctx = browser.new_context(viewport={"width": 1920, "height": 1080})
    page = ctx.new_page()
    yield page
    ctx.close()


def _login(page):
    page.goto(f"{DEMO_URL}/accounts/login/")
    page.fill("input[name='username']", DEMO_USER)
    page.fill("input[name='password']", DEMO_PWD)
    page.click("button[type='submit']")
    page.wait_for_url(f"{DEMO_URL}/officer/")


def test_dashboard_renders_with_audit_pill(page):
    _login(page)
    page.screenshot(path=str(SCREENSHOT_DIR / "01_dashboard.png"))
    # Audit pill is HTMX-loaded on `load`; wait for the swap.
    page.wait_for_selector("#audit-pill")
    pill_text = page.inner_text("#audit-pill")
    assert "Audit chain" in pill_text
    # Sidebar has the 5 step labels
    body_text = page.inner_text("body")
    for label in ("Upload", "Criteria", "Eval grid", "Drill-down", "Sign-off"):
        assert label in body_text


def test_eval_grid_renders_aggrid_rows(page):
    _login(page)
    # Pick the first RFP from dashboard
    page.click("a:has-text('Grid')")
    # AG-Grid renders rows with class `.ag-row`. Wait up to 5s for hydration.
    page.wait_for_selector(".ag-row", timeout=5000)
    row_count = page.locator(".ag-row").count()
    assert row_count >= 3, f"Expected ≥3 bidder rows, got {row_count}"
    page.screenshot(path=str(SCREENSHOT_DIR / "02_eval_grid.png"))


def test_drilldown_shows_bbox_overlay(page):
    _login(page)
    page.click("a:has-text('Grid')")
    page.wait_for_selector(".ag-row")
    # Click the first ABSTAIN cell — the most narrative-rich path.
    abstain_cells = page.locator(".ag-cell-pill.ABSTAIN")
    if abstain_cells.count() == 0:
        pytest.skip("No ABSTAIN cells visible in the grid; drilldown skipped.")
    abstain_cells.first.click()
    page.wait_for_url(f"**/officer/verdict/**")
    page.wait_for_selector(".evidence-frame", timeout=8000)
    # The bbox overlay div is under .evidence-frame. May take a moment
    # for the page PNG to fetch + render.
    page.wait_for_selector(".evidence-frame .bbox", timeout=8000)
    page.screenshot(path=str(SCREENSHOT_DIR / "03_drilldown.png"))


def test_signoff_shows_audit_timeline_and_pdf_history(page):
    _login(page)
    page.goto(f"{DEMO_URL}/officer/")
    page.click("a:has-text('Sign-off')")
    page.wait_for_load_state("networkidle")
    body = page.inner_text("body")
    assert "Audit timeline" in body
    assert "Signed evidence PDFs" in body
    page.screenshot(path=str(SCREENSHOT_DIR / "04_signoff.png"))


def test_criterion_inline_edit_toggle_via_htmx(page):
    _login(page)
    page.click("a:has-text('Criteria')")
    page.wait_for_selector("button:has-text('MANDATORY'), button:has-text('OPTIONAL')")
    # Capture the first row's state before the toggle
    first_pill = page.locator("button:has-text('MANDATORY'), button:has-text('OPTIONAL')").first
    before = first_pill.inner_text()
    first_pill.click()
    # HTMX swap should be quick
    page.wait_for_timeout(500)
    after = first_pill.inner_text()
    assert before != after, f"HTMX swap did not change pill text (before={before!r} after={after!r})"
    # Restore
    first_pill.click()
    page.wait_for_timeout(500)
