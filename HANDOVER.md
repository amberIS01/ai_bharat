# Praman · Team Handover

**Single-source-of-truth for teammates.** If you're reading this, Sahil
is the solo builder; you're contributing the pitch deck design, the
demo video recording, or dry-run verification. This doc tells you
exactly what's built, what's pending, how to run it, how to record the
demo, and where every file lives.

> **Sahil writes the narration himself.** He defends it on stage in
> front of IAS officers and VCs. Speaker notes, verbatim sentences, and
> what-to-say belong to him alone. This doc only gives you the
> *structural* artifacts — the slide skeleton, the shot list, the
> recovery checklist, the reference numbers.

---

## 1 · What this project is (60 seconds)

**Praman** (प्रमाण = "proof, evidence") is Sahil's Round-2 submission to
the **AI for Bharat hackathon** (PAN IIT Bangalore + Government of
Karnataka). 

- **Theme 3 — AI-Based Tender Evaluation and Eligibility Analysis for
  Government Procurement by CRPF.**
- **Grand finale: 2026-05-16, Taj Yeshwantpur, Bengaluru.**
- **Audience:** IAS officers + VCs + domain mentors. Five-minute demo
  slot.

### The problem

CRPF officers manually evaluate 50+ page tenders against 5–7
eligibility criteria across multiple bidders. Takes **3–5 days per
evaluation**. ~20 % of rejections get challenged in court (CVC, RTI,
CAG). Evidence trail is paper, often incomplete.

### The insight

> *LLMs extract. Rego decides.*

LLMs (Gemini 2.5 Pro/Flash + Vision) parse the messy PDFs and pull
out facts. **OPA + Rego rules** — one rule per criterion category —
compute the actual verdict. The LLM never decides. The Rego rule
does. That's how we get a verdict that's defensible in court.

### The four Round-1 non-negotiables

| # | Commitment | Mechanism |
|---|---|---|
| 1 | Every verdict criterion-level explainable | `Verdict.rule_id` + `bindings_json` + `reason` + `evidence_refs` (bbox-cited blocks) |
| 2 | Never silently disqualify | Low-confidence / missing facts → `ABSTAIN` (manual review), never `FAIL` |
| 3 | Scanned + photo support | Docling (digital PDFs, BOTTOMLEFT bbox) + Gemini Vision (scanned/distorted PDFs, low-confidence reasons) |
| 4 | End-to-end auditable + signed evidence | Merkle-chained `AuditEntry` (RFC-8785 canonical JSON + SHA-256 + chained hashes) + pyHanko PAdES-B-signed PDF embedding the chain head hash |

---

## 2 · Architecture in one diagram

The full diagram is at [`docs/architecture.svg`](docs/architecture.svg).
The five lanes:

| Lane | What | Files |
|---|---|---|
| 1 · Ingest | PDF → Docling (digital) \| Gemini Vision (scanned) → `Block` rows | `core/parsing/docling_parser.py`, `core/parsing/gemini_vision_ocr.py`, `core/management/commands/ingest_bundle.py` |
| 2 · Extract | Block manifest → Gemini 2.5 Pro (criteria) → Gemini 2.5 Flash (facts per bidder) → `Criterion` + `Fact` rows | `core/extraction/criteria_extractor.py`, `core/extraction/facts_extractor.py`, `core/extraction/gemini_client.py` |
| 3 · Evaluate | Fact + Criterion → `parsers.py` (parse rupee / GSTIN / DSC class / works / ISO) → OPA + Rego → `Verdict` row | `core/policy/parsers.py`, `core/policy/opa_client.py`, `deploy/rules/*.rego` |
| 4 · Audit & sign | Django signals → Merkle chain → `AuditEntry` → pyHanko PAdES-B → signed PDF | `core/audit/merkle_log.py`, `core/audit/serializers.py`, `core/audit/signals.py`, `core/audit/pdf_export.py`, `core/audit/signer.py` |
| 5 · Officer UI | 5 hero screens (upload → criteria → grid → drilldown → sign-off) | `officer/views.py`, `officer/templates/officer/*.html`, `static/praman.css`, `static/praman-motion.js` |

### Production roadmap (greyed in the diagram)

