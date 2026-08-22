# n8n Integration — VirusGPT (canonical reference)

> If n8n "stops working", follow the **Recovery checklist** at the bottom
> BEFORE editing code. This doc reflects the shipped `beta` design.

## The three n8n paths (know which is which)
VirusGPT touches n8n in three distinct ways — do not conflate them:

1. **n8n-mcp (czlonkowski/n8n-mcp)** — the "API MCP". A documentation +
   workflow-management MCP server. VirusGPT **spawns it as an HTTP Streamable
   server** on a local port and connects to it with a direct HTTP client.
   Provides node docs, workflow build/trigger, credentials, executions, etc.
   **This is the path that was broken (stdio deadlock) and is now fixed.**
2. **n8n REST adapter (virtual)** — `services/n8n.py` wrapped as MCP tools
   (`n8n_list_workflows`, `n8n_trigger_workflow`, ...) via the `n8n-adapter`.
   Used by the chat agent and the Settings panel's `list_workflows`.
3. **n8n instance-level MCP (n8n-io/skills)** — a *separate* server n8n itself
   exposes at `Settings → Instance-level MCP` (`/mcp-server/http`), using
   browser OAuth (the "secure cookie" path). This is for coding agents like
   Claude Code (`n8n-io/skills` is a skill pack for those). **VirusGPT does NOT
   use this path.** Don't confuse it with n8n-mcp.

## Auth model (read this — it's the usual gotcha)
- **n8n-mcp authenticates to n8n with the n8n API KEY** (`N8N_API_KEY`, sourced
  from `VG_N8N_TOKEN` in `.env`). This is the correct, documented auth — it is
  sent to n8n as `X-N8N-API-KEY`. **Not** cookie/session.
- If workflow tools (`n8n_create_workflow`, `n8n_get_workflow`, etc.) 401 with
  "Unauthorized", the **API key is invalid for this n8n instance** (revoked, or
  the n8n container was re-deployed with a new encryption key, orphaning old
  keys). Fix: regenerate the key in n8n **Settings → API** and update
  `VG_N8N_TOKEN` in `.env`; restart VirusGPT.
- n8n-mcp's **own** HTTP endpoint is protected by `MCP_AUTH_TOKEN`/`AUTH_TOKEN`
  (a 32+ char token VirusGPT generates per run and sends as
  `Authorization: *** — unrelated to the n8n API key. VirusGPT never exposes
  this token; it's local-only between the spawned server and the client.

## How the n8n-mcp connection works (current, working)
`services/mcp_client.connect_all()`:
1. If n8n is enabled and `node` + the npx-cached n8n-mcp bin exist, spawn
   n8n-mcp as an **HTTP Streamable server**:
   ```
   N8N_MODE=true MCP_MODE=http
   N8N_API_URL=<n8n base_url>
   N8N_API_KEY=$VG_N8N_TOKEN
   MCP_AUTH_TOKEN=<generated 32-hex>   AUTH_TOKEN=<same>
   PORT=8705  LOG_LEVEL=error
   node ~/.npm/_npx/*/node_modules/n8n-mcp/dist/mcp/index.js
   ```
2. Connect to `http://127.0.0.1:8705/mcp` via **`services/n8n_mcp_http.py`** —
   a minimal direct MCP-HTTP client (httpx, **no mcp SDK**) that speaks
   JSON-RPC-over-POST with the `Mcp-Session-Id` header. This is the only
   transport that connects to n8n-mcp 2.x on this Node 26 build; the Python
   `mcp` SDK's stdio/SSE clients **deadlock** on the initialize handshake.
3. Register the discovered tools (~25) in `TOOL_REGISTRY`.

### Why a custom HTTP client (and not the mcp SDK)
Empirically tested and confirmed dead:
- `mcp` SDK **stdio_client** → handshake never returns (anyio cancel-scope
  crash on `initialize()`).
- `mcp` SDK **sse_client** (against `/sse` and `/mcp`) → "unhandled errors in a
  TaskGroup" / hangs, even with the watchdog thread.
- **Direct httpx JSON-RPC** (initialize → notifications/initialized →
  tools/list) → works in ~2s, 25 tools, real content. ✅

The `mcp` Python SDK is pinned to **1.9.4** (the version `mcp_client.py` was
authored against). Do NOT upgrade it — 2.x changes the `ClientSession` API and
breaks the remaining SSE/stdio paths.

## Files
| File | Role |
|---|---|
| `services/mcp_client.py` | `connect_all()` spawns n8n-mcp HTTP + connects; `call_mcp_tool()` routes n8n-mcp calls |
| `services/n8n_mcp_http.py` | Minimal httpx MCP-Streamable-HTTP client (no SDK) |
| `services/n8n.py` | n8n REST adapter (`X-N8N-API-KEY`) — `list_workflows`, `trigger_workflow`, etc. |
| `config.json` → `services.n8n` | `enabled`, `base_url`, `timeout` |
| `.env` → `VG_N8N_TOKEN` | the n8n API key (also pushed to `os.environ` on settings save) |
| `docs/N8N_MCP.md` | Full n8n-mcp reference (env vars, tools, deployment) |

## Failure matrix
| Symptom | Cause | Fix |
|---|---|---|
| `discovered_tools` = 3 (adapter only); n8n-mcp not running | spawn failed or HTTP connect failed | check app log for `[mcp] n8n-mcp spawn failed`; ensure `node` + npx-cached n8n-mcp exist |
| n8n-mcp process up but tools = 0 | server not ready at connect (race) | server needs ~2s; `connect_all` waits; restart app |
| workflow tools 401 "Unauthorized" | n8n API key invalid for instance | regenerate key in n8n Settings → API; update `VG_N8N_TOKEN`; restart |
| all REST attempts 401 (empty/bogus/real key) | encryption-key/instance mismatch | same as above — key is orphaned |

## Verify (one command)
```bash
.venv/bin/python scripts/verify_n8n.py
```
Checks: n8n reachable, API key present, authenticated (GET /api/v1/workflows
returns 200), and n8n-mcp tool count > 0.

## Recovery checklist (when it breaks)
1. `curl -s -m8 http://10.0.0.120:5678/healthz` → must be `200`. If not, the
   Windows n8n box is down.
2. `curl -s -m8 -H "X-N8N-API-KEY: $VG_N8N_TOKEN" http://10.0.0.120:5678/api/v1/workflows`
   → `200` = key OK; `401` = key invalid → regenerate (step 4).
3. `curl -s -m8 http://127.0.0.1:8500/api/mcp/status` → `discovered_tools`
   should be ~28 (25 n8n-mcp + 3 adapter). If n8n-mcp missing, restart the
   VirusGPT app.
4. Key invalid? In n8n (`http://10.0.0.120:5678 → Settings → API`) create a new
   API key, paste into `.env` as `VG_N8N_TOKEN=...`, restart VirusGPT.
