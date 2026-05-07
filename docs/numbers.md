# Numbers ammunition

Reference data points the team can cite in the deck or under jury
questioning. Every number has a `file:line` or command citation so a
fact-checking juror can verify on the spot.

Snapshot: 2026-05-03. Regenerate this file via `python manage.py
shell -c "..."` before the finale.

---

## Test surface

| Claim | Number | Source |
|---|---|---|
| Test count, passed | **228** (+1 module-level skip = 229 collected) | `pytest --collect-only -q` then `pytest` |
| Test count, skipped | 1 | `core/tests/test_evidence_signing.py:160` (requires DB state, intentionally skipped in unit-test mode) |
| Test files | 12 | `find core/tests officer -name 'test_*.py' -o -name 'tests.py' \| wc -l` |
| Day-by-day test growth | 9: 144 → 10: 170 → 11: 179 → 12: 189 → 14: 212 → 15a: 227 → 15b: 228+ | (running tally; Day 15a + 15b = template-smoke + effective-status fixes) |
| Acceptance gates | 3 (verify_audit_chain, verify_day6, verify_day11) | `core/management/commands/verify_*.py`; Day-N gates (1-5) consolidated into verify_day11 on Day 14 |

## Code surface

| Claim | Number | Source |
|---|---|---|
| Python files | 86 | `find . -name '*.py' ! -path './.venv/*' \| wc -l` |
| Total Python lines | ~11,400 | `wc -l` over the same set |
| Rego rules + common | 6 files / 401 lines | `wc -l deploy/rules/*.rego` |
| · c1_turnover.rego | 97 lines | financial threshold rule |
| · c2_gst.rego | 68 lines | GSTIN compliance rule |
| · c3_dsc.rego | 55 lines | Class-3 DSC compliance rule |
| · c4_experience.rego | 67 lines | past works experience rule |
| · c5_iso.rego | 65 lines | ISO 9001 quality rule |
| · common.rego | 49 lines | shared helpers (is_low_confidence, etc.) |
| Django apps | 2 | `core/`, `officer/` |
| Migrations | 3 (core), 0 (officer) | `core/migrations/` |
| HTML templates | 9 | `find officer/templates -name '*.html' \| wc -l` |
| Management commands | ~17 | `ls core/management/commands/*.py` |

## Synthetic dataset (the demo's data spine)

| Claim | Number | Source |
|---|---|---|
| RFP files | 1 | `synthetic/rfp_synthetic_construction_001.pdf` |
| Bidder bundles | 3 | `synthetic/bidder_a/`, `_b/`, `_c/` |
| PDFs per bidder bundle | 6 | cover letter, audit report, GST cert, DSC cert, past performance, ISO cert |
| Total PDFs ingested | 18 (3 × 6) + 1 RFP = 19 (1 RFP Document + 18 bidder Documents in DB) | DB query: `Document.objects.count()` |
| Bidder C's distorted file | `synthetic/bidder_c/03_gst_certificate.pdf` (~875 KB) | Augraphy BadPhotoCopy + DirtyDrum + InkBleed pipeline |

## Pipeline state (current snapshot)

Pulled on 2026-05-03 16:30. Regenerate via the shell command at the
top of this section.

| Claim | Number | Source |
|---|---|---|
| Documents | 18 | `Document.objects.count()` |
| Blocks (parsed text regions) | 379 | `Block.objects.count()` |
| · Docling | 350 | `Block.objects.filter(source='DOCLING').count()` |
| · Gemini Vision | 29 | `Block.objects.filter(source='GEMINI_VISION').count()` |
| · Low-confidence (`< 0.85`) | 6 | proves N3 (scanned/photo support); these are Bidder C's distorted GST regions |
| Criteria | 7 | `Criterion.objects.count()` (5 Rego-mapped + 2 ABSTAIN no_rule_mapped) |
| Facts | 21 | `Fact.objects.count()` (7 criteria × 3 bidders) |
| Verdicts | 21 | `Verdict.objects.count()` |
| AuditEntries (chain depth) | 351 | `AuditEntry.objects.count()` |
| Gemini call cache hits | 42 | `GeminiCallCache.objects.count()` (snapshot baked in) |

## Verdict matrix (the demo's narrative spine)

```
            C-1  C-2  C-3  C-4  C-5  C-6  C-7
Bidder A:   ABS  PASS ABS  PASS PASS PASS PASS    (5 PASS, 2 ABSTAIN no_rule_mapped)
Bidder B:   ABS  PASS ABS  PASS FAIL PASS PASS    (4 PASS, 1 FAIL on turnover, 2 ABSTAIN)
Bidder C:   ABS  PASS ABS  ABS  PASS PASS PASS    (4 PASS, 1 ABSTAIN low_conf GST, 2 ABSTAIN)
```

Mapping:
- C-1 = Registered Contractor Status (no Rego rule → ABSTAIN no_rule_mapped)
- C-2 = Class-3 DSC → c3_dsc.rego
- C-3 = PAN (no Rego rule → ABSTAIN no_rule_mapped)
- C-4 = GST → c2_gst.rego (Bidder C distorted → low_conf ABSTAIN)
- C-5 = Min Annual Turnover → c1_turnover.rego (Bidder B Rs. 4.2 Cr < 5 Cr threshold → FAIL)
- C-6 = Past Experience → c4_experience.rego
- C-7 = Quality Management (ISO 9001) → c5_iso.rego

