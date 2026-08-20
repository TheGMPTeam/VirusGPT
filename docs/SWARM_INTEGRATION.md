# Swarm Integration: Hermes Agent‑Swarms ↔ VirusGPT Autonomous Engine

This document explains how to connect a **Hermes agent‑swarm** (the
idle‑by‑default, Kanban‑driven multi‑agent harness in the `agent-swarm` skill) to
VirusGPT's **existing autonomous engine** — the `Supervisor` in
`autonomous/orchestrator.py` driven over HTTP by `POST /api/autonomous/start`.

The short version: **VirusGPT is already an autonomous mission runtime with a clean
REST API.** You do not need to reimplement planning, execution, retry, verification,
or recovery inside Hermes — you drive a VirusGPT *mission* with a single `curl` (or
any HTTP client) and let VirusGPT's supervisor do the heavy lifting, while Hermes
owns the higher‑level swarm graph (research → code → docs → verify → synthesize).

---

## 1. What each side brings

| Layer | Hermes agent‑swarm | VirusGPT autonomous engine |
| ----- | ------------------ | -------------------------- |
| Orchestration | Kanban board + gateway dispatcher; role profiles (`researcher`, `coder`, `writer`, `verifier`, `orchestrator`) | `Supervisor` state machine (`orchestrator.py`) |
| Unit of work | Kanban card (task) | Mission → subtasks |
| Planning | Orchestrator decomposes a goal into cards | LLM planner emits `@PersonName: subtask` lines |
| Execution | Dispatcher spawns a profile subprocess | `AgentRuntime` ReAct loop w/ native Ollama tool calling |
| Retry / Recovery | Card re‑dispatch + review cycles | `_execute_with_retry` + LLM verification + planner‑led recovery |
| Transport | `kanban_*` tools (in‑process DB) | REST + SSE (`/api/autonomous/*`) |
| Persisted state | Kanban SQLite board | `missions` / `tasks` / `events` tables (survives restart) |

The two are complementary, not competing: **Hermes decides *what* to build; VirusGPT
executes the *mission*.** A common pattern is to make VirusGPT one *worker* inside a
larger Hermes swarm, or to use Hermes only as the trigger + monitor and let VirusGPT
run the whole mission end‑to‑end.

---

## 2. The VirusGPT autonomous API (the contract you call)

All endpoints are served by `server.py` on the VirusGPT port (**default `:8500`**).

| Method | Path | Purpose |
| ------ | ---- | ------- |
| `POST` | `/api/autonomous/start` | Start a mission. Body `{goal, room_personas?}`. Returns `{ok, mission_id, planner, status, stream_url}` immediately. `400` if `goal` is missing. |
| `GET`  | `/api/autonomous/stream/{mission_id}` | **SSE** live feed: mission + subtasks + recent events + artifacts. Ends on a terminal status. |
| `GET`  | `/api/autonomous/status/{mission_id}` | One‑shot snapshot. `404` if unknown. |
| `POST` | `/api/autonomous/stop/{mission_id}` | Cooperative cancel (checked before each step). `404` if unknown. |
| `POST` | `/api/autonomous/resume/{mission_id}` | Resume a single interrupted mission. `404` if unknown, `409` if it is already terminal. *(Bulk resume is automatic — the server calls `Supervisor.resume_interrupted_missions()` on boot — there is no bulk‑resume HTTP endpoint.)* |
| `GET`  | `/api/health` | Aggregate health (`ollama`/`tts`/`whisper` booleans, `models[]`, `default_model`). |

### The `start` body

```json
{
  "goal": "Summarize the top local LLM inference tools",
  "room_personas": [
    {"name": "Planner", "role": "planner", "system_prompt": "", "skills": "", "voice": "alba"},
    {"name": "Researcher", "role": "worker", "system_prompt": "", "skills": "", "voice": "alba"}
  ]
}
```

- `goal` (required string) — the high‑level objective.
- `room_personas` (optional list) — if omitted, the server loads its default room
  personas (`_load_personas()` in `server.py`). Each persona is a dict with
  `name`, `role` (`"planner"` or `"worker"`), `system_prompt`, `skills`, `voice`.
  The planner is the first persona whose `role == "planner"` (else the first
  persona). The planner emits one `@<PersonName>: <subtask>` line per worker; each
  becomes a `Task`.

Mission lifecycle: `planning → running → verifying → completed` (a mission may also
end `failed` or `blocked`; `cancelled` is set by a cooperative stop). Every transition
and event is persisted, so the state survives a server restart.

---

## 3. Minimal integration: start a mission from Hermes (curl)

This is the snippet the swarm uses to hand a goal to VirusGPT. `start` returns
immediately — the mission runs in the background as an `asyncio.Task`.

```bash
# 1. Start a mission on the running VirusGPT server (default :8500)
curl -s -X POST localhost:8500/api/autonomous/start \
  -H 'Content-Type: application/json' \
  -d '{"goal": "Summarize the top local LLM inference tools"}'

# → {"ok": true, "mission_id": "M-<ts>-<hex>", "planner": "VirusGPT",
#    "status": "planning", "stream_url": "/api/autonomous/stream/M-<ts>-<hex>"}
```

