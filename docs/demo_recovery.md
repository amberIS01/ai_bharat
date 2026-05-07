# Demo recovery checklist

What to do (and what *not* to say) if something fails on stage.
**Narration is Sahil's; this document gives the team the *facts about
the system* so they can wrap their own words around them.**

For each failure mode: the symptom (what the operator sees), the
recovery action (what to click or type), and the *system fact* that
makes the failure non-fatal — never a script of what to say.

---

## 1. OPA is not running

**Symptom**
- Red banner on the dashboard: "● OPA policy engine is not reachable."
- "Run evaluation" button errors out.

**Recovery**
- In a separate terminal: `deploy/bin/opa run --server --addr 127.0.0.1:8181 --log-level error deploy/rules/`
- Wait ~2 seconds for OPA to come up.
- Reload the dashboard. Banner disappears.

**System fact (for narration)**
- Praman never silently disqualifies a bidder when OPA is down. The
  dashboard's red banner is the explicit fail-safe.
- Production: OPA runs as a sidecar container, not as a separate
  manual process.

---

## 2. Tailwind / HTMX / AG-Grid CDN unreachable

**Symptom**
- Page loads with NO styling (raw HTML, default browser fonts).
- AG-Grid shows blank `<div>` instead of the verdict matrix.

**Recovery**
- Pre-load every demo page on the venue WiFi at least 30 minutes
  before the demo, so the browser caches all three CDN assets.
- If still broken on stage: switch to mobile hotspot. Reload pages.
- Fallback: open the architecture.svg on screen instead of the live
  grid. The verdict matrix data is also in
  `/officer/rfp/21/grid.json` (raw JSON).

**System fact**
- HTMX 2.0.3 and AG-Grid v33 are SRI-pinned on the templates. If they
  load at all, they're verified. Day-14 dry-runs should vendor these
  locally to remove the CDN dependency entirely.

---

## 3. Snapshot DB is stale or diverged

**Symptom**
- Yellow warning on `bootstrap_demo`: "Live DB diverges from
  db.sqlite3.demo snapshot."
- OR: verdict matrix doesn't match the expected A=5×PASS / B=4×PASS+FAIL /
  C=4×PASS+ABSTAIN pattern.

**Recovery**
- One command: `python manage.py bootstrap_demo --restore-snapshot`
- The live DB is overwritten with the committed snapshot.
- Re-open the dashboard. Verdict matrix matches.

**System fact**
- Day-12 added `db.sqlite3.demo` (committed snapshot) + Day-13 added
  the freshness check + `--restore-snapshot` flag. Demo state is
  always recoverable.

---

## 4. Gemini API is rate-limited or down (shouldn't matter for demo)

**Symptom**
- `bootstrap_demo` (cold path) hangs at `extract_facts` with
  "DEADLINE_EXCEEDED" or "429 Too Many Requests".

**Recovery**
- Use the snapshot: `python manage.py bootstrap_demo --restore-snapshot`.
- The snapshot bakes in the `GeminiCallCache` table — every prompt+image
  hash that the demo flow needs is already cached.
- The demo path **never calls Gemini live**. If it tries to, the
  snapshot was missing or out of date.

**System fact**
- The snapshot decouples demo execution from Gemini API availability.
- Production: Qwen2.5-VL via vLLM runs locally — no external API calls
  at all.

---

## 5. PyMuPDF page render fails

**Symptom**
- Clicking an ABSTAIN cell → drilldown loads, but the evidence card
  shows "Failed to load evidence" instead of the page image with bbox
  overlay.

**Recovery**
- Reload the drilldown page. PyMuPDF is deterministic; if it failed,
  it'll fail again. Check that the underlying PDF exists at
  `media/documents/<sha-prefix>/<filename>` and that the page number
  is in range.
- Fallback: read the verdict's `bindings_json` aloud from the
  verdict_detail page. The rule_id and bindings are still rendered.

**System fact**
- PyMuPDF is the page-rasterization layer. It's CPU-only and bundled
  in the venv; no network dependency.
