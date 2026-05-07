# Praman pitch deck — slide outline

**8 slides for the team to design in PPT/Keynote.** This document is
**structural only** — slide titles, bullet points (the *what*), and
references to data + screenshots the team can pull from the codebase.

> **CLAUDE.md hard rule:** narration belongs to Sahil. He defends the
> deck on stage in front of IAS officers and VCs. Speaker notes,
> verbatim sentences, and video script text are NOT in this document.
> What's here is the skeleton; flesh and voice are Sahil's.

The team should review each slide's bullet points + reference data,
then write narration that ties the slide to the demo flow.

---

## Slide 1 · Problem

**Title bar:** *Tender evaluation in CRPF today*

**Bullets (the *what*, not the *how to say it*):**
- 50+ pages of RFP per tender; 20-100+ pages per bidder bundle
- Officer reads everything manually; ~3-5 days per evaluation
- 20%+ rejection rate gets challenged in court (CVC, RTI, CAG); evidence
  trail is paper, often incomplete
- The decision-maker needs three things at once: a verdict, a citation
  (page + bbox), and a tamper-evident trail

**Visual aid for the team:** photo of a stack of CRPF tender bundles
or a screenshot of a real CRPF NIT PDF (link to one of the cached
references in the local copy).

**Reference numbers (factual, defensible):**
- Sahil downloaded 3 CRPF tenders 2026-04-30: NIT026, NIT041, B.V-4/2025-26-SZ-Works
- Average tender bundle observed: ~80 pages text + ~6 scanned attachments

---

## Slide 2 · Why naive AI fails

**Title bar:** *Three failure modes the jury cares about*

**Bullets:**
- **Hallucination.** "GPT-4 says Bidder A meets the turnover criterion"
  — but cites no page, no bbox, no parsed number. Officer has to
  re-read the bundle anyway.
- **Black box.** "GPT-4 says ABSTAIN" — what rule? what threshold?
  what input? Officer can't defend it in court.
- **No trail.** Officer signs the decision, but the model output is
  ephemeral. CAG asks "show me how this was decided" and there's
  nothing to show.

**Visual aid:** three side-by-side cards, one per failure mode, with
an X mark.

**Why this matters in slide 3:** these three failures are exactly what
Praman's mechanisms (rule_id, bindings, audit log) deliver against.

---

## Slide 3 · The insight

**Title bar:** *LLMs extract. Rego decides.*

**Bullets:**
- Use LLMs (Gemini 2.5 Pro/Flash + Vision) for what they're good at:
  parsing messy PDFs, finding relevant clauses, extracting facts
- Use OPA + Rego for what LLMs are bad at: deterministic rule-based
  reasoning that an officer can defend
- Every verdict = (LLM-extracted fact) → (Rego rule) → (status, rule_id,
  bindings, reason, evidence_refs)
- The LLM never sees "the criterion threshold" — only the value to
  extract. The threshold lives in Rego.

**Visual aid:** tiny architecture stub showing PDF → LLM → Fact → Rego
→ Verdict, with "decision" arrow only on the right side.

**Reference:**
- `core/policy/opa_client.py` — HTTP client to OPA
- `deploy/rules/c1_turnover.rego` — example Rego rule
- `core/policy/parsers.py` — title-based dispatch (Day-12 fix)

---

## Slide 4 · Live demo screenshot (the hero shot)

**Title bar:** *Praman in action — bidder × criterion verdict matrix*

