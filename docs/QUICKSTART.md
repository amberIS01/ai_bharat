# QUICKSTART — demo morning runbook

Goal: take a fresh clone to a working demo at `http://localhost:8000/`
in under 60 seconds.

## Prerequisites

- Python 3.10+ (verified on 3.10.12)
- 64-bit Linux or macOS (ARM Mac wheels untested; see Troubleshooting)
- A `.env` file with `GEMINI_API_KEY` and `GEMINI_VISION_API_KEY` (only
  needed if the snapshot doesn't exist; the demo path is cache-only)

## The 7-step path

```bash
# 1. Get the code
git clone <repo-url> ai_bharat && cd ai_bharat

# 2. Python environment + dependencies
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. Apply Django migrations against an empty SQLite
python manage.py migrate

# 4. Start OPA in a *separate terminal* and leave it running
# Demo will not work without OPA reachable on 127.0.0.1:8181.
deploy/bin/opa run --server --addr 127.0.0.1:8181 --log-level error deploy/rules/

# 5. Bootstrap the demo state (instant if db.sqlite3.demo is present)
python manage.py bootstrap_demo

# 6. Start the Django dev server
python manage.py runserver 0.0.0.0:8000

# 7. Open http://localhost:8000/  in a browser.
#    Login: officer / praman_demo_2026
```

That's it. The hero flow is: dashboard → criteria_review → eval_grid →
verdict_detail → signoff.

## What the demo path looks like

```
$ python manage.py bootstrap_demo
=== bootstrap_demo · using db.sqlite3.demo snapshot ===
  copied db.sqlite3.demo → db.sqlite3
  bootstrap_demo skipped (snapshot path)
  Login at http://127.0.0.1:8000/officer/  with officer / praman_demo_2026
```

If you see this message, you're done. The verdict matrix is pre-loaded
and ready.

## Troubleshooting

### "OPA not reachable" red banner on dashboard

Step 4 is missing or OPA died. In a separate terminal:
```bash
deploy/bin/opa run --server --addr 127.0.0.1:8181 --log-level error deploy/rules/
```
Reload the dashboard. The banner disappears.

### "Run evaluation" doesn't work

Same as above — OPA must be running. The view refuses to call OPA when
it's down (graceful failure, not a 500).

### Verdict matrix shows mostly ABSTAIN with `no_rule_matched`

Day-12 fixed this. If you're seeing it on a fresh checkout, check that
you're on the latest `main` and that `core/policy/parsers.py` has the
title-based dispatch (not code-based).

### `bootstrap_demo` says "Live DB diverges from db.sqlite3.demo snapshot"

You changed something in the live DB and it no longer matches the demo
snapshot. To return to known-good state:
```bash
python manage.py bootstrap_demo --restore-snapshot
```

### Tailwind/HTMX/AG-Grid don't load (CSS broken on dashboard)

The page CDN-loads three resources. If conference WiFi is bad, you'll
see an unstyled page. Pre-load the page on the venue WiFi at least once
30 minutes before the demo so the browser caches the assets. Day-14
dry-runs should vendor these locally.

### `pip install` fails on M-series Mac (ARM)

PyMuPDF / pyHanko wheels for ARM may not be available. The simplest
workaround on demo day: run the demo on an x86-64 Linux laptop. Long-
term: build wheels from source via `pip install --no-binary :all: …`.

### `bootstrap_demo` hangs at `ingest_bundle` for 10–20 seconds

Docling is downloading layout + table-detection models on first run.
This is a one-time cost. If WiFi is slow, the demo has stalled. The
snapshot path (step 5) avoids this entirely — that's why it's the
default for demo morning.

### Gemini API quota exhausted

The demo path uses cached responses (`GeminiCallCache` table) baked
into `db.sqlite3.demo`. Live API calls happen only on cache miss, which
shouldn't occur during the demo. If it does, the snapshot is corrupted
or out of date — `--restore-snapshot` and verify with `verify_day11`.

## Verify everything works

```bash
# OPA must be running for this
python manage.py verify_day11 --skip-bootstrap
# Expected: "=== Day-11 PASS — pipeline end-to-end is demo-ready ==="
```

The full unit-test suite:
```bash
python -m pytest
# Expected: ≥228 passed, 2 skipped (Playwright module + slow ingest test)
```

## What NOT to do on stage

- Don't `bootstrap_demo` mid-demo without the snapshot — the cold-start
  Gemini path takes ~80 seconds.
- Don't `--force` re-ingest a Document that already has Verdict
  citations; it'll orphan the audit chain. The command refuses by
  default; pass `--force-cascade` if you mean it.
- Don't sign off with empty `rule_id` verdicts — `export_evidence`
  refuses (Day-11 fix). File an override or fix the Rego rule first.
