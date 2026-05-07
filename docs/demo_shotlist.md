# 5-minute demo shot list

What to show on screen, minute-by-minute. **Narration is owned by
Sahil; this document is the *visual sequence* the team uses to record
the demo video over Days 14-15.**

Total target: 4:30 + 30s buffer. Demo flow assumes:
- Snapshot loaded (`bootstrap_demo` already ran).
- OPA running on `127.0.0.1:8181`.
- Browser at full-screen 1920×1080.
- Logged in as `officer` / `praman_demo_2026`.

---

## Pre-roll · 0:00–0:15 — Setup shot

| Time | What's on screen | Notes |
|---|---|---|
| 0:00 | Praman logo + tagline (deck slide 1) | static |
| 0:05 | "Live demo" lower-third graphic | static |
| 0:10 | Browser opens to `http://localhost:8000/officer/` (already logged in) | dashboard |

**Why this matters:** the deck has been talking; now we move to the
running system. The transition signals "this is real, not vapor."

---

## Section A · 0:15–0:45 — Dashboard + audit pill

| Time | What's on screen | Element to highlight |
|---|---|---|
| 0:15 | `/officer/` dashboard | top-right audit pill (green ● Audit chain GREEN, N entries) |
| 0:25 | Hover sidebar nav | five-step rail visible: Upload → Criteria → Eval grid → Drill-down → Sign-off |
| 0:30 | RFP list table | one row: `rfp_synthetic_construction_001.pdf` |
| 0:35 | Click "Criteria" link in the row | navigates to criteria_review |

**Why this matters:** establishes the "running system, audit chain
already alive" story. The audit pill is N4 (auditability) made
visible from the very first frame.

---

## Section B · 0:45–1:30 — Criteria review

| Time | What's on screen | Element to highlight |
|---|---|---|
| 0:45 | `/officer/rfp/21/criteria/` | 7 criteria cards visible |
| 0:55 | Scroll through 7 criteria | C-1 through C-7, each with mandatory/optional toggle pill |
| 1:05 | Hover the MANDATORY pill on C-5 (Turnover) | shows "click to toggle" tooltip |
| 1:10 | Click MANDATORY pill on any criterion | HTMX swap: pill flips to OPTIONAL, page does not reload |
| 1:15 | Audit pill in header (top-right) updates count by 1 | live evidence the toggle was logged |
| 1:20 | Click MANDATORY pill again to restore | HTMX swap back |
| 1:25 | Click "Verdict grid" link at bottom of page | navigate to eval_grid |

**Why this matters:** demonstrates HTMX inline editing + the live
audit-chain capture. Each toggle is a hash-chained event.

---

## Section C · 1:30–2:30 — Eval grid (the hero shot)

| Time | What's on screen | Element to highlight |
|---|---|---|
| 1:30 | `/officer/rfp/21/grid/` | AG-Grid renders, 3 rows × 7 columns |
| 1:40 | Pause on the matrix | bidder rows: A (5 PASS, 2 ABSTAIN), B (4 PASS, 1 FAIL on C-5, 2 ABSTAIN), C (4 PASS, 1 ABSTAIN on C-4, 2 ABSTAIN) |
| 1:50 | Hover Bidder B × C-5 (FAIL, red pill) | tooltip shows rule_id="c1.fail.turnover_below_threshold" |
| 2:00 | Hover Bidder C × C-4 (ABSTAIN, amber pill) | tooltip shows rule_id="c2.abstain.low_confidence" |
| 2:10 | Hover Bidder A × C-1 (ABSTAIN, amber pill) | tooltip shows rule_id="no_rule_mapped" |
| 2:20 | Click Bidder C × C-4 cell | navigate to verdict_detail |

**Why this matters:** all three verdict types (PASS/FAIL/ABSTAIN)
visible in one frame. The two ABSTAIN flavors (low_confidence vs
no_rule_mapped) are both surfaced — that's N2 (never silently
disqualify).

---

## Section D · 2:30–3:30 — Drilldown (the deepest shot)