Capture the `mission_id` and stream it:

```bash
# 2. Live SSE feed (Ctrl-C to stop; it also ends on a terminal status)
MISSION_ID="M-<ts>-<hex>"
curl -N localhost:8500/api/autonomous/stream/$MISSION_ID

# 3. Or poll a one-shot snapshot
curl -s localhost:8500/api/autonomous/status/$MISSION_ID

# Stop early if needed (cooperative)
curl -s -X POST localhost:8500/api/autonomous/stop/$MISSION_ID
```

---

## 4. Pattern A — VirusGPT as one worker in a Hermes swarm

Use this when the goal is bigger than a single mission and you want Hermes's
dispatcher to coordinate role profiles *around* VirusGPT.

```
goal (Hermes root, done when synthesizer finishes)
 ├─ worker: researcher   →  web research brief
 ├─ worker: coder        →  code/benchmark
 ├─ worker: writer       →  docs
 ├─ worker: virusgpt     →  POST /api/autonomous/start <goal>   ← calls VirusGPT engine
 ├─ verifier: verifier   (gated on all workers)
 └─ synthesizer: orchestrator (gated on verifier)
```

The `virusgpt` worker is just a Hermes profile whose job is to start and monitor a
VirusGPT mission over HTTP. A minimal worker body (pseudo‑code the `coder`/`writer`
profile can run):

```python
import json, subprocess, time, urllib.request

VG_BASE = "http://localhost:8500"

def vg_post(path, body):
    req = urllib.request.Request(
        f"{VG_BASE}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def run_mission(goal):
    started = vg_post("/api/autonomous/start", {"goal": goal})
    mid = started["mission_id"]
    # Poll the snapshot until terminal.
    while True:
        st = json.loads(urllib.request.urlopen(
            f"{VG_BASE}/api/autonomous/status/{mid}", timeout=30).read())
        if st["status"] in {"completed", "failed", "blocked", "cancelled"}:
            return st
        time.sleep(2)

result = run_mission("Compare local LLM inference tools and emit a benchmark table")
print(result["final_result"])   # mission.final_result (synthesized answer)
```

> Note: the real engine returns `final_result` only after the `verifying` stage
> completes. If the server is restarted mid‑mission, interrupted missions are
> re‑driven automatically on boot; call `POST /api/autonomous/resume/{mission_id}`
> to re‑drive a specific one on demand. VirusGPT re‑hydrates from persisted state
> and re‑executes the remaining subtasks.

---

## 5. Pattern B — Hermes is only the trigger + monitor

When VirusGPT can plan and execute the whole goal itself, Hermes does not need a full
swarm graph. A single orchestrator card runs the `curl` from §3, watches the SSE
stream, and writes the `final_result` back to the Kanban board as the card's
deliverable. This is the thinnest integration and the easiest to stand up.

```bash
# From inside a Hermes orchestrator task:
MID=$(curl -s -X POST localhost:8500/api/autonomous/start \
        -H 'Content-Type: application/json' \
        -d '{"goal": "Produce a comparison of local LLM inference tools"}' \
      | python3 -c 'import sys,json;print(json.load(sys.stdin)["mission_id"])')

# Block until terminal, then fetch the synthesized result.
while :; do
  S=$(curl -s localhost:8500/api/autonomous/status/$MID | python3 -c 'import sys,json;print(json.load(sys.stdin)["status"])')
  case "$S" in completed|failed|blocked|cancelled) break;; esac
  sleep 3
done
curl -s localhost:8500/api/autonomous/status/$MID | python3 -c 'import sys,json;print(json.load(sys.stdin)["final_result"])'
```

---

## 6. Wiring it into the agent‑swarm skill

The `agent-swarm` skill (`~/.hermes/skills/agent-swarm/scripts/swarm.py`) builds the
swarm graph on the Kanban board; the gateway dispatcher spawns the assigned profiles.

1. **`setup`** — create the five role profiles (idempotent). No processes are spawned.
2. Add a `virusgpt` worker profile (or reuse `coder`) whose toolset includes
   `web`/`terminal`/`file` so it can reach the REST API and run the §4 snippet.
3. **`launch "GOAL"`** — build the graph. Make the VirusGPT hand‑off a card whose
   body is "call `POST /api/autonomous/start` with this goal, poll
   `/api/autonomous/status/{id}`, return `final_result`".
4. **`status`** — watch the board + gateway dispatcher. The dispatcher stays live
   until the root card is done, so workers → VirusGPT call → verifier → synthesizer
   flow automatically.

Pitfalls (from the swarm skill): do **not** run a standalone `kanban daemon` — the
dispatcher lives inside the gateway (`hermes gateway status` must show running). Give
worker profiles a full toolset (`terminal,code_execution,file,kanban,web,skills,...`)
or they auto‑block on their first tool call.

