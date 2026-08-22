# n8n-mcp Reference (czlonkowski/n8n-mcp)

This is the upstream n8n-mcp reference, trimmed to what VirusGPT uses. Source:
<https://github.com/czlonkowski/n8n-mcp> and `docs/N8N_DEPLOYMENT.md`.

## What n8n-mcp is
An MCP server that gives an AI client deep knowledge of n8n's ~2,500 nodes plus
workflow management (create/update/trigger/get workflows, credentials, data
tables, executions) through the n8n API.

## How VirusGPT runs it
Per `docs/N8N_INTEGRATION.md`, VirusGPT **spawns n8n-mcp as an HTTP Streamable
server** (not stdio) and connects with a direct HTTP client. The spawn env:

| Variable | Value | Notes |
|---|---|---|
| `N8N_MODE` | `true` | enables n8n integration mode |
| `MCP_MODE` | `http` | HTTP Streamable server (not stdio) |
| `N8N_API_URL` | `http://10.0.0.120:5678` | from `config.services.n8n.base_url` |
| `N8N_API_KEY` | `$VG_N8N_TOKEN` | the n8n API key (Settings → API in n8n) |
| `MCP_AUTH_TOKEN` | 32-hex (generated per run) | auth for the MCP endpoint |
| `AUTH_TOKEN` | same as `MCP_AUTH_TOKEN` | must match exactly |
| `PORT` | `8705` | local port |
| `LOG_LEVEL` | `error` | quiet |

Endpoint: `http://127.0.0.1:8705/mcp` (also `/sse` legacy, `/health`).

## Tool categories (as of n8n-mcp 2.x)
- **Documentation** (no API key): `list_nodes`, `search_nodes`,
  `get_node_info`, `get_node_essentials`, `validate_workflow`,
  `get_node_documentation`, `tools_documentation`.
- **Management** (requires `N8N_API_KEY`): `n8n_create_workflow`,
  `n8n_update_workflow`, `n8n_get_workflow`, `n8n_delete_workflow`,
  `n8n_list_workflows`, `n8n_trigger_workflow`, `n8n_deploy_template`,
  `n8n_manage_credentials`, `n8n_manage_datatable`, `n8n_audit_instance`,
  `n8n_health_check`.

## Connecting from n8n itself (optional, not used by VirusGPT)
n8n can use n8n-mcp via its **MCP Client Tool** node:
Server URL `http://<host>:8705/mcp`, `Auth Token: <MCP_AUTH_TOKEN>`,
Transport `HTTP Streamable`. This is the documented production pattern;
VirusGPT instead drives n8n-mcp from Python.

## Troubleshooting
- **n8n-mcp won't start**: ensure `node` is on PATH and the npx-cached bin
  exists at `~/.npm/_npx/*/node_modules/n8n-mcp/dist/mcp/index.js`. If the npm
  cache was cleared, re-run `npx -y n8n-mcp` once to repopulate.
- **401 on /mcp**: wrong `MCP_AUTH_TOKEN`/`AUTH_TOKEN` mismatch, or request
  missing the `Authorization: *** header.
- **n8n management tools 401**: the `N8N_API_KEY` is invalid for the n8n
  instance — regenerate in n8n Settings → API.
- **Don't use stdio with the Python mcp SDK here**: it deadlocks on Node 26 /
  n8n-mcp 2.x. Use HTTP mode + the direct httpx client (VirusGPT's approach).