**Visual aid:** screenshot of the eval grid at
`http://localhost:8000/officer/rfp/21/grid/`. AG-Grid with 3 bidders ×
7 criteria, showing colored pills:
- Bidder A: 5 PASS (green), 2 ABSTAIN (amber, no_rule_mapped)
- Bidder B: 4 PASS, 1 FAIL (red, turnover < threshold), 2 ABSTAIN
- Bidder C: 4 PASS, 1 ABSTAIN (low-conf GST, demo's narrative spine), 2 ABSTAIN

**Bullets (caption-style):**
- One row per bidder; one column per criterion
- Cell color = verdict status
- Click any cell → drilldown with cited page + bbox + rule reasoning
- This screenshot is from a live run on synthetic data. No real
  bidder names, no real GSTINs.

**Take-away the team should hammer:** "every cell has a story; every
story has a citation."

---

## Slide 5 · Architecture diagram

**Title bar:** *System architecture (5 lanes)*

**Visual aid:** embed `docs/architecture.svg` (or PNG export at
`docs/architecture.png` if the slide tool can't render SVG).

**Lanes (left-to-right, top-to-bottom):**
1. **INGEST** — PDF → Docling (digital) | Gemini Vision (scanned) → Block rows
2. **EXTRACT** — Block manifest → Gemini Pro (criteria) → Gemini Flash (facts) → Criterion + Fact rows
3. **EVALUATE** — Fact + Criterion → parsers.py → OPA + Rego → Verdict (PASS/FAIL/ABSTAIN)
4. **AUDIT & SIGN** — Django signals → Merkle chain → AuditEntry → pyHanko PAdES-B → Signed PDF
5. **OFFICER UI** — 5 hero screens: Upload → Criteria → Eval grid → Drilldown → Sign-off

**Production roadmap (greyed sidebar in the SVG):**
- Local Qwen2.5-VL via vLLM (replaces Gemini, true air-gap)
- Trillian / immudb (replaces SQLite Merkle, public verifiability)
- CCA Class-3 DSC via PKCS#11 (replaces self-signed cert)
- ColPali fallback retrieval

**Reference numbers:**
- Total Python files: ~50 (count: `find . -name '*.py' | grep -v venv | wc -l`)
- Rego lines across 5 rule files + common.rego: ~400

---

## Slide 6 · The four Round-1 non-negotiables → mechanisms

**Title bar:** *Commitments → mechanisms → demo proof*

**Table (3 columns):**

| Non-negotiable | Mechanism | Demo proof |
|---|---|---|
| **N1** Every verdict criterion-level explainable | `Verdict.rule_id` + `bindings_json` + `reason` + `evidence_refs` (M2M to Block); empty rule_id is detected and a banner shows | Click any verdict cell → drilldown shows all four fields |
| **N2** Never silently disqualify | Rego rules emit ABSTAIN on low confidence (`< 0.85`), missing values, or no rule mapped — never FAIL | Bidder C × C-4 GST → ABSTAIN low_confidence (visible amber pill) |
| **N3** Scanned + photo support | Docling fallback → Gemini Vision Flash with prompt-elicited per-region confidence; `Block.source = GEMINI_VISION` for those rows | Bidder C's distorted GST cert produces ≥1 GEMINI_VISION block with conf < 0.85 |
| **N4** End-to-end auditable + signed PDF | Merkle chain (RFC 8785 canonical JSON + SHA-256, RFC 9162-style domain delimiter); pyHanko PAdES-B signs every PDF, embeds head hash on cover; tamper-test invalidates signature | Sign-off page shows audit timeline + multi-PDF history; flip 1 byte → `verify_signed_pdf` reports invalid |

**Reference:**
- N1: `officer/templates/officer/verdict_detail.html` (rule_id banner + bindings render)
- N2: `deploy/rules/common.rego` `is_low_confidence()` function
- N3: `core/parsing/gemini_vision_ocr.py` + `core/extraction/facts_extractor.py` confidence aggregation
- N4: `core/audit/merkle_log.py` + `core/audit/signer.py` + `verify_day11` acceptance gate

---

## Slide 7 · Production roadmap

**Title bar:** *From demo to deployment*

**Bullets (each = a swap from demo to production):**
- **LLM:** Gemini hosted API → local Qwen2.5-VL + vLLM in air-gapped enclave
- **Audit log:** SQLite + Python Merkle chain → Google Trillian (verifiable log) or immudb
- **PDF signing:** Self-signed X.509 → CCA India Class-3 DSC token via PKCS#11 (eMudhra ProxKey, ePass2003)
- **Visual fallback retrieval:** Same Gemini Vision call that does OCR → ColPali (multimodal retrieval) for richer ABSTAIN context
- **Hosting:** Local laptop / Docker → on-prem server in CRPF intranet, no internet egress

**Visual aid:** the production-roadmap sidebar from
`docs/architecture.svg` (already greyed in the SVG; just point to it).

**Reference for "why we chose these":**
- Qwen2.5-VL is Apache-2.0 licensed, vLLM is open
- Trillian is the reference verifiable log used by Certificate Transparency
- eMudhra ProxKey + ePass2003 are the two CCA-licensed Class-3 DSC tokens for L1 procurement

---

## Slide 8 · Why CRPF should pilot

**Title bar:** *What Praman delivers, day 1 of pilot*

**Bullets (each is a measurable outcome):**
- **Time-to-evaluate:** 5 days → 5 minutes per bidder bundle (manual
  reading vs Praman click-through)
- **Defensibility:** every verdict comes with a rule_id, bindings, and
  cited page+bbox — CVC/CAG/RTI requests answerable in seconds
- **Tamper-evidence:** the Merkle chain + signed PDF make any post-hoc
  alteration cryptographically detectable; no "chain of custody" disputes
- **Officer-in-the-loop is preserved:** ABSTAIN routes to manual
  review by design; the system never disqualifies a bidder without
  the officer's explicit override
- **Air-gap-ready:** the production swaps in slide 7 don't change the
  architecture, just the implementations behind each box

**The one slide that closes the deal:**
- Pilot scope: 1 RFP, 5 bidders, 1 evaluation officer
- Time to value: 2 weeks (1 week deploy on intranet, 1 week training)
- Success metric: officer signs off in < 1 hour with full audit trail

**Reference numbers (defensible):**
- Test count: ≥228 passed / 2 skipped (regenerate via `pytest`)
- Verdict matrix coverage: 21 verdicts (3 bidders × 7 criteria)
- 5 Rego rules covering financial, compliance, technical categories
- Audit chain depth at sign-off: 200+ entries (regenerable)

---

## Closing notes for the team

- **Slide 4 (the hero screenshot)** is the spine. Every other slide
  loops back to it.
- **Slide 6 (non-negotiables table)** is what an IAS officer or VC
  asks the hardest questions about. Be ready to drill into any of N1-N4.
- **Slide 8 (pilot ask)** should end with a single, concrete
  conversion: "we want a pilot." Don't dilute it with "we also could…"

For the demo recording sequence (Days 14-15), see
[docs/demo_shotlist.md](demo_shotlist.md).
For failure modes during the live demo, see
[docs/demo_recovery.md](demo_recovery.md).
For the data points the team can quote, see
[docs/numbers.md](numbers.md).