---

## 7. End‑to‑end example (Hermes → VirusGPT → result)

```bash
# A) Hermes launches the swarm
python3 ~/.hermes/skills/agent-swarm/scripts/swarm.py launch \
  "Compare the top local LLM inference tools: research, build a benchmark script, write a README, verify, synthesize a report. Delegate the end-to-end comparison to VirusGPT via /api/autonomous/start and incorporate its final_result."

# B) The virusgpt worker card runs this (equivalent to §3/§4):
curl -s -X POST localhost:8500/api/autonomous/start \
  -H 'Content-Type: application/json' \
  -d '{"goal": "Compare the top local LLM inference tools and emit a benchmark table + README"}'

# C) Hermes verifier pulls the mission's final_result and checks it against requirements,
#    then the orchestrator synthesizes the final deliverable on the Kanban board.
```

---

## 8. Operational notes

- **Server must be running.** The autonomous API is served by `server.py` (`./launch.sh`
  starts it on `:8500` plus voice services). `GET /api/health` always returns `200`
  regardless of backend state, so read its JSON body as the readiness check: confirm
  `"ollama": true` (and that `"models"` contains your target model) **before** starting
  a mission — a `200` alone does not mean Ollama is reachable. Note `models` falls back
  to `[default_model]` when Ollama is down, so a non‑empty `models` list is *not* proof
  of connectivity.
- **No‑auth, local‑first.** VirusGPT is designed for fully‑local/offline use; the API
  has no auth. Only expose `:8500` on a trusted network.
- **Cooperative cancellation only.** `stop` sets a per‑mission flag checked before each
  step; there is no hard kill of an in‑flight LLM call.
- **Resume is built in.** Interrupted (in‑flight) missions are auto‑resumed on server
  boot; `POST /api/autonomous/resume/{mission_id}` re‑drives a specific one on demand.
- **Persistence.** `missions` / `tasks` / `events` tables (SQLite `data/virusgpt.db`
  by default, or PlanetScale/MySQL) hold all state, so a mission's progress survives a
  restart even though the in‑flight execution does not.

---

## 9. Quick reference

```bash
# Start
curl -s -X POST localhost:8500/api/autonomous/start \
  -H 'Content-Type: application/json' -d '{"goal": "YOUR GOAL"}'

# Watch (SSE)
curl -N localhost:8500/api/autonomous/stream/$MISSION_ID

# Snapshot
curl -s localhost:8500/api/autonomous/status/$MISSION_ID

# Stop (cooperative)
curl -s -X POST localhost:8500/api/autonomous/stop/$MISSION_ID

# Resume a single interrupted mission (bulk resume is automatic on boot)
curl -s -X POST localhost:8500/api/autonomous/resume/$MISSION_ID

# Health
curl -s localhost:8500/api/health
```

---

## 10. Built artifact — `tools/hermes_bridge.py`

The patterns above are **implemented, not just described**. A dependency‑free
(stdlib‑only, Python 3.8+) CLI lives at `tools/hermes_bridge.py` so a Hermes
worker can drive the autonomous engine with one shell call — no `requests`,
no `curl` plumbing. It speaks every endpoint in §2 and is verified end‑to‑end
against the running server.

```bash
# From a Hermes virusgpt worker card (Pattern A), or as the whole job (Pattern B):

# Pattern A — start, then poll/stream separately
MID=$(python3 tools/hermes_bridge.py --base http://localhost:8500 \
        start "Compare the top local LLM inference tools" --quiet)
python3 tools/hermes_bridge.py --base http://localhost:8500 status $MID
python3 tools/hermes_bridge.py --base http://localhost:8500 stream $MID

# Pattern B — start, block until terminal, print final_result (the sweet spot)
python3 tools/hermes_bridge.py --base http://localhost:8500 \
        run "Compare the top local LLM inference tools and emit a benchmark table" \
        --timeout 1800

# Lifecycle controls
python3 tools/hermes_bridge.py --base http://localhost:8500 stop   $MID
python3 tools/hermes_bridge.py --base http://localhost:8500 resume $MID
python3 tools/hermes_bridge.py --base http://localhost:8500 health
```

Subcommands: `start` (opts `--personas`/`--room`/`--quiet`),
`status`, `stream` (`--quiet` prints only the terminal `end` event),
`stop`, `resume`, `health`, and `run` (opts `--timeout`/`--interval`/`--skip-health`/`--quiet`).
`run --quiet` prints **only** `final_result`, ideal for piping back into a
Kanban card's deliverable. `--base` overrides the default `http://localhost:8500`.

The bridge persists nothing itself — all mission state stays in VirusGPT's own
`missions`/`tasks`/`events` tables, so a server restart mid‑run is recoverable
via `resume`.

See also: `autonomous/README.md` (full engine internals), `docs/ARCHITECTURE.md`,
`docs/STATUS.md`, and the `agent-swarm` skill (`~/.hermes/skills/agent-swarm/SKILL.md`).