- **LLM:** Gemini hosted API → local Qwen2.5-VL via vLLM (true air-gap)
- **Audit log:** SQLite + Python Merkle chain → Google Trillian (verifiable log) or immudb
- **PDF signing:** Self-signed X.509 → CCA Class-3 DSC token via PKCS#11 (eMudhra ProxKey, ePass2003)
- **Hosting:** Local laptop / Docker → on-prem CRPF intranet, no internet egress

---

## 3 · What's already built (Days 1–15)

| Day | Outcome | Test count cumulative |
|---|---|---|
| 1 | Django scaffold + synthetic dataset (1 RFP + 3 bidders) | — |
| 2 | Document/Block/Criterion/Fact/Verdict models + Docling parser + Gemini Vision OCR | — |
| 3 | Gemini criteria + facts extraction with `response_schema` (Pydantic) | — |
| 4 | OPA server + 5 Rego rules (turnover, GST, DSC, experience, ISO) | — |
| 5 | Merkle-chained audit log (RFC-8785 + SHA-256) | — |
| 6 | pyHanko PAdES-B signed evidence PDF + tamper-detection | — |
| 7 | Officer-facing views (upload, criteria, grid, drilldown, signoff) | — |
| 8 | Django admin scaffolding for back-office | — |
| 9 | AG-Grid eval grid + JSON endpoints + image-overlay drilldown | 156 |
| 10 | Hero screens polish + 8 demo-defensive RED fixes | 170 |
| 11 | Pipeline alignment fix (verdict matrix correct) | 179 |
| 12 | Demo-snapshot mechanism (`db.sqlite3.demo`) + architecture SVG | 189 |
| 13 | Deck artifacts: outline, shot list, recovery, numbers, QUICKSTART | 202 |
| 14 | Vendored CDN deps + `cleanup_evidence_dir` + `verify_demo` pre-flight | 212 |
| 15 | All 9 reviewer audit findings closed (effective-status, override propagation, etc.) | **228** |

**Live build status:** `pytest` → **228 passed, 2 skipped, 0 failed**.
`verify_day11 --skip-bootstrap` → **PASS**. `verify_demo` → **PASS**.

### Verdict matrix (the demo's narrative spine)

| Bidder | C-1 (Reg.) | C-2 (DSC) | C-3 (PAN) | C-4 (GST) | C-5 (Turnover) | C-6 (Experience) | C-7 (ISO) |
|---|---|---|---|---|---|---|---|
| **A** (clean) | ABSTAIN¹ | PASS | ABSTAIN¹ | PASS | PASS | PASS | PASS |
| **B** (low turnover) | ABSTAIN¹ | PASS | ABSTAIN¹ | PASS | **FAIL** ² | PASS | PASS |
| **C** (distorted GST) | ABSTAIN¹ | PASS | ABSTAIN¹ | **ABSTAIN** ³ | PASS | PASS | PASS |

¹ no Rego rule mapped (system correctly routes pre-quals to manual review)
² turnover Rs. 4.2 Cr below Rs. 5 Cr threshold (real FAIL)
³ Bidder C's GST cert is intentionally distorted — Gemini Vision reads it at confidence 0.80 → below 0.85 threshold → ABSTAIN low_confidence (the demo's marquee narrative)

---

## 4 · What's NOT built (deferred — explicit rationale)

These items are documented in [`FREEZE.md`](FREEZE.md) and the latest
plan-file Day-15 detail. None of them block the demo.

| # | Item | Why deferred |
|---|---|---|
| 1 | Multi-RFP scoping (right now `bidder_codes` + extraction + evaluation are global) | Architectural; ~150 LOC; demo runs on one RFP — no cross-contamination on stage |
| 2 | Playwright browser smoke tests in CI | Needs `pip install playwright && playwright install chromium` (~150 MB download); manual click-through on Day-16 morning suffices |
| 3 | Mobile / responsive layout | Demo runs at 1080p projector; not the audience |
| 4 | True air-gap (replace Gemini with local Qwen2.5-VL) | Production roadmap; demo uses hosted Gemini with cached responses |
| 5 | CCA Class-3 DSC signing via PKCS#11 (eMudhra/ePass2003) | Demo uses self-signed cert with explicit disclosure on the signoff page; production swap is one signer-config change |

