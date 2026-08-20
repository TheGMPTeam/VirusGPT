# VirusGPT Web UI — Visual Audit

**Date:** 2026-08-20
**Scope:** Live UI at http://localhost:8500 (FastAPI + vanilla-JS frontend, `app/`).
**Method:** Real headless-Chromium inspection (browser-use) across every tab, theme, and a
390px mobile viewport, with screenshots and on-the-spot DOM/CSS verification against source
(`app/index.html`, `app/assets/css/styles.css`, `app/assets/js/memory.js`).

---

## TL;DR

The UI is **mostly solid** — Chat, Personas, Memory Graph, Settings, and all three themes
render with no overflow, clipping, or broken controls under desktop widths. I found **2 real
defects** and **1 borderline issue**, each reproduced and traced to source:

| # | Severity | Area | Finding | Root cause |
|---|----------|------|---------|------------|
| 1 | **High** | Mobile (≤720px) | Left sidebar (Sessions / "Who should answer?" / "Personas in room") is **unreachable** — slides off-screen, no way to open it | `styles.css:163` defines `.open` drawer but NO hamburger/toggle exists anywhere in JS or the topbar |
| 2 | **Medium** | Memory Graph | Concept cloud **collapses into the top-left corner**; the root/hub node is half-clipped at the canvas edge; >80% of canvas is empty | Weak centering force (`0.0015`) + low repulsion in `memory.js` `mgStep()`; initial positions near origin drift left and stick |
| 3 | **Low** | Settings modal | "Think timeout (ms)" label renders **inline beside** its input while every other field stacks label-above-input | `index.html:150` uses `class="form-rows"` (plural); CSS only defines `.form-row` (singular) |

**Non-issues (checked, cleared):**
- Title bar shows a "🐴" horse prefix in the inspection harness — **NOT a product bug**. The
  served `<title>` is `VirusGPT ~ Offline Agent Chat` with no emoji (`index.html:6`); no JS sets
  `document.title`. That prefix is an artifact of the browser-use harness tab wrapper.
- Legend "garbled overlapping text" flagged by one vision pass — **false positive**; re-capture
  shows the legend renders cleanly (color dots + 9 type labels).
- Persona card dropdown values ("Wor…", "alb") are normal selected-value truncation, not a bug.

---

## 1. Mobile sidebar breakage (HIGH)

**Evidence:** At a 390×800 viewport the sidebar computed style is
`transform: matrix(1,0,0,1,-280,0)`, `left:-280`, `visible:false`. The only topbar buttons are
`btn-settings` and `btn-tts-toggle` — there is **no hamburger/menu control** to reveal it.

**Source:** `styles.css:163`
```css
@media(max-width:720px){#sidebar{position:absolute;z-index:15;height:100%;transform:translateX(-100%);transition:.2s}#sidebar.open{transform:none}#topbar h1{font-size:15px}}
```
The `@media(max-width:720px)` block slides `#sidebar` off-screen and only reveals it via the
`.open` class — but a search of every JS file (`ui.js`, `main.js`, `chat.js`, `sessions.js`,
`personas.js`, …) finds **no code that ever adds `.open` to `#sidebar`**, and the topbar markup
(`index.html:16-28`) has no toggle button. So on phones/tablets ≤720px the Sessions list, the
"Who should answer?" chooser, and the "Personas in room" manager are completely inaccessible.

Also at 390px the **Chat + Team Workflow split-pane** crams the chat column to <150px, and the
decorative rotated "How can I assist you today?" strip eats most of that width.

**Impact:** Core chat works, but session switching and persona-in-room management are
impossible on mobile. **Recommendation:** add a hamburger button that toggles `#sidebar.open`,
and stack `#sidebar` / `#rightbar` vertically (or behind a second drawer) below ~720px instead
of the fixed two-column flex.

**Screenshot:** `docs/evidence/mobile_390_sidebar_hidden.png`

---

## 2. Memory Graph node clustering (MEDIUM)

**Evidence:** Across three captures (immediately after tab open, and after 6s of simulation
settling) the 19-concept graph stays pinned in the far-left corner. The hub node ("ai") is
cropped at the left canvas edge; the rest of the canvas is empty dark space.

**Source:** `app/assets/js/memory.js`
- Initial positions: `x: cos(i/n*2π)*180 + rand`, `y: sin(...)*180 + rand` (a ~360px circle
  centered on world origin `0,0`).
- `mgStep()` centering force toward canvas center is extremely weak: `fx += (cx-n.x)*0.0015`.
- Repulsion `2200/d²` is modest; with one dominant hub and short token-overlap edges, the cloud
  drifts left and the gentle centering never recovers it.

**Impact:** Cosmetic but misleading — the "living force-directed concept map" reads as broken
because it looks stuck in a corner and the root node is clipped. **Recommendation:** (a) run an
initial settling loop (e.g. 100 `mgStep()` calls before first paint) so it opens already spread;
(b) increase centering weight (e.g. `0.02`) and/or clamp node coordinates to the canvas bounds;
(c) offset initial positions by `(W/2, H/2)` so they start centered.

**Screenshot:** `docs/evidence/memory_graph_clustered.png`

---

## 3. Settings "Think timeout" label misalignment (LOW)

**Evidence:** In the Settings modal every field stacks its `<label>` above the input except
"Think timeout (ms)", whose label sits **inline to the left** of the number input, breaking the
form's visual rhythm.

**Source:** `index.html:150`
```html
<div class="form-rows"><label>Think timeout (ms)</label><input id="st-timeout" type="number" value="60000"></div>
```
vs. the others using `class="form-row"`. CSS defines only `.form-row` (`styles.css:129`):
```css
.form-row{padding:10px 18px;display:flex;flex-direction:column;gap:5px}
```
Because `.form-rows` is undefined, that div falls back to default block flow → label and input
land on the same line. **Fix:** change `form-rows` → `form-row` on line 150.

**Screenshot:** `docs/evidence/settings_thinktimeout_misaligned.png` (amber theme; issue is
theme-independent).

---

## What works well (verified)

- **Chat:** user + bot bubbles align correctly (right/left), avatars/labels positioned, streamed
  reply "Hi there! How can I assist you today?" rendered with per-sentence play buttons. No
  overflow or broken bubbles. `docs/evidence/chat_ok.png`
- **Personas:** New Persona button + 3 seeded cards (VirusGPT / Cipher / Oracle) with
  name, description, system prompt, skills, team-role, TTS voice, emoji, color, and
  Test/Delete/Save actions — all render and align. `docs/evidence/personas.png`
- **Memory Graph stats:** all 5 cards populated (19 concepts, 6 directories, 60 links, 0
  orphans, yes conformant); legend clean; canvas renders nodes/edges.
- No console errors (`window.__vgErr` = none), no leftover modal overlays, themes switch cleanly
  (Cyber / Amber / Ice).

---

## Evidence files (in `docs/evidence/`)

- `chat_ok.png` — Chat tab after a real send (bot replied correctly)
- `personas.png` — Personas management tab
- `memory_graph_clustered.png` — Memory Graph, nodes collapsed to top-left (defect #2)
- `mobile_390_sidebar_hidden.png` — 390px viewport, sidebar off-screen, no hamburger (defect #1)
- `settings_thinktimeout_misaligned.png` — Think-timeout label inline (defect #3)
