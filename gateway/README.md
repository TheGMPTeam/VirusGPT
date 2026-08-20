# Gateway Supervisor

The VirusGPT gateway is a lightweight supervisor that keeps the local stack
alive and runs scheduled jobs. It is part of THIS project — no external
dependency on Hermes, cron, or launchd.

## What it does

On a continuous loop:

1. **Heartbeat** — every `VG_GW_HEARTBATE` (default 30s), probe
   `/api/health` on the main server. Record status + timestamp to
   `data/gateway/heartbeat.json`. If the server is down for
   `VG_GW_REVIVE` (default 15s), run `launch.sh` to revive it.
2. **Cron scheduler** — runs built-in and user-defined jobs on intervals.

## Built-in jobs

| Job | Interval | Action |
|-----|----------|--------|
| `launch_check` | 60s | ensure whole stack is up (delegates to `launch.sh`) |
| `memory_maintain` | 30m | light self-check of the memory store |
| `db_backup` | 24h | snapshot SQLite DB to `data/db_backups/` (auto-pruned to 10) |
| `selfdev` | 1h | Dreamer cycle: research → fact-check → dream |

## User jobs

Add to `data/gateway/crontab.json`:

```json
[{"name":"myjob","every_sec":3600,"cmd":"echo hello"}]
```

Reloaded automatically every 10 seconds; no restart needed.

## Run

```bash
./gateway/service.py          # foreground, Ctrl-C to stop
# or via launch.sh (auto-started with the stack)
```

Status is exposed to the UI through `/api/gateway/status` (which reads
`data/gateway/heartbeat.json`).

## Configuration

| Env | Default | Notes |
|-----|---------|-------|
| `VG_BASE` | `http://localhost:8500` | server to monitor |
| `VG_GW_HEARTBEAT` | `30` | seconds between health probes |
| `VG_GW_REVIVE` | `15` | seconds down before revive |

## Notes

- If `httpx` is not installed, the gateway runs but reports `ok: false` on
  every heartbeat. Install it (`pip install httpx`) for real probing.
- Designed to survive being launched from `cron`, `launchd`, or `launch.sh`.
- Keeps the last 50 heartbeats in the JSON file for UI sparkline display.
