# Refactoring Analysis — Recent Updates

This analyzes the refactors shipped across the last several cycles (the
static-cleanup pass, the chat-TTS splitter work, the A2A mission hardening, and
this test-suite cycle) for correctness, cohesion, and risk. It is the "analysis on
updates refactoring" deliverable.

## 1. Chat TTS sentence engine (the biggest recent refactor)

**Before:** a single `ttsQueue` + `pumpTTS()` serial player; sentences were split
with one regex used in *two* places (`messages.splitSentences` for finalized
bubbles, `tts.drainSentences` for live streaming) and the splitter was **buggy**
(see §3).

**After:**
- `messages.js`
  - `splitSentences(t)` — pure, terminal-bounded splitter (now correct).
  - `makeSentencePlay` / `makePlayAll` / `buildSentencePlays` — DOM builders.
  - `playSingle(text, persona)` lives in `tts.js` (see below).
- `tts.js`
  - `playSingle()` — isolated single-sentence player (stops in-flight audio, clears
    queue, plays one `Audio`).
  - `autoPlaySentence()` — gated by `TTS_ON && sessionAutoPlay`.
  - `drainSentences()` — streaming extractor with a fallback re-split when the legacy
    regex spans the whole buffer.

**Assessment:** the split between *rendering* (`messages.js`, DOM) and *playback*
(`tts.js`, audio) is now clean. The live-streaming path (`drainSentences`) and the
finalized path (`buildSentencePlays`) both converge on the corrected
`splitSentences` semantics. **Risk eliminated:** previously both paths shared a
broken regex, so a fix in one place didn't help the other.

**Residual risk:** `drainSentences` still carries the legacy
`/[^.!?\n]*[.!?]+|\n+/g` regex plus a fallback branch. The fallback is only needed
because the legacy regex can still misfire. Once the whole streaming path moves to
the new splitter (Optimization §3.2), the legacy regex + fallback can be deleted,
removing ~15 lines of defensive code.

## 2. Session auto-play scoping

**Pattern introduced:** a module-level `sessionAutoPlay` flag in `tts.js`, set by
four discrete call sites:
- `chat.js` `/clear` handler → `false` + `stopTTS()`
- `sessions.js` `newSession()` → `false` + `stopTTS()`
- `ui.js` speaker toggle → `TTS_ON` (true/false)
- `main.js` boot → seeded from saved `TTS_ON`

**Assessment:** this is the correct, minimal way to scope auto-play to a session
without threading state through every call. The four sites are the *only* places a
session boundary or speaker state changes, so the invariant ("a fresh session is
muted until the speaker is on") is easy to audit. **Suggestion:** the flag name
`sessionAutoPlay` slightly misleads — it's really "streaming-auto-play allowed for
this session", distinct from the click-only `playSingle`/`replayMessage` which
ignore it. A one-line comment already clarifies this; no code change needed.

## 3. The splitSentences bug — root-cause retro

The original regex `/[^.!?\n]*[.!?]+|\n+/g` combined with a `lastIndex`-tracking
loop produced the **entire reply N times** for multi-word sentences. Why it
slipped through: the function was only ever fed *single-sentence* fixtures in
informal checks; the regression only manifested with realistic multi-sentence
model output. The new implementation (`text.match(/[^.!?]+[.!?]+|[^.!?]+$/g)`)
is provably terminal-bounded and has a unit test (`tests/test_split_sentences.py`)
covering 7 cases including the exact failure string. **This is the textbook case
for the unit test that now guards it.**

## 4. Tool-call logging (API shape refactor)

**Before:** the event bus stored `data` as a JSON *string* (durable for SQLite);
the SSE stream handler parsed it on output, but the **polling** status endpoint
(`/api/autonomous/status/{id}`) did not — so the JSON-string shape leaked to the
UI and `ev.data.tool` was `undefined`.

**After:** both the SSE stream handler and the polling status handler now
`json.loads` `data` on the way out; `team.js` `logToolCall` also defensively parses
strings. **Assessment:** correct — the durable form stays a string in the DB, the
API contracts are now consistent across both endpoints. Minor: `team.js` does this
twice (status + stream); a shared `normalizeEvent(e)` helper would DRY it.

## 5. Autonomous orchestrator (cross-restart recovery)

**Pattern:** `Supervisor` schedules background missions with
`asyncio.run_coroutine_threadsafe` on a loop captured at startup
(`set_loop`/`_schedule`), fixing the classic "synchronous route → wrong event loop
→ task never runs" bug. `resume_interrupted_missions()` re-drives in-flight missions
on boot; `resume_mission()` allows manual recovery; both reuse persisted subtasks
(`reuse_existing`) and skip completed tasks.

**Assessment:** this is robust and well-instrumented (every state transition emits
an event: `mission.running` → `task.started` → … → `mission.end`). The only
structural nit: `_run()` is ~190 lines with nested try/except and three early-return
paths; it would read better split into `_plan_stage` / `_dispatch_stage` /
`_verify_synthesize_stage`. Functionally correct, low priority.

## 6. Static-cleanup pass (imports)

Removed ~20 unused imports across 11 files; fixed `vgctl.py`'s `yellow()` → `yel()`
crash. `pyflakes` is now clean except 2 unused `typing` imports in `selfdev.py`.
**Assessment:** net positive, low risk (all deletions were confirmed unused via
`grep`/compile). The `selfdev.py` imports should be dropped to hit zero warnings.

## 7. Overall refactoring health

| Area | Before | After | Risk |
|---|---|---|---|
| Sentence TTS | shared queue + broken splitter | isolated `playSingle` + correct splitter | low |
| Auto-play | always-on global | per-session `sessionAutoPlay` | low |
| Tool logging | string-leak on 1 endpoint | consistent parse on both | low |
| Mission loop | wrong event loop (never ran) | `run_coroutine_threadsafe` + resume | low |
| Imports | ~20 unused + 1 crash | clean | none |

**Conclusion:** the recent refactors materially improved correctness (3 real bugs
fixed) without introducing new coupling. The single highest-value follow-up is
deleting the legacy `drainSentences` regex + fallback once streaming adopts the new
splitter, which removes the last piece of defensive code guarding the old bug.
