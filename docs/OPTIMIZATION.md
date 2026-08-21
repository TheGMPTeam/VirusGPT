# Optimization Report — VirusGPT

Scope: source under `server.py`, `services/`, `autonomous/`, `gateway/`, `memory/`,
`app/assets/` (the shippable product — vendored `pockettts/`, `whisper/`, and the
`dist/` frozen bundles are excluded as third-party/build artifacts).

## 1. Size & dependency footprint

| Metric | Value |
|---|---|
| Python LOC (core) | ~9,860 |
| JS LOC (frontend) | ~1,885 |
| Runtime Python deps (`requirements.txt`) | 5 (`fastapi`, `uvicorn`, `httpx`, `pydantic`, `python-multipart`) |
| External services | Ollama, PocketTTS, Whisper, ComfyUI, Memory MCP, Gateway |
| Static-analysis debt (`TODO`/`FIXME`/`HACK`) | **0** |
| `pyflakes` warnings (core) | **2** (harmless unused `Any`/`Dict` imports in `autonomous/selfdev.py`) |
| `py_compile` / `compileall` | clean |
| Test coverage (this release) | 71 automated tests (54 backend + 16 frontend + 1 split unit) |

**Verdict:** the runtime is lean. Five direct dependencies and a deliberately
small surface. This is already well-optimized for a local/offline agent stack.

## 2. What is already good

- **Graceful degradation.** Every external service is wrapped in a health-check
  that never fails the request path (e.g. `/api/health` reports `comfyui:false`
  instead of 500). Each feature degrades independently.
- **Small-context windowing.** `trim_history()` caps chat history to the last N
  messages / M chars before it ever reaches the model — bounds token cost on
  long sessions. Persona context is also windowed (`WINDOW*2`).
- **Sandboxed tool execution.** Shell tools run under a timeout + an allowlist of
  safe binaries; file ops are confined to `data/sandbox`. No arbitrary FS escape.
- **Strict serial TTS.** The audio queue plays one sentence at a time and resolves
  only on `onended`, so there is never overlapping speech or leaked `Audio`
  elements.
- **Offline-testable by design.** All upstreams are injected via `services`
  modules, so the entire backend suite runs with zero network (mocked at the
  `llm`/`tts`/`stt`/`comfyui`/`memory` boundary).

## 3. Optimization opportunities (ranked)

### 3.1 Bundle the frozen desktop app (HIGH impact, low effort)
`dist/` contains a **stale** frozen bundle (`server.py` there is 939 LOC vs the
current 936 — i.e. an older build). Shipping the desktop app without rebuilding
leaves users on old frontend/JS. Rebuild via `desktop/build-macos.py` after every
frontend change and either commit the bundle or document the rebuild step in
`install.sh`.
**Action:** add a CI job (or release script) that rebuilds the `.app` on tag.

### 3.2 Hot-path regex in the TTS streamer (MEDIUM)
`drainSentences()` (tts.js) re-runs a global regex (`/[^.!?\n]*[.!?]+|\n+/g`)
over the **entire** `ttsBuf` on every streamed chunk. As a reply grows to
hundreds of chars this is O(n²) across the turn. After the `splitSentences`
rewrite the streaming path no longer needs the whole-buffer scan — it can keep a
cursor and only scan the newly-appended tail.
**Action:** switch `drainSentences` to scan only `ttsBuf.slice(ttsDone)` each
call (the fallback re-split already does this for the broken-regex case).

### 3.3 Duplicate mission-streaming code (MEDIUM)
`/api/autonomous/stream/{id}` and `/api/autonomous/status/{id}` build near-
identical task/event snapshots inline (two copies of the same dict comprehension
with the `json.loads` parse). A shared `_mission_snapshot(mission, tasks, events)`
helper would remove the duplication and prevent the two from drifting.
**Action:** extract the snapshot builder; have both routes call it.

### 3.4 No response caching on static asset negotiation (LOW)
`/api/tts/voices` and `/api/health` hit upstreams on every call (the latter also
lists Ollama models). These are slow (network) and rarely change. A short-TTL
cache (e.g. 5–15s) would cut backend load during rapid UI polling. The frontend
already polls `/api/autonomous/status` every 1.5s and `/api/health` every 15s.
**Action:** memoize `tts.list_voices` / `llm.list_models` / `comfyui_models` with
a TTL decorator.

### 3.5 Chat router LLM call per message (LOW / by-design)
`choosePersona('router')` makes an extra `/api/chat` round-trip to pick a persona
for multi-persona rooms. This is intentional (smart routing) but doubles latency
for the first token. Single-persona rooms skip it (already optimized).

### 3.6 Frontend: no module bundler / hash-busting (LOW)
Assets are served as 12 separate `<script>` tags with `?v=13` cache-busters
(manually bumped). Fine for a local app; for the web UI over LAN it means 12
sequential script requests. Acceptable, but a single hashed bundle would be
faster on flaky phone connections.

## 4. Dead code / loose ends

- `autonomous/selfdev.py:23` — `from typing import Any, Dict` is unused; drop it
  (the only `pyflakes` noise).
- No `TODO`/`FIXME` markers anywhere in the product code — debt is being paid
  down as it's found.
- `autonomous/test_orchestrator.py` and the new `tests/` suite overlap somewhat
  in intent (both test the planner); consider consolidating the orchestrator
  tests under `tests/` for a single `pytest` entrypoint.

## 5. Benchmark recommendation

Add a tiny `tests/bench_chat_latency.py` that measures p95 time-to-first-token
and total turn time against a mocked Ollama at fixed delays, so regressions in
`trim_history` / `choosePersona` / streaming are caught numerically, not just by
assertions.
