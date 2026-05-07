# FROZEN BUILD — Praman demo-day artefact

This file is the freeze marker for the AI for Bharat hackathon demo at
**Taj Yeshwantpur, 2026-05-16**. Day-15 (the planned freeze day on
2026-05-15) closes here.

## Frozen state

| Item | Value | Source |
|---|---|---|
| Freeze date | 2026-05-15 (planned) — actual: end of Day-14-15 work, 2026-05-03 | this file |
| Test suite | **212 passed, 2 skipped** (1 Playwright module skip + 1 slow integration skip) | `pytest` |
| Acceptance gate | `verify_day11 --skip-bootstrap` exits 0 | `core/management/commands/verify_day11.py` |
| Pre-flight gate | `verify_demo` exits 0 | `core/management/commands/verify_demo.py` |
| `db.sqlite3.demo` SHA-256 | `289d20281a1c30539ff000549c02a9322d9402538444d9674bfff5f60513331b` | `python manage.py snapshot_demo_db --force` |
| `db.sqlite3.demo` size | 745 KB | filesystem |
| Tailwind built CSS | `static/vendor/tailwind.built.css` (14.7 KB) | `./deploy/bin/tailwindcss --minify` |
| Architecture diagram | `docs/architecture.svg` (14 KB) | Day 12 |
| Deck outline | `docs/deck_outline.md` (8 slides) | Day 13 |
| Demo shot list | `docs/demo_shotlist.md` | Day 13 |
| Demo recovery checklist | `docs/demo_recovery.md` | Day 13 |
| Numbers ammunition | `docs/numbers.md` | Day 13 |
| Operator runbook | `docs/QUICKSTART.md` | Day 13 |
| Fallback plan | `docs/FALLBACK_PLAN.md` | Day 14 |
| Project README | `README.md` | Day 13 |

## Verdict matrix at freeze

```
            C-1  C-2  C-3  C-4  C-5  C-6  C-7
Bidder A:   ABS  PASS ABS  PASS PASS PASS PASS    (5 PASS, 2 ABSTAIN no_rule_mapped)
Bidder B:   ABS  PASS ABS  PASS FAIL PASS PASS    (4 PASS, 1 FAIL turnover, 2 ABSTAIN)
Bidder C:   ABS  PASS ABS  ABS  PASS PASS PASS    (4 PASS, 1 ABSTAIN low-conf GST, 2 ABSTAIN)
```

## Round-1 non-negotiables — proof points

| N | Mechanism | Proof |
|---|---|---|
| **N1** Every verdict criterion-level explainable | `Verdict.rule_id` + `bindings_json` + `reason` + `evidence_refs` M2M; empty rule_id triggers banner; sign-off refuses empty rule_id | `officer/templates/officer/verdict_detail.html` |
| **N2** Never silently disqualify | 6 ABSTAIN verdicts (4 no_rule_mapped + 1 low_conf + 1 PAN); no FAIL except genuine threshold violation | `deploy/rules/common.rego` |
| **N3** Scanned + photo support | Bidder C's distorted GST → Gemini Vision blocks (`source=GEMINI_VISION`, conf<0.85) → ABSTAIN | `core/parsing/gemini_vision_ocr.py` |
| **N4** Append-only audit + signed PDF | Merkle chain (RFC 8785 + SHA-256); pyHanko PAdES-B; tamper-test detects byte-flip | `core/audit/merkle_log.py`, `core/audit/signer.py` |

## What does NOT change after this freeze

- Code in `core/` and `officer/`
- Rego rules in `deploy/rules/`
- The verdict matrix (any change requires re-running `verify_day11`)
- The architecture diagram

## What CAN change after this freeze

- Comments + docs (typos, screenshots)
- Sahil's video narration (per CLAUDE.md, his deliverable)
- The team's slide design (PPT/Keynote — the outline is in `docs/deck_outline.md`)
- A re-run of `bootstrap_demo --restore-snapshot` on demo morning
- A fresh `verify_demo` run on demo morning (just confirms freeze is intact)

## Day-14 hardening summary

| Task | Outcome |
|---|---|
| T1.1 Tailwind vendored | `static/vendor/tailwind.built.css` shipped; `cdn.tailwindcss.com` removed from `base.html` |
| T1.2 Playwright smoke test | `core/tests/test_demo_flow_browser.py` shipped (skipped unless `pip install playwright`) |
| T1.3 Dead command cleanup | Removed: `verify_synthetic_pipeline`, `verify_extractions`, `verify_verdicts`, `verify_day5`, `audit_summary`, `reset_audit_chain` (consolidated into `verify_day11`) |
| T1.4 `cleanup_evidence_dir` | New management command + run; orphaned PDFs + test subdirs cleaned |
| T1.5 `verify_day11` comments | Header docstring updated 5→7 criteria with explanation |
| T2.1 `verify_demo` pre-flight | New single command checks 8 demo-readiness signals |
| T2.3 Concurrent-edit smoke | New test proves audit chain stays green under interleaved POSTs |
| T2.4 `FALLBACK_PLAN.md` | 3-layer fallback (live demo → video → static deck) documented |
| T3.x Final regression + snapshot | 212 passed, snapshot regenerated post-cleanup |

## Demo-morning ritual (2026-05-16)

```bash
# 1. Restore from snapshot (instant)
python manage.py bootstrap_demo --restore-snapshot

# 2. Start OPA in a separate terminal
deploy/bin/opa run --server --addr 127.0.0.1:8181 --log-level error deploy/rules/

# 3. Pre-flight check
python manage.py verify_demo
# Must print: "=== verify_demo PASS — system is demo-ready ==="

# 4. Start Django
python manage.py runserver 0.0.0.0:8000

# 5. Browser → http://localhost:8000/  · login officer / praman_demo_2026
# 6. Demo
```

If `verify_demo` reports any RED at venue, follow `docs/FALLBACK_PLAN.md`.

## Sign-off

This build is frozen. Any code change after this point requires:
1. Re-running the full pytest suite (`pytest`).
2. Re-running `verify_demo`.
3. Regenerating `db.sqlite3.demo` via `snapshot_demo_db --force`.
4. Updating the freeze hash recorded in this file.

— Sahil, Day-14 closing on 2026-05-03
