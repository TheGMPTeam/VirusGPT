#!/usr/bin/env python3
"""VirusGPT Gateway — heartbeats + cron jobs.

A small, self-contained supervisor that keeps the local VirusGPT stack alive and
runs scheduled jobs. It is part of THIS project (no external/Hermes dependency).

What it does, on a loop:
  • Heartbeat: every HEARTBEAT_SEC, probe the server's /api/health. Record status
    + timestamp to data/gateway/heartbeat.json. If the server is down, run
    launch.sh to revive it (auto-heal).
  • Cron: a tiny interval scheduler. Built-in jobs:
      - launch_check   : ensure the whole stack is up (delegates to launch.sh)
      - memory_maintain: run a memory self-check/query (keeps the store healthy)
    User jobs can be added to data/gateway/crontab.json:
        [{"name":"myjob","every_sec":3600,"cmd":"<shell command>"}]

Status is exposed to the UI through the main server's /api/gateway/status
(which just reads data/gateway/heartbeat.json).

Run:  ./gateway/service.py   (or: python3 gateway/service.py)
Stop: Ctrl-C. It's designed to be launched by launch.sh / cron / a launchd plist.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
# Ensure the repo root is importable (gateway may run under a clean env from launch.sh).
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DATA = ROOT / "data"
GW = DATA / "gateway"
GW.mkdir(parents=True, exist_ok=True)
HEARTBEAT_FILE = GW / "heartbeat.json"
CRONTAB_FILE = GW / "crontab.json"
LAUNCH_SH = ROOT / "launch.sh"

HEARTBEAT_SEC = int(os.environ.get("VG_GW_HEARTBEAT", "30"))
# How long the server may be down before we try to revive it.
REVIVE_AFTER_SEC = int(os.environ.get("VG_GW_REVIVE", "15"))

try:
    import httpx
    _CLIENT_OK = True
except Exception:  # noqa
    _CLIENT_OK = False


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def log(msg):
    print(f"[gateway {datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def read_json(p: Path, default):
    try:
        return json.loads(p.read_text())
    except Exception:
        return default


def write_json(p: Path, obj):
    try:
        p.write_text(json.dumps(obj, indent=2))
    except Exception as e:  # noqa
        log(f"write {p} failed: {e}")


def server_health(base: str) -> dict:
    if not _CLIENT_OK:
        return {"ok": False, "error": "httpx missing"}
    try:
        with httpx.Client(timeout=5) as c:
            r = c.get(base + "/api/health")
            if r.status_code == 200:
                return {"ok": True, **r.json()}
    except Exception as e:  # noqa
        return {"ok": False, "error": str(e)}
    return {"ok": False, "status": "non-200"}


def revive_stack():
    """Run launch.sh to bring the stack back up."""
    log("server appears down — invoking launch.sh to revive")
    try:
        subprocess.Popen(
            ["bash", str(LAUNCH_SH)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:  # noqa
        log(f"revive failed: {e}")


def load_crontab() -> list:
    jobs = read_json(CRONTAB_FILE, [])
    if not isinstance(jobs, list):
        return []
    return [j for j in jobs if isinstance(j, dict) and j.get("name") and j.get("every_sec")]


def run_cron_job(job: dict):
    name = job.get("name")
    cmd = job.get("cmd")
    if cmd:
        log(f"cron: running user job '{name}': {cmd}")
        try:
            subprocess.run(cmd, shell=True, cwd=ROOT, timeout=300,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:  # noqa
            log(f"cron job '{name}' error: {e}")


def builtin_jobs():
    return [
        {"name": "launch_check", "every_sec": 60, "fn": lambda: _ensure_stack()},
        {"name": "memory_maintain", "every_sec": 1800,
         "fn": lambda: _memory_maintain()},
        {"name": "selfdev", "every_sec": 3600,
         "fn": lambda: _selfdev_cycle()},
    ]


def _ensure_stack():
    # Delegate to launch.sh so behavior stays consistent with manual launches.
    try:
        subprocess.run(["bash", str(LAUNCH_SH)], cwd=ROOT, timeout=30,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _memory_maintain():
    # Light self-check: ask the local memory store for its status. If the store
    # is healthy this keeps the bundle "touched"; failures are logged only.
    try:
        import asyncio
        from memory import store as ms
        s = ms.memory_status()
        if s:
            log(f"memory_maintain: {s.get('concepts')} concepts, "
                f"orphans={s.get('graph', {}).get('orphans')}")
    except Exception as e:  # noqa
        log(f"memory_maintain error: {e}")


def _selfdev_cycle():
    # Continuous self-improvement: research + fact-check + dream (the "Dreamer").
    try:
        import asyncio
        from autonomous import selfdev
        res = asyncio.run(selfdev.run_selfdev_cycle())
        r = res.get("research", {})
        log(f"selfdev: researched '{r.get('topic')}' -> stored '{r.get('stored')}'; "
            f"dream trimmed {res.get('dream', {}).get('trimmed')}")
    except Exception as e:  # noqa
        log(f"selfdev error: {e}")


def heartbeat(base: str):
    h = server_health(base)
    state = {
        "ok": h.get("ok", False),
        "at": now_iso(),
        "server": base,
        "ollama": h.get("ollama"),
        "tts": h.get("tts"),
        "stt": h.get("stt"),
        "error": h.get("error"),
    }
    prev = read_json(HEARTBEAT_FILE, {})
    history = prev.get("history", []) if isinstance(prev, dict) else []
    history.append({"at": state["at"], "ok": state["ok"]})
    history = history[-50:]  # keep last 50
    write_json(HEARTBEAT_FILE, {
        **state,
        "history": history,
        "last_down_at": prev.get("last_down_at") if isinstance(prev, dict) else None,
    })
    if not state["ok"]:
        log(f"heartbeat: server NOT healthy ({state.get('error') or 'unknown'})")
    else:
        log("heartbeat: server healthy")
    return state


def main():
    base = os.environ.get("VG_BASE", "http://localhost:8500")
    log(f"VirusGPT gateway starting (heartbeat every {HEARTBEAT_SEC}s, base={base})")
    log(f"heartbeat file: {HEARTBEAT_FILE}")

    last_down = 0.0
    # per-job last-run timestamps
    runs = {}
    user_jobs = load_crontab()
    builtins = builtin_jobs()
    all_jobs = builtins + [{"name": j["name"], "every_sec": j["every_sec"], "user": j}
                           for j in user_jobs]

    last_hb = 0.0
    try:
        while True:
            t = time.time()
            # Heartbeat
            if t - last_hb >= HEARTBEAT_SEC:
                last_hb = t
                st = heartbeat(base)
                if not st["ok"]:
                    if last_down == 0:
                        last_down = t
                    elif t - last_down >= REVIVE_AFTER_SEC:
                        revive_stack()
                        last_down = 0
                else:
                    last_down = 0

            # Cron scheduler
            for job in all_jobs:
                every = job["every_sec"]
                if t - runs.get(job["name"], 0) >= every:
                    runs[job["name"]] = t
                    if "fn" in job:
                        try:
                            job["fn"]()
                        except Exception as e:  # noqa
                            log(f"builtin job {job['name']} error: {e}")
                    elif "user" in job:
                        run_cron_job(job["user"])

            # Reload user crontab periodically (cheap)
            if int(t) % 10 == 0:
                uj = load_crontab()
                # merge new user jobs (keep builtins)
                seen = {j["name"] for j in all_jobs if "user" in j}
                for j in uj:
                    if j["name"] not in seen:
                        all_jobs.append({"name": j["name"], "every_sec": j["every_sec"], "user": j})
                        seen.add(j["name"])

            time.sleep(2)
    except KeyboardInterrupt:
        log("gateway stopped")
        sys.exit(0)


if __name__ == "__main__":
    main()
