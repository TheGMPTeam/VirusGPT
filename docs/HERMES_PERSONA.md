# Hermes Persona (A2A Relay)

The **Hermes** persona relays a user's message to the **Hermes Agent** peer over
Agent-to-Agent (A2A) JSON-RPC and returns the agent's reply verbatim. Talking to
the Hermes persona == talking to the Hermes agent.

## How it works

* `data/personas.json` defines the `Hermes` persona (`role: "relay"`,
  `tools: ["ask_hermes"]`). Its `system_prompt` instructs the model to ALWAYS
  call `ask_hermes` and return the reply unchanged.
* `autonomous/tools.py` registers the `ask_hermes` tool (params: `message`).
* `services/hermes.py` implements `ask_hermes(text)`:
  1. `message/send` JSON-RPC (the method the hermes-VirusPC peer actually
     implements — it returns the FINAL task result synchronously, so no poll is
     needed) with `Authorization: Bearer <token>`. Roles use the A2A enum
     (`ROLE_USER` / `ROLE_AGENT`); the reply is read from
     `result.status.message.parts[].text` (or `result.artifacts[].parts[].text`).
  2. If a peer reports `method not found` (-32601) for `message/send`, it falls
     back to the documented `tasks/send` + `tasks/get` poll pattern
     (`tasks/get` polled every ~2s until `status.state` is terminal).
* `server.py /api/chat` now exposes a persona's `tools` to the model and runs a
  ReAct loop: stream the model, execute any `tool_calls` via `run_tool`, loop
  until the model answers with no further tool call. A single-tool relay persona
  forces `tool_choice` to that tool so the message is ALWAYS relayed.
* `chat.js send()` posts `{persona, tools}` to `/api/chat` (cache-buster bumped).

## Enabling

No secret is committed. The send-token is resolved at runtime in this order:

1. `VG_HERMES_TOKEN` env var (highest priority).
2. `services.hermes.token` in `config.json` (leave empty — do not commit tokens).
3. The local peer-token file `/Users/Master/.hermes/cache/a2a_tokens_win.env`
   key `A2A_PEER_TOKENS` (fallback used on this machine).

Base URL + enabled flag:

* `VG_HERMES_URL` env var (default `http://10.0.0.120:9900`), else
  `services.hermes.base_url` in `config.json` / `_DEFAULTS`.

`config.json` ships `services.hermes.enabled: true` on this deployment; the token
comes from the runtime file. To disable the relay, set `enabled: false` (or
`VG_HERMES_TOKEN` empty and remove the token file) — the persona then reports a
clean failure instead of relaying.

## Flow

```
user → chat.js → POST /api/chat {persona:"Hermes", tools:["ask_hermes"], messages}
     → model calls ask_hermes(message)
     → services.hermes.ask_hermes → A2A message/send → VirusPC Hermes agent
     → (final result returned synchronously) → reply text
     → model streams the agent's reply verbatim → user
```

## Failure matrix

| Condition                         | Symptom / returned value                                              | Recovery |
| --------------------------------- | -------------------------------------------------------------------- | -------- |
| Peer unreachable (offline / wrong IP) | `{"status":"failed","error":"hermes tasks/send failed: ..."}`     | Check `VG_HERMES_URL` / network to `10.0.0.120:9900`. |
| Wrong / missing token             | `{"status":"failed","error":"hermes unauthorized (-32050): token rejected by peer"}` | Set a valid `VG_HERMES_TOKEN` (or fix the `A2A_PEER_TOKENS` file). **Do not loop** — a `-32050` means the token is definitively wrong. |
| Task never reaches terminal (stuck) | `{"status":"failed","error":"hermes timeout: no terminal response within deadline","last_state":"..."}` | The agent may be busy/slow; raise `services.hermes.timeout` (default 60s) or retry. |
| Empty model reply                 | `{"status":"failed","error":"hermes task completed returned no reply text"}` | The agent answered without text parts; inspect artifacts / agent logs. |
| No token configured at all        | `{"status":"failed","error":"hermes not authenticated (no token; ...)"}` | Provide `VG_HERMES_TOKEN` or the token file. |

All failures are returned as dicts (never raised), so `/api/health` and the chat
UI stay green — the user just sees the error text relayed back.

## Checking health

```
curl -s localhost:8500/api/health | python3 -m json.tool   # look for "hermes"
curl -s localhost:8500/api/personas | python3 -m json.tool # "Hermes" present
```
