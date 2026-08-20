#!/usr/bin/env python3
"""Hermes <-> VirusGPT autonomous-engine bridge (stdlib only, Python 3.8+).

This is the *built* artifact that `docs/SWARM_INTEGRATION.md` describes. It is a
dependency-free CLI that drives VirusGPT's existing autonomous engine over HTTP
(`POST /api/autonomous/start` and friends, served by `server.py` on :8500 by
default), implementing the two integration patterns from the doc:

  * Pattern A — VirusGPT as one *worker* inside a larger Hermes swarm. Use the
    `status`/`stream`/`run` subcommands from your `virusgpt` worker card body.
  * Pattern B — Hermes is only the trigger + monitor. Use `run` (block until the
    mission is terminal, then print `final_result`).

No third-party packages are used, so it runs anywhere Python 3 ships. All state
lives in VirusGPT's own SQLite `missions`/`tasks`/`events` tables — this script
never persists anything itself.

Endpoints used (verified against server.py):
  POST /api/autonomous/start   {goal, room_personas?} -> {ok, mission_id, status, stream_url}
  GET  /api/autonomous/status/{id}  -> snapshot {status, final_result, tasks[]}
  GET  /api/autonomous/stream/{id}  -> SSE (event-stream)
  POST /api/autonomous/stop/{id}    -> {ok}
  POST /api/autonomous/resume/{id}  -> {ok}
  GET  /api/health                 -> {ollama, tts, whisper, models[], default_model}
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

DEFAULT_BASE = "http://localhost:8500"
TERMINAL = {"completed", "failed", "blocked", "cancelled"}
HTTP_TIMEOUT = 30


def _request(method: str, url: str, body: Optional[dict] = None) -> Any:
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "replace")
            code = resp.getcode()
    except urllib.error.HTTPError as exc:  # server returned an error status
        raw = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"HTTP {exc.code} from {url}: {raw}")
    except urllib.error.URLError as exc:
        raise SystemExit(f"cannot reach {url}: {exc.reason}")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise SystemExit(f"non-JSON response ({code}) from {url}: {raw}")


def _get(url: str) -> Any:
    return _request("GET", url)


def _post(url: str, body: Optional[dict] = None) -> Any:
    return _request("POST", url, body)


# ---------------------------------------------------------------------------
# Subcommand implementations
# ---------------------------------------------------------------------------
def cmd_start(base: str, args: argparse.Namespace) -> int:
    personas = None
    if args.personas:
        personas = json.loads(args.personas)
    elif args.room:
        # read a room JSON file of the shape [{name, role, ...}, ...]
        with open(args.room, "r", encoding="utf-8") as fh:
            personas = json.load(fh)
    payload: Dict[str, Any] = {"goal": args.goal}
    if personas is not None:
        payload["room_personas"] = personas
    out = _post(f"{base}/api/autonomous/start", payload)
    if not out.get("ok"):
        raise SystemExit(f"start failed: {out}")
    if args.quiet:
        # machine-readable: print ONLY the mission id
        print(out["mission_id"])
    else:
        print(json.dumps(out, indent=2))
    return 0


def cmd_status(base: str, args: argparse.Namespace) -> int:
    snap = _get(f"{base}/api/autonomous/status/{args.mission_id}")
    print(json.dumps(snap, indent=2))
    return 0


def cmd_stream(base: str, args: argparse.Namespace) -> int:
    url = f"{base}/api/autonomous/stream/{args.mission_id}"
    req = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").rstrip("\n")
            if not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if args.quiet and obj.get("event") != "end":
                # quiet mode: only print the terminal 'end' event
                continue
            print(json.dumps(obj, indent=2))
            if obj.get("event") == "end":
                break
    return 0


def cmd_stop(base: str, args: argparse.Namespace) -> int:
    print(json.dumps(_post(f"{base}/api/autonomous/stop/{args.mission_id}", {})))
    return 0


def cmd_resume(base: str, args: argparse.Namespace) -> int:
    print(json.dumps(_post(f"{base}/api/autonomous/resume/{args.mission_id}", {})))
    return 0


def cmd_health(base: str, _args: argparse.Namespace) -> int:
    h = _get(f"{base}/api/health")
    print(json.dumps(h, indent=2))
    # NOTE: /api/health always returns 200 and `models` falls back to
    # [default_model] when Ollama is down, so a non-empty `models` list is NOT
    # proof of connectivity. The real signal is the `ollama` boolean.
    if not h.get("ollama"):
        raise SystemExit("health check: ollama not reachable; aborting start")
    return 0


def cmd_run(base: str, args: argparse.Namespace) -> int:
    """Pattern B: start a mission, block until terminal, print final_result."""
    # 1. readiness check
    if not args.skip_health:
        h = _get(f"{base}/api/health")
        # `models` always falls back to [default_model] when Ollama is down, so
        # gate on the `ollama` boolean, not on a non-empty `models` list.
        if not h.get("ollama"):
            raise SystemExit("health check: ollama not reachable; aborting run")

    # 2. start
    start = _post(f"{base}/api/autonomous/start", {"goal": args.goal})
    if not start.get("ok"):
        raise SystemExit(f"start failed: {start}")
    mid = start["mission_id"]
    sys.stderr.write(f"[hermes_bridge] mission {mid} -> {start.get('status')}\n")

    # 3. poll until terminal
    last_status = None
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        snap = _get(f"{base}/api/autonomous/status/{mid}")
        st = snap.get("status")
        if st != last_status:
            sys.stderr.write(f"[hermes_bridge] {mid}: {st}\n")
            last_status = st
        if st in TERMINAL:
            final = snap.get("final_result")
            if args.quiet:
                print(final if final is not None else "")
            else:
                print(json.dumps({"mission_id": mid, "status": st,
                                  "final_result": final}, indent=2))
            return 0
        time.sleep(args.interval)

    # timed out without reaching a terminal state
    raise SystemExit(f"timed out after {args.timeout}s waiting for {mid}")


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hermes_bridge",
        description="Drive VirusGPT's autonomous engine from Hermes/any HTTP client.",
    )
    p.add_argument("--base", default=DEFAULT_BASE,
                   help=f"VirusGPT base URL (default {DEFAULT_BASE})")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("start", help="POST a goal; print {mission_id, ...}")
    s.add_argument("goal")
    s.add_argument("--personas", help="JSON array of room personas")
    s.add_argument("--room", help="path to a JSON file of room personas")
    s.add_argument("--quiet", action="store_true", help="print only mission_id")
    s.set_defaults(func=cmd_start)

    s = sub.add_parser("status", help="one-shot snapshot of a mission")
    s.add_argument("mission_id")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("stream", help="follow the live SSE feed")
    s.add_argument("mission_id")
    s.add_argument("--quiet", action="store_true", help="print only the end event")
    s.set_defaults(func=cmd_stream)

    s = sub.add_parser("stop", help="cooperative cancel")
    s.add_argument("mission_id")
    s.set_defaults(func=cmd_stop)

    s = sub.add_parser("resume", help="re-drive an interrupted mission")
    s.add_argument("mission_id")
    s.set_defaults(func=cmd_resume)

    s = sub.add_parser("health", help="print GET /api/health")
    s.set_defaults(func=cmd_health)

    s = sub.add_parser("run", help="Pattern B: start + block + print final_result")
    s.add_argument("goal")
    s.add_argument("--timeout", type=int, default=1800,
                   help="max seconds to wait (default 1800)")
    s.add_argument("--interval", type=int, default=3,
                   help="poll interval seconds (default 3)")
    s.add_argument("--skip-health", action="store_true",
                   help="skip the /api/health models check")
    s.add_argument("--quiet", action="store_true",
                   help="print only final_result text")
    s.set_defaults(func=cmd_run)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args.base, args)


if __name__ == "__main__":
    raise SystemExit(main())