- Day-11 fix R3 surfaces "citation could not be rendered" warnings
  visibly when this happens.

---

## 6. PDF signing fails mid-export

**Symptom**
- Sign-off button click → progress spinner → red error message:
  "Sign-off failed: <something>".

**Recovery**
- Common cause: `deploy/demo_signer.p12` was deleted or replaced.
  The cert auto-regenerates on first use, so just click sign-off
  again — second attempt usually works.
- If repeated failure: open the signoff page; the PDF history table
  may have a previously-signed PDF still on disk that the team can
  download for the demo.

**System fact**
- The audit chain is appended only after the PDF is signed and
  written. If signing fails, the chain stays consistent — there's no
  half-signed state.
- Production: the swap is to a CCA Class-3 DSC token; same code path,
  different signer.

---

## 7. Audit chain integrity broken (verify_chain reports problems)

**Symptom**
- Red banner on the signoff page: "● Audit chain integrity broken (N
  problems detected at render time)."
- The audit pill in the header turns red.

**Recovery**
- Day-13 fix C1: this state should never happen on a fresh
  `--restore-snapshot` because the snapshot was created when the chain
  was green.
- Run `python manage.py verify_audit_chain` in a terminal to see
  exactly which seq is broken.
- If it's a Day-1 demo dry-run problem, `--restore-snapshot` resets
  everything.

**System fact**
- The Merkle chain is RFC 8785 canonical JSON + SHA-256. Tampering
  with one entry breaks every entry from that seq forward — the
  damage is precisely localized.
- The chain check happens at *render time* on the signoff page, not
  just on demand. A juror can't tamper with a row in /admin/ and have
  the timeline display it as if nothing happened.

---

## 8. Browser scaling at projector resolution

**Symptom**
- AG-Grid column widths look wrong, sidebar overlaps content.

**Recovery**
- Press `Ctrl+0` (or `Cmd+0` on Mac) to reset zoom.
- If projector is non-1080p: pre-zoom to a level where the layout is
  clean, before the demo starts.

**System fact**
- The UI is built with Tailwind responsive classes (md:, lg:). Tested
  at 1920×1080. At lower resolutions the sidebar collapses on
  `md:hidden` breakpoint by design.

---

## 9. Anything else: the always-safe fallback

If a demo step breaks irrecoverably:

- The `verify_day11` acceptance gate output is a static text record of
  the verdict matrix + the audit chain head hash + the signed PDF
  SHA-256 — read it from a terminal.
- The architecture.svg + the deck slides are static and never break.
- The signed PDF is on disk under `media/evidence/`. Open it directly
  (no Praman UI needed) to show the embedded head hash + valid
  signature.

The juror evaluation is on:
1. Technical implementation & innovation (25%)
2. Real-world deployability & govt feasibility (25%)
3. Problem relevance & depth of understanding (20%)
4. Demo quality & presentation (15%)
5. Scalability & long-term impact (15%)

A demo glitch hits #4 (15%). The other 85% is in the slides + the
deck narrative + the audit-trail proofs. Don't panic over a glitch.

---

## Pre-demo readiness checklist

Run this 30 minutes before the demo:

```bash
# 1. Snapshot is current
ls -la db.sqlite3.demo  # exists, recent mtime
python manage.py verify_day11 --skip-bootstrap  # PASS

# 2. OPA is running
deploy/bin/opa run --server --addr 127.0.0.1:8181 --log-level error deploy/rules/ &
curl -sS http://127.0.0.1:8181/health  # 200 OK

# 3. Django is running
python manage.py runserver 0.0.0.0:8000
# Browser: http://localhost:8000/  → login → dashboard renders

# 4. CDN assets are cached in the browser
# Visit each hero page once: dashboard, criteria, eval grid, drilldown, signoff
# This warms the browser cache against CDN failure

# 5. Audit pill is green
# Top-right corner of every page shows "● Audit chain GREEN (N entries)"
```

If all five are green, the demo is ready.
