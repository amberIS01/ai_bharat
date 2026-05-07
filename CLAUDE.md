# Praman — AI for Bharat Hackathon (Theme 3, CRPF)

This directory is the build workspace for **Praman** — Sahil's Round 2 submission to the AI for Bharat hackathon (PAN IIT Bangalore + Government of Karnataka). Theme 3: AI-Based Tender Evaluation and Eligibility Analysis for Government Procurement by CRPF. Grand Finale: **2026-05-16**, Taj Yeshwantpur, Bengaluru.

Read `~/.claude/projects/-home-sahil-Documents-new-ai-bharat/memory/MEMORY.md` for hackathon context, theme details, team split, and the full submitted Praman architecture. This file documents what is specific to working *inside* this repo.

---

## Project state

- **Phase:** Round 2 — prototype build + 5-min video + code repo. Idea phase cleared on 2026-05-01.
- **Builder:** Sahil solo on the working product. Team contributes pitch deck and demo video only — do not assume any teammate will write code.
- **Demo data:** real CRPF tenders are not released. Synthetic-but-realistic RFPs and bidder bundles must be manufactured as part of the build.

The four claimed non-negotiables (criterion-level explainability, never-silently-disqualify, scanned+photo support, end-to-end auditable log) are **commitments**, not aspirations — every demo path must visibly satisfy all four or the jury (domain mentors + IAS officers + VCs) will catch the gap.

---

## Hard rules (Theme 3 + cross-hackathon non-negotiables)

These are commitments from the submitted idea + sponsor non-negotiables. Violating any of them collapses the differentiator that won Round 1.

1. **The LLM decides nothing.** LLMs/VLMs extract facts and structure; verdicts come from the Open Policy Agent (Rego) rule engine. If a draft drifts toward "LLM-as-judge" or "ask the model to rate eligibility," push back hard.
2. **Every verdict is criterion-level explainable.** Output schema must always include rule ID + variable bindings + cited spans (page + bbox). No black-box verdicts.
3. **Never silently disqualify.** Missing or low-confidence data must fail closed to `ABSTAIN` → human review, never to `FAIL`. Char-conf < 0.85 routes to human.
4. **Audit log is append-only and Merkle-hash-chained.** Every state change writes an entry. Demo must be able to export a signed evidence PDF.
5. **No real tender / bidder data.** Use synthetic RFPs and synthetic bidder bundles only. No real govt entity names, no real GSTINs, no real PANs in committed files.
6. **No hosted-LLM calls on real PII.** All LLM/VLM work runs on synthetic data. The architecture's "air-gapped" claim must remain defensible — note it explicitly any time a hosted API is suggested.
7. **All code written during the hackathon.** Open-source libraries (Docling, PaddleOCR, Qwen2.5-VL weights, OPA, immudb, MinIO, MAPIE, Outlines, etc.) are allowed; pre-existing tender-eval products are not.

If a draft / patch / suggestion violates one of these, flag it to Sahil before he reads further. Don't quietly soften.

---

## What "good" looks like in the Round 2 demo

Evaluation weights (memorise these — every demo decision should map to one):

| Weight | Dimension | What graders are checking |
|--------|-----------|---------------------------|
| 25% | Technical implementation & innovation | Real working pipeline; the "Rego decides, LLM extracts" split is visibly true |
| 25% | Real-world deployability & govt feasibility | Air-gapped story holds; CVC/CAG/RTI evidence PDF actually exports |
| 20% | Problem relevance & depth of understanding | Synthetic data feels real; criteria categories (technical/financial/compliance) are right |
| 15% | Demo quality & presentation | Clickable evidence trail (page + bbox) works live; ABSTAIN routing visible |
| 15% | Scalability & long-term impact | One added criterion = one added Rego rule; no model retrain needed |

The demo's spine — the thing every section of the video and deck circles back to:

1. Officer uploads an RFP. System extracts 4–6 eligibility criteria, each with the cited clause.
2. Officer uploads 3 bidder bundles (mix of typed PDF, scanned, phone photo).
3. System emits per-criterion verdicts: some PASS, some FAIL, at least one ABSTAIN → human review.
4. Officer clicks any verdict → jumps to the source page with the bounding box highlighted.
5. Officer signs off → audit log entry written → evidence PDF exports with Merkle root.

If a feature does not contribute to one of those five steps, it does not belong in the demo. It can stay in the deck and architecture diagram.

---

## Working style for this project (Sahil-specific)

- **Solo build → ruthless scope control.** Default position on any new feature suggestion: "does this go in the demo or the deck?" If demo, what does it replace?
- **Don't auto-write code Sahil hasn't asked for.** He's solo, so every line in the repo is his to defend on stage. Generate only what he requests, in the smallest unit he asks for.
- **Hold the line on the four non-negotiables.** Under time pressure, the first thing builders cut is explainability or audit-log plumbing. Those are the differentiators that won Round 1. Cut breadth of OCR engines before cutting auditability.
- **Synthetic data is a workstream, not a side task.** Plan for it explicitly. A weak synthetic RFP makes the whole demo look weak.
- **Stage components by judging risk.** Components that are off-the-shelf and just need integration (Docling, PaddleOCR, OPA, immudb, hashlib-Merkle) are low risk. Components that need training or calibration data Sahil doesn't have (YOLOv9s for stamps, MAPIE conformal, TrOCR-Indic fine-tuning, DSC PKI integration) are high risk and should default to "deck-only mention" unless a working baseline exists in 1 day.

---

## File hygiene

- Don't create a README until there's something to describe.
- `.gitignore` excludes `private/`, `real-data/`, model weights, and any folder containing actual tender documents from day one.
- Keep synthetic test data under `synthetic/` — file names should make it obvious the data is fake (e.g. `rfp_fake_construction_001.pdf`, not `crpf_tender.pdf`).
- Architecture diagrams as `.svg` (editable) committed alongside the source `.md`/`.drawio`/`.excalidraw`.

---

## Things Claude should *not* do in this repo

- Don't generate sample data with realistic-looking names, GSTINs, PANs, or organisation names — even in code comments. Use obvious fakes (`Acme Constructions`, `GSTIN 29ABCDE0000F1Z5` with synthetic check digit).
- Don't recommend hosted-LLM APIs for the data path without flagging that the "air-gapped" claim in the submission means a local model path must exist (Qwen2.5-VL local, llama.cpp, vLLM, Ollama).
- Don't write Sahil's pitch deck narration or video script. He has to defend it on stage in front of IAS officers and VCs.
- Don't add scope to code Sahil asks for. If he asks for a Rego rule, write the rule — not the rule plus a UI plus a test harness plus a CI workflow.
- Don't drift the architecture toward "LLM rates the bidder." That's the exact pattern Praman is positioned against.