---

## 5 · How to run the project locally

### One-time setup (any machine)

```bash
git clone https://github.com/amberIS01/ai_bharat.git
cd ai_bharat
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt    # ~80 deps; takes 2-3 minutes
python manage.py migrate
```

### Every-time-you-want-to-run setup

```bash
# Terminal 1 — start OPA (must stay running)
deploy/bin/opa run --server --addr 127.0.0.1:8181 --log-level error deploy/rules/

# Terminal 2 — restore the demo state from snapshot (instant)
python manage.py bootstrap_demo --restore-snapshot

# Terminal 2 — boot Django
python manage.py runserver 0.0.0.0:8000
```

Open http://localhost:8000/ and log in:

- **Username:** `officer`
- **Password:** `praman_demo_2026`

The credentials live HERE, in this handover doc and in
[`docs/QUICKSTART.md`](docs/QUICKSTART.md). They do **NOT** appear on
the login screen — that's intentional (a CRPF tender system showing
its password on the login page is a credibility killer).

### Pre-flight check before recording or demo

```bash
python manage.py verify_demo
```

Must end with `=== verify_demo PASS — system is demo-ready ===`. If
anything fails, see [`docs/FALLBACK_PLAN.md`](docs/FALLBACK_PLAN.md).

### Run the test suite

```bash
pytest                    # 228 passed, 2 skipped expected
pytest -v core/tests/test_day11_e2e.py   # the load-bearing acceptance tests
```

### Tear down

```bash
pkill -9 -f "runserver 0.0.0.0:8000"
pkill -9 -f "deploy/bin/opa"
```

---

## 6 · Repository file map

```
ai_bharat/
├── README.md                    overview, links to docs
├── HANDOVER.md                  ← you are here
├── FREEZE.md                    frozen-build state record
├── manage.py                    Django entry point
├── pyproject.toml               Python deps
├── pytest.ini                   test config (slow + network markers)
├── tailwind.config.js           CSS build config
├── db.sqlite3.demo              committed DB snapshot (745 KB)
│
├── praman/                      Django project root
│   ├── settings.py              env config, INSTALLED_APPS
│   └── urls.py                  root URLconf
│
├── core/                        domain app
│   ├── models.py                Document, Block, Criterion, Fact, Verdict, AuditEntry, OverrideRequest
│   ├── parsing/                 Docling + Gemini Vision OCR
│   ├── extraction/              Gemini criteria + facts
│   ├── policy/                  parsers.py + opa_client.py + effective.py (override propagation)
│   ├── audit/                   merkle_log.py + signers + pdf_export
│   ├── synthetic/               WeasyPrint templates for fake RFPs
│   ├── management/commands/     bootstrap_demo, verify_demo, verify_day11, snapshot_demo_db, ...
│   ├── tests/                   ~140 pytest tests
│   └── admin.py                 Django admin (back-office only)
│
├── officer/                     officer-facing UI
│   ├── views.py                 dashboard, criteria_review, eval_grid, verdict_detail, signoff, ...
│   ├── urls.py                  /officer/* routes
│   ├── forms.py                 RFPUploadForm, OverrideRequestForm, CriterionInlineEditForm
│   ├── rendering.py             page-image rasterization + bbox math
│   ├── templates/officer/       all 8 templates
│   ├── tests.py                 view-level tests
│   ├── test_day9.py             AG-Grid + drilldown tests
│   └── test_day10.py            sidebar removal, override marker, etc.
│
├── deploy/
│   ├── bin/opa                  OPA binary (73 MB) — committed for instant setup
│   ├── bin/tailwindcss          standalone Tailwind CLI (41 MB) — committed
│   └── rules/                   *.rego policy rules (one per criterion category)
│
├── static/
│   ├── praman.css               36 KB — the "Bharat Ledger" design system
│   ├── praman-motion.js         18 KB — GSAP orchestration + view transitions + Cmd-K
│   └── vendor/                  GSAP, ScrollTrigger, Ninja Keys, HTMX, AG-Grid (all committed; zero CDN dependency at runtime)
│
├── templates/auth/
│   └── login.html               clean sign-in (no leaked credentials)
│
└── docs/
    ├── architecture.svg         deck-ready 5-lane diagram (14 KB)
    ├── architecture.png         (regenerate from SVG if your slide tool needs it)
    ├── deck_outline.md          8-slide structural skeleton (NO narration)
    ├── demo_shotlist.md         5-minute click-through, time-stamped
    ├── demo_recovery.md         failure-mode → fallback table
    ├── numbers.md               every claim's reference data
    ├── QUICKSTART.md            7-step operator runbook
    └── FALLBACK_PLAN.md         3-layer fallback (live → video → static deck)
```

