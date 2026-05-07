# Demo-day fallback plan

Three-layer fallback for 2026-05-16 at Taj Yeshwantpur. Owners and
deadlines below.

> CLAUDE.md hard rule: Sahil records the video narration. The team
> does NOT script speaker notes. This document is structural; what
> Sahil says on stage / in voiceover is his.

## Layer 1 — Live demo (primary)

**State of readiness (end of Day 14):**
- `verify_demo` exits 0 on Sahil's primary laptop
- Snapshot `db.sqlite3.demo` committed; SHA-256 logged in `docs/numbers.md`
- Tailwind vendored locally at `static/vendor/tailwind.built.css` (no CDN dependency)
- HTMX 2.0.3 + AG-Grid v33 SRI-pinned
- OPA binary committed at `deploy/bin/opa`
- 5 hero URLs return 200 via Django test client
- Tamper-test on signed PDF works live (verify_signed_pdf reports invalid after byte-flip)

**Pre-demo ritual (30 min before stage):**

```bash
# 1. Pull repo to demo laptop (USB stick if no Wi-Fi)
git clone … && cd ai_bharat
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Snapshot path
python manage.py bootstrap_demo --restore-snapshot

# 3. Pre-flight
python manage.py verify_demo

# 4. Start OPA in a separate terminal
deploy/bin/opa run --server --addr 127.0.0.1:8181 --log-level error deploy/rules/

# 5. Start Django
python manage.py runserver 0.0.0.0:8000

# 6. Browser → http://localhost:8000/, login officer / praman_demo_2026
# 7. Walk through dashboard → criteria → grid → drilldown → signoff
#    Confirm no console errors.
```

**Pre-demo ritual (5 min before stage):**
- Refresh dashboard. Confirm audit pill shows GREEN.
- Click "Sign-off" link in sidebar. Confirm chain banner is NOT red.
- Browser dev tools open (F12 → Console). Confirm no errors on each page.
- Audio + screen capture verified.

## Layer 2 — Pre-recorded demo video (backup)

**Owner:** Sahil + team logistics lead.
**Path on disk (when recorded):** `docs/demo_video.mp4` (~60-100 MB,
1920×1080, 30 fps, h264+AAC).
**Recording window:** Days 14-15.
**Tool recommendation:** OBS Studio (cross-platform, .mkv → .mp4
encode via ffmpeg). NOT Loom (cloud-dependent).

**What the video captures:**
- The 7-section sequence in `docs/demo_shotlist.md`
- Sahil's voiceover layered after recording the screen capture
- Sahil's call on language, pacing, emphasis (per CLAUDE.md)

**Where the video lives:**
- `docs/demo_video.mp4` (committed if size permits — git LFS otherwise)
- Mirrored to a USB stick and to a shared drive (Google Drive / Confluence)

**When to fall back to the video:**
- Live demo's `verify_demo` doesn't pass at venue
- AG-Grid fails to render after CDN check (Tailwind is vendored, but
  AG-Grid is still CDN-loaded; SRI-pinned so it fails closed if hashes
  drift)
- Laptop dies / projector adapter incompatibility
- Timing pressure: video is 5:00 exactly; live demo can run long under stress

## Layer 3 — Static deck + architecture diagram (last resort)

If both Layers 1 and 2 fail simultaneously (laptop + USB both compromised),
the team falls back to:
- The 8 slides (per `docs/deck_outline.md`)
- The architecture diagram (`docs/architecture.svg` → embedded in slide 5)
- The numbers table (`docs/numbers.md` → reference data)
- The verdict matrix (slide 4 backup screenshot, captured on Day 14)

**Pre-render PDFs of every key page** during Day 14 dry-run; commit to
`docs/screenshots/`. Even with no laptop and no video, the team can
walk the audience through static screenshots that prove every claim
in the deck.

## Network-condition decision tree (07:00 AM, May 16)

```bash
# 1. Latency check
curl -w "tailwind: %{time_total}s\n" -I https://cdn.tailwindcss.com 2>/dev/null
curl -w "ag-grid:  %{time_total}s\n" -I https://cdn.jsdelivr.net 2>/dev/null
curl -w "htmx:     %{time_total}s\n" -I https://unpkg.com 2>/dev/null
```

| Condition | Action |
|---|---|
| All three < 200 ms | **Layer 1 — live demo, no warmup needed.** |
| One or two timeout / >2s | **Layer 1 with cache warmup**: visit each hero page once before stage so browser caches CDN assets. **Tailwind is vendored locally — Tailwind path doesn't matter.** |
| All three blocked / >5s | **Layer 2 — play the video.** No live demo. |
| Network completely down | **Layer 2 — play the video, then Layer 3 deck.** |

## Backup laptop checklist

**Spec:** Linux x86-64 (Ubuntu 22.04 LTS preferred). Avoid M-series Macs
(PyMuPDF/pyHanko ARM wheel risk per `docs/QUICKSTART.md`).

**Setup (Day 14 morning):**
- [ ] Fresh `git clone`
- [ ] `python -m venv .venv && pip install -r requirements.txt`
- [ ] `python manage.py bootstrap_demo --restore-snapshot`
- [ ] `python manage.py verify_demo` exits 0
- [ ] OPA running in tmux session
- [ ] Browser bookmarks for all 5 hero URLs
- [ ] Browser cache pre-warmed (visit each hero page once)
- [ ] Demo video at `~/demo_video.mp4` and USB stick

**Stowage:** Faraday bag or laptop sleeve. Keep on the table during
the demo — visible to the team, not in a checked bag.

**Owner during demo:** team logistics lead (Sahil is on stage). If
primary laptop fails, lead swaps backup in.

## Failure modes Sahil should rehearse

For each, the system fact (NEVER what to say — Sahil writes the words):

1. **OPA dies mid-demo** — dashboard banner fires immediately. System
   fact: "Praman fails closed when OPA is unreachable. The dashboard
   shows the banner; we never hide a missing policy engine."
2. **Tailwind didn't load (vendoring missed)** — page is unstyled but
   functional. System fact: "The CSS path is now vendored as part of
   Day-14 hardening; this fallback is what production looks like with
   blocked CDN."
3. **A juror tampers with `db.sqlite3` mid-demo** — `verify_chain` at
   render time of signoff page detects it. System fact: "The audit
   chain check runs at render time, not just on demand."
4. **Tamper test on signed PDF doesn't fail** — extremely unlikely;
   would mean pyHanko or our verify wrapper is broken. Fallback: open
   the audit timeline; the embedded head hash is still authoritative.

## Owners + deadlines

| Item | Owner | Deadline |
|---|---|---|
| Video recorded + verified | Sahil + team | EOD 2026-05-15 |
| Backup laptop set up | Logistics lead | EOD 2026-05-15 |
| `docs/screenshots/` populated | Sahil | EOD 2026-05-15 |
| Pre-flight ritual rehearsed | Sahil | 2026-05-16 morning |
| `verify_demo` re-run at venue | Sahil | 07:00 AM 2026-05-16 |
| Decision-tree network check | Sahil | 07:00 AM 2026-05-16 |
| Demo or fallback executed | Team | 2026-05-16 stage time |