| Time | What's on screen | Element to highlight |
|---|---|---|
| 2:30 | `/officer/verdict/<id>/` for Bidder C × C-4 | verdict header: "C-4 · Bidder C [ABSTAIN]" |
| 2:40 | Verdict reasoning card | rule_id "c2.abstain.low_confidence", bindings JSON (confidence_threshold: 0.85, ocr_confidence: 0.80) |
| 2:50 | Reason text | "OCR / extraction confidence 0.80 is below the threshold 0.85 — manual review required." |
| 3:00 | Scroll to evidence card | image of distorted GST cert page with red bbox overlay on the GSTIN region |
| 3:10 | Reason_if_low text under the snippet | "OCR uncertainty: <Gemini's per-region reason>" |
| 3:20 | Scroll to override form | shows the form for filing an OverrideRequest |
| 3:25 | Pause on the form | static |

**Why this matters:** this is the **N1 + N3 single-frame shot**:
- N1 (explainable): rule_id, bindings, reason all visible
- N3 (scanned/photo): the Gemini Vision low-conf bbox is visible

Optional B-roll: split-screen with Bidder A's clean GST cert (Docling
parse, no red bbox, conf=1.0) for contrast.

---

## Section E · 3:30–4:15 — Sign-off + signed PDF

| Time | What's on screen | Element to highlight |
|---|---|---|
| 3:30 | Click "Sign-off" in the sidebar | navigate to signoff page |
| 3:35 | `/officer/rfp/21/signoff/` | audit timeline visible (last 20 entries, hash-chained) |
| 3:45 | Multi-PDF history table (right column) | already-signed PDFs from prior runs, with "current" + "superseded" badges |
| 3:55 | Click "Sign off & export evidence PDF" | button submits |
| 4:00 | Wait ~3-5s | progress: WeasyPrint renders HTML → PDF, then pyHanko signs |
| 4:05 | Page reloads | new entry top of audit timeline (`evidence.signed`), new row top of PDF history |
| 4:10 | Click "Download current PDF" | browser downloads the signed PDF |

**Why this matters:** N4 (auditable + signed PDF) made visible. The
new audit entry is appended in real time; the signed PDF embeds the
head hash that was current at sign-time.

---

## Section F · 4:15–4:45 — Live tamper test (the closer)

| Time | What's on screen | Element to highlight |
|---|---|---|
| 4:15 | Switch to a terminal | full-screen terminal |
| 4:20 | Show `verify_signed_pdf` for the freshly downloaded PDF | output: `signature_intact: True` |
| 4:25 | Open the PDF in a hex editor | scroll to a body byte |
| 4:30 | Flip one byte (e.g. `0x41` → `0x42`) | save |
| 4:35 | Re-run `verify_signed_pdf` | output: `signature_intact: False` |
| 4:40 | Pause on the False output | static |

**Why this matters:** the final demo beat. Cryptographic tamper
detection is no longer abstract — the audience saw a single byte get
flipped and the system catch it. This is the "you can't fake this"
proof of N4.

---

## Tail · 4:45–5:00 — Wrap to deck slide 7 (production roadmap)

| Time | What's on screen | Notes |
|---|---|---|
| 4:45 | Browser closes / desktop visible | back to deck |
| 4:50 | Slide 7 (production roadmap) appears | static |
| 5:00 | End | static |

---

## Recording checklist (Days 14-15)

- [ ] Browser at 1920×1080 (matches projector aspect ratio)
- [ ] Mouse cursor visible (some screen recorders hide it)
- [ ] Audio captured separately (narration is layered later — Sahil's voice)
- [ ] Each section starts and ends on a static frame (clean cuts)
- [ ] `verify_day11 --skip-bootstrap` returns PASS before recording starts
- [ ] OPA running, no red banners visible
- [ ] Audit pill is GREEN throughout
- [ ] No browser console errors (open dev tools and verify pre-roll)
- [ ] Screenshot of every section saved to `docs/screenshots/` for slide use

## Files this shot list references

- `officer/templates/officer/dashboard.html` — section A
- `officer/templates/officer/criteria_review.html` — section B
- `officer/templates/officer/eval_grid.html` — section C
- `officer/templates/officer/verdict_detail.html` — section D
- `officer/templates/officer/signoff.html` — section E
- `core/audit/signer.py:verify_signed_pdf` — section F