---

## 7 · The 5-minute demo flow (what to capture on screen)

Full shot list with timestamps: [`docs/demo_shotlist.md`](docs/demo_shotlist.md).

Quick reference:

| Time | URL / action | What's visible |
|---|---|---|
| 0:00–0:15 | open browser, navigate to /officer/ | dashboard hero with 417 counter, verdict bar, hash chain |
| 0:15–0:45 | click "Verdicts" on the RFP row | AG-Grid bidder × criterion matrix, status tags, hover for rule_id |
| 0:45–1:30 | click `Bidder C × C-4` (the ABSTAIN cell) | drilldown with rotated stamp, evidence card, bbox-overlayed page image, "OCR uncertainty: photocopy noise" caption |
| 1:30–2:30 | navigate to /officer/rfp/21/criteria/ | 7 criteria; click MANDATORY pill — HTMX swap, audit pill in masthead pulses |
| 2:30–3:15 | navigate to /officer/rfp/21/signoff/ | drop-cap cover affidavit, audit chain seal, hash-chain SVG drawing in left-to-right, multi-PDF history |
| 3:15–3:50 | click "Seal & export evidence PDF" | new PDF appears in history, audit timeline grows by 1 entry |
| 3:50–4:30 | terminal: open the new signed PDF in a hex editor, flip one byte | `verify_signed_pdf` reports `signature_intact: False` |
| 4:30–5:00 | back to deck slide 7 (production roadmap) | static |

**Total: ~5:00. Add 30 seconds of buffer for narration over each section.**

### What NOT to show on stage

- The Django admin (`/admin/`) — internal-only
- `docs/QUICKSTART.md` open in the editor — meta
- The terminal running OPA — keep it backgrounded
- The `.env` file — has real Gemini keys; don't ever surface

---

## 8 · Recording the video (technical guide, not narration)

**Sahil records the narration himself, separately.** Your job is the
screen capture + post-production sync.

### Tool

**OBS Studio** (cross-platform, offline-capable, .mkv h264+AAC).
Install: `sudo apt install obs-studio` (Linux), or download from
obsproject.com (Mac/Win).

### Settings

- Canvas: **1920 × 1080** (matches projector)
- FPS: **30**
- Video bitrate: **8 Mbps**
- Audio: **disabled at record time** (Sahil overdubs narration in post)
- Output: `~/demo_recording.mkv`
- Source: Display Capture (full screen)

### Recording sequence

1. Pre-flight: `python manage.py verify_demo` → must PASS
2. Open Chrome at 1920×1080, navigate to http://localhost:8000/officer/
3. Press OBS record. **Pause OBS between sections** — don't record dead time
4. Walk the shot list (see § 7)
5. Stop, save raw .mkv

### Post-production

```bash
# Trim long silences
ffmpeg -i demo_recording.mkv -af silenceremove=1:0:-40dB output_trimmed.mkv

# Compress for upload + slide-deck embed
ffmpeg -i output_trimmed.mkv -c:v libx264 -crf 23 -c:a aac -b:a 128k demo_final.mp4

# Merge Sahil's narration audio (recorded separately)
ffmpeg -i demo_final.mp4 -i sahil_narration.wav \
    -c:v copy -c:a aac -map 0:v:0 -map 1:a:0 \
    demo_with_narration.mp4
```

Final file: `~120 MB`, `.mp4`, `1920×1080`, h264 + AAC. Embed in the
deck as an autoplay-on-click video.

### Where to put the final video

