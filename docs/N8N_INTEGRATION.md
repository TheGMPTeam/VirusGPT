# n8n Integration — VirusGPT

> **Canonical, reproducible reference.** If n8n "stops working", work the
> checklist at the bottom **before** touching code.

## Components
VirusGPT talks to n8n three ways. Know which is which:

1. **n8n-mcp (czlonkowski/n8n-mcp)** — the "API MCP". A documentation +
   workflow-management MCP server. VirusGPT **spawns it as an HTTP Streamable
   server** on a local port and connects to it with a direct HTTP MCP client.
   Provides node docs, workflow build/trigger, credentials, executions, etc.
   This is the path that was broken (stdio deadlock) and is now fixed.
2. **n8n REST adapter (virtual)** — `services/n8n.py` wrapped as MCP tools
   (`n8n_list_workflows`, `n8n_trigger_workflow`, ...) via the `n8n-adapter`.
   Used by the chat agent and the Settings panel's list_workflows.
3. **n8n instance-level MCP (n8n-io/skills)** — a *separate* server n8n itself
   exposes at `Settings → Instance-level MCP` (`/mcp-server/http`), using
   browser OAuth (the "secure cookie" path). VirusGPT does NOT use this; it's
   for coding agents like Claude Code. Don't conflate it with n8n-mcp.

## Auth model (important)
- **n8n-mcp uses the n8n API KEY** (`N8N_API_KEY` / `VG_N8N_TOKEN`). This is
  the correct, documented auth — NOT cookie/session. The key is sent to n8n as
  `X-N8N-API-KEY`.
- If `list_workflows` / workflow tools 401 with "Unauthorized", the **API key
  is invalid for this n8n instance** (revoked, or the n8n container was
  re-deployed with a new encryption key, orphaning old keys). Fix: regenerate
  the key in n8n **Settings → API** and update `VG_N8N_TOKEN` in `.env`.
- n8n-mcp's own HTTP endpoint is protected by `MCP_AUTH_TOKEN`/`AUTH_TOKEN`
  (a 32+ char token VirusGPT generates per run and sends as
  `Authorization: *** — unrelated to the n8n API key.

## How the connection works (current, fixed)
`services/mcp_client.connect_all()`:
1. If n8n is enabled, spawn `n8n-mcp` (the npx-cached `dist/mcp/index.js`) as
   an **HTTP server**: `N8N_MODE=true MCP_MODE=http N8N_API_URL N8N_API_KEY
   MCP_AUTH_TOKEN AUTH_TOKEN PORT=8705 LOG_LEVEL=error`.
2. Connect to `http://127.0.0.1:8705/mcp` via `services/n8n_mcp_http.py` — a
   minimal direct MCP-HTTP client (httpx, **no mcp SDK**) that speaks
   JSON-RPC-over-POST with the `Mcp-Session-Id` header. This avoids the Python
   `mcp` SDK's stdio/SSE deadlock (anyio cancel-scope crash on handshake).
3. Register the discovered tools (25+) in `TOOL_REGISTRY`.

The `mcp` Python SDK is pinned to **1.9.4** (the version `mcp_client.py` was
authored against). Do NOT upgrade it — 2.x changes the `ClientSession` API and
breaks the SSE/stdio paths.

## Failure matrix
| Symptom | Cause | Fix |
|---|---|---|
| `discovered_tools` = 3 (adapter only), n8n-mcp not running | n8n-mcp spawn failed or HTTP connect failed | check app log for `[mcp] n8n-mcp spawn failed`; ensure `node` + npx-cached n8n-mcp exist |
| n8n-mcp running but tools=0 | server not ready at connect (race) | server needs ~2s; `connect_all` already waits; restart app |
| `list_workflows` 401 "Unauthorized" | n8n API key invalid for instance | regenerate key in n8n Settings → API; update `VG_N8N_TOKEN` in `.env`; restart |
| All REST attempts 401 (empty/bogus/real key) | encryption-key/instance mismatch | same as above — key is orphaned |

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
   → `200` means key OK; `401` means key invalid → regenerate (step 4).
3. `curl -s -m8 http://127.0.0.1:8500/api/mcp/status` →
   `discovered_tools` should be ~28 (25 n8n-mcp + 3 adapter). If n8n-mcp
   missing, restart the VirusGPT app.
4. Key invalid? In n8n (http://10.0.0.120:5678 → Settings → API) create a new
   API key, paste into `.env` as `VG_N8N_TOKEN=...`, restart VirusGPT.