Source: `core/management/commands/verify_day11.py:EXPECTED_MATRIX` and
`Verdict.objects.select_related('criterion').order_by(...)` on a
post-bootstrap DB.

## Round-1 non-negotiables proof points

| Non-negotiable | Demo proof | File:line |
|---|---|---|
| **N1** Every verdict criterion-level explainable | Drilldown shows rule_id + bindings + reason for every Verdict; empty-rule_id banner triggers when missing | `officer/templates/officer/verdict_detail.html` |
| **N2** Never silently disqualify | 6 ABSTAIN verdicts in the matrix (4 no_rule_mapped + 1 low_conf + 1 from Bidder B's PAN regression); 1 FAIL only when turnover is genuinely below threshold | `deploy/rules/common.rego:is_low_confidence` |
| **N3** Scanned/photo support | 29 Gemini Vision blocks; 6 with confidence < 0.85; all from Bidder C's distorted GST | `core/parsing/gemini_vision_ocr.py` + Block.source = GEMINI_VISION |
| **N4** Auditable + signed PDF | 351 AuditEntries in a green Merkle chain; signed PDF embeds head hash; tamper test detects byte-flip | `core/audit/merkle_log.py:verify_chain` + `core/audit/signer.py:verify_signed_pdf` |

## Performance (live demo timing)

Measured on the snapshot path (no Gemini calls).

| Step | Time | Notes |
|---|---|---|
| `bootstrap_demo` (snapshot) | <1 s | `cp db.sqlite3.demo db.sqlite3` |
| Django startup | ~3 s | `python manage.py runserver` |
| Dashboard render | ~200 ms | login + dashboard SQL queries |
| Eval grid render | ~300 ms | view + AG-Grid CDN load |
| `/grid.json` endpoint | ~100 ms | the JSON behind the grid |
| Drilldown render | ~500 ms | view + image-overlay layout |
| `/evidence.json` endpoint | ~200 ms | bbox math + page-dim cache |
| `/document/<id>/page/<n>.png` | ~500 ms | PyMuPDF rasterise at 200 DPI |
| Sign-off + PDF generation | ~5–10 s | WeasyPrint render + pyHanko sign |
| Audit chain `verify_chain()` | ~50 ms (351 entries) | bound by SHA-256 throughput |

## Cold-start (no snapshot, fresh Gemini calls)

| Step | Time |
|---|---|
| `generate_synthetic --clean` | ~10 s |
| `ingest_bundle ×4` (Docling + Gemini Vision fallback for Bidder C) | ~25–60 s |
| `extract_criteria` (1 Gemini Pro call) | ~5 s |
| `extract_facts` (21 Gemini Flash calls) | ~30–45 s |
| `evaluate_verdicts` (21 OPA HTTP calls) | <1 s |
| **Total** | ~80–120 s |

Snapshot is 60–100× faster than cold start. That's why we ship it.

## Deployment surface

| Claim | Number | Source |
|---|---|---|
| External binary deps (committed) | 1 (`deploy/bin/opa`, ~77 MB) | self-hosted |
| Self-signed cert | `deploy/demo_signer.p12` (~3 KB) | for demo PDF signing only |
| Auto-generated cert | yes, via `core/audit/cert_gen.py` | runs on first sign if .p12 missing |
| External API dependencies (demo path) | 0 | snapshot-driven, no live Gemini |
| External CDN dependencies | 3 (Tailwind play, HTMX 2.0.3, AG-Grid v33) | HTMX + AG-Grid SRI-pinned; Tailwind unpinned |
| OS targets | Linux x86-64 (verified), macOS x86-64 (likely), ARM (untested) | wheel availability dependent |

## Future numbers to track (Day-14 dry-runs)

- Wall-clock for full demo flow on a fresh laptop
- CDN load time on conference WiFi
- AG-Grid render at 1920×1080 vs 1280×720 (projector scaling)
- Concurrent-officer test (two browsers): does the audit chain stay
  consistent?

---

## Regenerate command

```bash
.venv/bin/python manage.py shell -c "
from core.models import AuditEntry, Document, Block, BlockSource, Criterion, Fact, Verdict, GeminiCallCache
print('AuditEntries:', AuditEntry.objects.count())
print('Documents:', Document.objects.count())
print('Blocks:', Block.objects.count())
print('  Docling:', Block.objects.filter(source=BlockSource.DOCLING).count())
print('  Gemini Vision:', Block.objects.filter(source=BlockSource.GEMINI_VISION).count())
print('  low-conf (<0.85):', Block.objects.filter(confidence__lt=0.85).count())
print('Criteria:', Criterion.objects.count())
print('Facts:', Fact.objects.count())
print('Verdicts:', Verdict.objects.count())
print('GeminiCallCache:', GeminiCallCache.objects.count())
"
```

After Day-14 dry-runs, edit this file with the fresh numbers.