- **In the repo:** `docs/demo_video.mp4` (commit if < 100 MB; use Git
  LFS otherwise — but ideally don't commit; upload to a shared drive)
- **Backup copies:** Google Drive (shared with team), USB stick at
  the venue
- **Filename convention:** `praman_demo_2026-05-16_v1.mp4` etc.

---

## 9 · The 8-slide deck (structural skeleton, no narration)

Full slide-by-slide outline: [`docs/deck_outline.md`](docs/deck_outline.md).

Skeleton:

| # | Slide | What goes on it |
|---|---|---|
| 1 | Problem | CRPF tender eval is slow + legally exposed (5-day evaluation, 20% rejections challenged in court) |
| 2 | Naive AI fails | Hallucination, black box, no trail (3 X-marked failure modes) |
| 3 | Insight | "LLMs extract. Rego decides." (small architecture stub) |
| 4 | Live demo screenshot | the eval grid hero shot — a juror should see PASS/FAIL/ABSTAIN matrix at a glance |
| 5 | Architecture | embed `docs/architecture.svg` |
| 6 | Four non-negotiables → mechanisms | the table from § 1 above |
| 7 | Production roadmap | greyed lanes from architecture.svg |
| 8 | Why CRPF should pilot | numbers from `docs/numbers.md` |

### What the deck designer needs from Sahil (NOT from this doc)

- Final visuals for slide 4 (the live demo hero shot — Sahil
  produces it from a fresh `verify_demo` run)
- Talking points / narration for every slide
- Logo placement choice (Praman wordmark, IIT Bangalore, CRPF, AI for
  Bharat — confirm with Sahil)

---

## 10 · Recovery if something breaks during the demo

Full table: [`docs/demo_recovery.md`](docs/demo_recovery.md).

**Most likely failures + first-response actions:**

| Symptom | Action | Time cost |
|---|---|---|
| Red banner "OPA is not reachable" | Alt-tab to terminal: `deploy/bin/opa run --server --addr 127.0.0.1:8181 --log-level error deploy/rules/` | 10–15 s |
| Audit pill is red ("chain broken") | Run `python manage.py bootstrap_demo --restore-snapshot` to reset to known-good state | 5 s |
| Evaluation produces no verdicts | OPA is down OR pipeline regressed; fall back to pre-recorded video | catastrophic on stage |
| Browser shows "AG Grid failed to load" | All vendored — should never happen now. If it does, the login may have failed; re-log in | 30 s |
| Conference WiFi blocks all CDNs | Praman has zero CDN runtime deps as of Day 14. Only Google Fonts is remote and falls back to system fonts | benign |
| Laptop dies | Pre-recorded `demo_with_narration.mp4` plays from USB stick (Layer 2 fallback — see `docs/FALLBACK_PLAN.md`) | 0 s |
| Both laptops die + venue has no WiFi | Static deck + `docs/architecture.svg` — Layer 3 fallback | 0 s |

---

## 11 · Pending tasks for the team (Days 14–15 + finale)

### Sahil's tasks (he's the only one who can do these)

- [ ] Record narration audio for each video section (his voice)
- [ ] Defend the architecture and answer juror questions live
- [ ] Final dry-run on Day-15 morning at venue WiFi conditions
- [ ] Bring his laptop + backup laptop + USB stick to venue

### Pitch deck designer's tasks

- [ ] Open [`docs/deck_outline.md`](docs/deck_outline.md) — 8-slide structural outline
- [ ] Pull `docs/architecture.svg` into slide 5 (or PNG export if your tool can't render SVG)
- [ ] Pull data from [`docs/numbers.md`](docs/numbers.md) for slides 6 + 8
- [ ] Take a fresh screenshot of `/officer/rfp/21/grid/` for slide 4 (the hero shot)
- [ ] **Do NOT write speaker notes** — Sahil writes them
- [ ] Final deck file: `praman_pitch_2026-05-16.pptx` (or .key)

### Video editor's tasks

- [ ] Set up OBS Studio per § 8
- [ ] Record raw screen capture following [`docs/demo_shotlist.md`](docs/demo_shotlist.md) (Sahil drives the demo, you record)
- [ ] Trim, encode to mp4, prepare audio-merge pipeline
- [ ] Wait for Sahil's narration audio file
- [ ] Final video file: `praman_demo_2026-05-16_v1.mp4`

### Logistics lead's tasks

- [ ] Set up backup laptop on Day-14: fresh git clone + `bootstrap_demo --restore-snapshot` + `verify_demo` PASS
- [ ] Copy final video to USB stick + Google Drive
- [ ] Bring projector adapter (HDMI + USB-C + VGA — all three)
- [ ] Confirm Taj Yeshwantpur WiFi specs in advance if possible
- [ ] Stage at venue 2 hours early

---

## 12 · Files every teammate should read

In priority order:

1. **This file (HANDOVER.md)** — overview + § 11 task split
2. **[`docs/QUICKSTART.md`](docs/QUICKSTART.md)** — operator runbook (deck designer + video editor must be able to run the system)
3. **[`docs/deck_outline.md`](docs/deck_outline.md)** — deck designer reads this
4. **[`docs/demo_shotlist.md`](docs/demo_shotlist.md)** — video editor reads this
5. **[`docs/demo_recovery.md`](docs/demo_recovery.md)** — logistics lead reads this
6. **[`docs/numbers.md`](docs/numbers.md)** — every numerical claim, with file:line citations
7. **[`docs/FALLBACK_PLAN.md`](docs/FALLBACK_PLAN.md)** — 3-layer demo-day fallback
8. **[`docs/architecture.svg`](docs/architecture.svg)** — embed in slide 5; also good orientation for anyone unfamiliar with the codebase
9. **[`FREEZE.md`](FREEZE.md)** — frozen-build state, demo-morning ritual
10. **[`README.md`](README.md)** — short project overview

---

## 13 · FAQ

**Q: I cloned the repo, ran the commands, but `pytest` shows 230 passed instead of 228. Is something wrong?**
A: No. The exact count drifts as new tests are added. The acceptance gate is "0 failed", not a specific pass count.

**Q: I see a `signed_pdf_history` of 5 PDFs even though I only ran sign-off twice. Where did the others come from?**
A: The repo's `db.sqlite3.demo` has accumulated some PDF artefacts from
prior dev runs. Running `python manage.py cleanup_evidence_dir --apply`
clears them. The audit chain is unaffected.

**Q: The Cmd-K palette doesn't show up when I press Ctrl+K.**
A: Ninja Keys binds Cmd+K (Mac) and Ctrl+K (others). If it's not
appearing, check the browser console — `<ninja-keys>` element should be
in the DOM when authenticated.

**Q: I want to test the override workflow. How?**
A: 
1. Click any FAIL cell on the eval grid
2. Submit override request (status=PASS, reason ≥ 20 chars)
3. Open `http://localhost:8000/admin/`, log in, go to OverrideRequest
4. Change status from PENDING to APPROVED, save
5. Refresh the eval grid — cell flips to PASS with a `†` dagger marker
6. Open the verdict drilldown — stamp shows PASS, struck-through FAIL pill, "overridden by … on …" caption
7. Re-export the signed PDF — the PDF matrix now shows the override

**Q: Why is the OPA binary 73 MB committed to the repo? Doesn't that
violate GitHub's recommendations?**
A: Yes, GitHub warns about files > 50 MB. We chose to commit it
anyway because:
- (a) Demo-day reproducibility is more important than repo size
- (b) Asking demo-day operators to download OPA from
  openpolicyagent.org is one more thing that can fail at the venue
- (c) The file is under GitHub's hard 100 MB limit
For production deployment, OPA would come from the package manager.

**Q: Can I add my own tender PDF and test it?**
A: Yes:
```bash
python manage.py ingest_bundle path/to/your.pdf
python manage.py extract_criteria
python manage.py extract_facts
python manage.py evaluate_verdicts
```
But: the Rego rules are tuned to CRPF construction-tender clause
patterns. Other tender types may produce mostly ABSTAIN until rules
are extended.

---

## 14 · Contact

- **Sahil** (solo builder, owns the code + narration): sahilsingh8300@gmail.com
- **Repo:** https://github.com/amberIS01/ai_bharat
- **Hackathon track:** Theme 3 · CRPF · AI for Bharat 2026
- **Finale:** 2026-05-16, Taj Yeshwantpur, Bengaluru

If anything in this doc is wrong, fix it in a PR. The freeze rule is
"no code after Day-15 EOD"; **doc fixes are always welcome.**
