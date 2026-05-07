# Praman · प्रमाण

Round-2 submission to the AI for Bharat hackathon (PAN IIT Bangalore +
Government of Karnataka), Theme 3: **AI-Based Tender Evaluation and
Eligibility Analysis for Government Procurement by CRPF**. Grand finale
2026-05-16, Taj Yeshwantpur, Bengaluru.

Praman parses a tender (RFP) + bidder bundles, extracts the eligibility
criteria + per-bidder facts via Gemini 2.5, evaluates each fact against
a Rego rule (LLM extracts; OPA decides), and emits a Merkle-chained
audit log + signed evidence PDF.

The four Round-1 non-negotiables — every verdict is criterion-level
explainable, never silently disqualify, scanned/photo support, and an
end-to-end auditable log — are mechanical commitments, not aspirational
features.

## Get started

If you're an operator setting up for a demo: **read
[docs/QUICKSTART.md](docs/QUICKSTART.md)** — it's a 7-step recipe
that takes a fresh clone to demo-ready in under a minute.

If you're a juror / reviewer: open
[docs/architecture.svg](docs/architecture.svg) for the system overview,
then [docs/deck_outline.md](docs/deck_outline.md) for the slide-by-slide
structure of the pitch.

## Project state

- **Days 1-12 shipped.** Pipeline: parse (Docling + Gemini Vision) →
  extract (Gemini Pro/Flash) → evaluate (OPA Rego) → audit (Merkle
  chain) → sign (pyHanko PAdES-B).
- **Tests:** ~228 passed + 2 module-level skips (Playwright not
  installed; one slow integration test that needs ingest_bundle state).
  Run `pytest` to confirm against current state.
  `python manage.py verify_day11 --skip-bootstrap` is the load-bearing
  acceptance gate.
- **Demo readiness:** verified end-to-end on synthetic data. Snapshot
  (`db.sqlite3.demo`) makes cold-start sub-second.

## Repository layout

| Path | What |
|---|---|
| `praman/` | Django project root (settings, URLs) |
| `core/` | Domain app: models, parsing, extraction, policy, audit |
| `officer/` | Officer-facing views + templates (5 hero screens) |
| `synthetic/` | Synthetic RFP + 3 bidder bundles (gitignored, regenerated) |
| `deploy/rules/` | Rego rules (one per criterion category) |
| `deploy/bin/opa` | OPA binary (committed, ~77 MB) |
| `deploy/demo_signer.p12` | Self-signed cert for demo PDF signing |
| `docs/` | Architecture diagram + deck outline + operator runbooks |
| `db.sqlite3.demo` | Pre-warmed DB snapshot for instant demo-ready |

## Key rules (CLAUDE.md)

- The LLM decides nothing. Verdicts come from Rego.
- Every verdict is criterion-level explainable (rule_id + bindings + reason).
- Never silently disqualify — low-conf or missing → ABSTAIN, not FAIL.
- Audit log is append-only and Merkle-hash-chained. Signed evidence PDF.
- No real tender / bidder data — synthetic only.
- No hosted-LLM calls on real PII. Air-gapped story holds via Qwen2.5-VL
  in production.

## Hackathon meta

This is a solo build by Sahil. Team contributes pitch deck + 5-min demo
video over Days 13-15. The architecture and code are Sahil's to defend
on stage.
