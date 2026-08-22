# n8n Integration — VirusGPT

> **Canonical, reproducible reference.** If n8n "stops working" (e.g. `list_workflows`
> returns `401 unauthorized`), work the checklist at the bottom **before** touching code.
> Most failures are credential/instance state, not VirusGPT bugs.

---

## 1. What VirusGPT uses n8n for

- List workflows (`GET /api/v1/workflows`) — shown in Settings → Connected Services → n8n.
- Trigger a workflow by id (`POST .../workflows/{id}/execute`).
- Create a workflow from nodes/connections (`POST .../workflows`).
- Get a single workflow definition (`GET .../workflows/{id}`).

All calls go through `services/n8n.py`, exposed in the UI via `services/n8n_settings.py`
(Settings → Connected Services → **n8n**).

---

## 2. Endpoint & auth (the exact working contract)

| Item | Value |
|------|-------|
| Base URL | `http://10.0.0.120:5678` (Windows Docker box, LAN) |
| API prefix | `/api/v1` |
| Auth header | `X-N8N-API-KEY: <JWT>` |
| Health (no auth) | `GET /healthz` → `{"status":"ok"}` |
| Auth test | `GET /api/v1/workflows` → `200` (key valid) / `401` (key rejected) |

The key is a **n8n public-API JWT** (`iss:"n8n"`, `aud:"public-api"`, `sub:<user-uuid>`).
It is **NOT** an `Authorization: *** bearer token for the owner login — it is the
API key generated in n8n at **Settings → API → "Create an API key"**.

Key sources (precedence, lowest → highest):
1. `config.json` → `services.n8n.api_key` (NOT committed — kept out of git on purpose)
2. `VG_N8N_TOKEN` env var (the supported way; loaded from `.env` at boot)
3. UI "api_key (secret)" field in Settings → writes it to the live runtime **and**
   into the process env (`settings_base._SECRET_ENV_MAP`) so it applies with no restart.

The client reads them in `services/n8n.py::_api_key()`:
```python
return (os.environ.get("VG_N8N_TOKEN")
        or cfg.service_cfg("n8n").get("api_key") or "").strip()
```
and sends:
```python
headers = {"X-N8N-API-KEY": key}   # only when key is non-empty
```

---

## 3. Verifying it works (one command)

```bash
cd /Users/Master/virusgpt-mac
.venv/bin/python scripts/verify_n8n.py
```
Expected output when healthy:
```
[ok] n8n reachable        http://10.0.0.120:5678  /healthz 200
[ok] api key present      (len 267)
[ok] authenticated        GET /api/v1/workflows -> 200  (N workflows)
```
Expected when broken:
```
[FAIL] authenticated       GET /api/v1/workflows -> 401 unauthorized
```
A raw curl equivalent (for manual debugging):
```bash
curl -s -m 10 "http://10.0.0.120:5678/api/v1/workflows?limit=1" \
  -H "X-N8N-API-KEY: $VG_N8N_TOKEN"
```

---

## 4. Failure matrix (what each symptom means)

| Symptom | Meaning | Fix |
|---------|---------|-----|
| `/healthz` → not 200 | n8n container down / wrong IP | Start the container on the Win box; check `10.0.0.120:5678` |
| key empty, `has_token:false` | key never loaded | Put `VG_N8N_TOKEN=...` in `.env` (repo root) and restart server |
| `authenticated:false`, every call → `401` (even `Bearer`, even `bogus`) | **n8n rejects ALL keys** | See §5 — almost always an encryption-key / instance mismatch |
| `401` only for the real key, `bogus` also 401 | key invalid for this instance | Regenerate the API key in n8n Settings → API |
| `200` but empty workflow list | no workflows exist yet | expected; create one in n8n |

---

## 5. THE GOTCHA (this is what breaks it most often)

n8n validates an API key against its runtime **`N8N_ENCRYPTION_KEY`**. If the
n8n container is recreated/redeployed **without persisting `N8N_ENCRYPTION_KEY`**,
Docker/host generates a **new random key** and **every previously-issued API key
becomes permanently invalid** — even a key that is byte-for-byte "correct."

Tell-tale sign: **all** auth attempts return `401` identically — empty, bogus,
`Bearer`, and the real `X-N8N-API-KEY`. That is NOT a header-name bug; it is the
instance no longer trusting the key's signature.

Fix (on the Windows Docker box):
1. Ensure `N8N_ENCRYPTION_KEY` is set in the n8n compose/service env and is
   **stable across restarts** (use a fixed secret, not an omitted/random one).
2. If the key was already rotated, the only remedy is to **issue a fresh API key**
   in n8n (Settings → API) and update `VG_N8N_TOKEN` in VirusGPT's `.env`.
3. Restart the VirusGPT server (or paste the new key into Settings → n8n → save).

---

## 6. Quick recovery checklist (run in order)

1. `curl -s http://10.0.0.120:5678/healthz` → must be `{"status":"ok"}`.
2. Confirm key loaded: `curl localhost:8500/api/services/n8n/settings` shows `api_key:"***"`.
3. `curl .../api/v1/workflows -H "X-N8N-API-KEY: $VG_N8N_TOKEN"` → must be `200`.
   - If `401`: regenerate the key in n8n (§5). The code is correct.
4. If the key changed, update `.env` (`VG_N8N_TOKEN=...`) and restart the server,
   OR paste it into Settings → Connected Services → n8n → `api_key (secret)` → Save.

---

## 7. Files involved

| File | Role |
|------|------|
| `services/n8n.py` | HTTP client: health, status, list/get/trigger/create, auth header |
| `services/n8n_settings.py` | Settings+tools module (registered in `server.py::SERVICE_MODULES`) |
| `server.py` (`/api/services/n8n/*`) | Settings/status/tools endpoints |
| `app/assets/js/services_ui.js` | Renders the n8n card + `api_key (secret)` field |
| `services/settings_base.py` | `write_settings` → applies secret to runtime + process env |
| `config.json` `services.n8n` | `base_url`, `enabled`, `timeout` |
| `.env` `VG_N8N_TOKEN` | the actual API key (untracked) |
| `scripts/verify_n8n.py` | one-command health + auth check |
