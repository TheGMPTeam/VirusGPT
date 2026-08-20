#!/usr/bin/env python3
"""
vgctl — VirusGPT command-line control tool.

Subcommands:
  health            Probe the running server's /api/health and print a summary.
  doctor            Run a full diagnostic of the local stack + config.
  fix               Attempt to repair common problems (stale ports, missing
                    venvs, unmet requirements, restart services).
  settings          View / change config.json (get, set, list, reset).

Pure standard library — runs with the system python or the project venv.
Settings written to config.json are picked up on the NEXT server boot.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"

# Ports the stack uses (server, pockettts, whisper, ollama)
PORTS = {"server": 8500, "pockettts": 49152, "whisper": 8181, "ollama": 11434}
DEFAULT_BASE = "http://localhost:8500"


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def cfg() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception as e:  # noqa
        print(f"[WARN] could not read {CONFIG_PATH}: {e}")
        return {}


def write_cfg(d: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(d, indent=2) + "\n")
    print(f"[ok] wrote {CONFIG_PATH}")


def get_path(d: dict, dotted: str):
    cur = d
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def set_path(d: dict, dotted: str, value) -> bool:
    parts = dotted.split(".")
    cur = d
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
        if not isinstance(cur, dict):
            return False
    cur[parts[-1]] = value
    return True


def port_open(host: str, port: int, timeout: float = 1.5) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def http_get_json(url: str, timeout: float = 5.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode()), r.status
    except urllib.error.HTTPError as e:
        return None, e.code
    except Exception as e:  # noqa
        return None, str(e)


def run(cmd, **kw):
    return subprocess.run(cmd, shell=isinstance(cmd, str), text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw)


def green(t): return f"\033[32m{t}\033[0m"
def red(t):   return f"\033[31m{t}\033[0m"
def yel(t):   return f"\033[33m{t}\033[0m"
def bold(t):  return f"\033[1m{t}\033[0m"


# --------------------------------------------------------------------------- #
# health
# --------------------------------------------------------------------------- #
def cmd_health(args):
    base = args.base or cfg().get("host") and f"http://localhost:{cfg().get('port', 8500)}" or DEFAULT_BASE
    url = f"{base.rstrip('/')}/api/health"
    print(f"GET {url}")
    data, status = http_get_json(url)
    if data is None:
        print(red(f"FAIL — server not reachable ({status})"))
        return 1
    print(bold("VirusGPT health"))
    print(f"  ollama : {green('up') if data.get('ollama') else red('down')}")
    print(f"  tts    : {green('up') if data.get('tts') else red('down')}")
    print(f"  whisper: {green('up') if data.get('whisper') else red('down')}")
    print(f"  model  : {data.get('default_model')}")
    print(f"  voices : {', '.join(data.get('voices', [])[:8])}{'…' if len(data.get('voices', [])) > 8 else ''}")
    print(f"  models : {', '.join(data.get('models', [])[:8])}")
    return 0 if (data.get("ollama") and data.get("tts")) else 1


# --------------------------------------------------------------------------- #
# doctor
# --------------------------------------------------------------------------- #
def cmd_doctor(args):
    problems = []
    print(bold("VirusGPT doctor\n"))

    # 1. python
    py = shutil.which("python3") or sys.executable
    print(f"[chk] python3: {sys.version.split()[0]}  ({py})")

    # 2. config file
    if not CONFIG_PATH.exists():
        problems.append(("FAIL", f"config.json missing at {CONFIG_PATH}"))
        print(red("[FAIL] config.json missing"))
    else:
        c = cfg()
        required = ["host", "port", "ollama.base_url", "ollama.default_model",
                    "tts.base_url", "stt.base_url"]
        missing = [k for k in required if get_path(c, k) is None]
        if missing:
            problems.append(("FAIL", f"config.json missing keys: {', '.join(missing)}"))
            print(red(f"[FAIL] config.json missing keys: {', '.join(missing)}"))
        else:
            print(green("[ok] config.json valid, all required keys present"))

    # 3. venvs
    for name, p in [("server", ROOT / ".venv"),
                    ("pockettts", ROOT / "pockettts" / ".venv"),
                    ("whisper", ROOT / "whisper" / ".venv")]:
        if p.exists():
            print(green(f"[ok] {name} venv present"))
        else:
            problems.append(("WARN", f"{name} venv missing ({p})"))
            print(yel(f"[warn] {name} venv missing — run: ./install.sh --{name}"))

    # 4. ports
    c = cfg()
    checks = [("server", PORTS["server"], "localhost"),
              ("pockettts", PORTS["pockettts"], "localhost"),
              ("whisper", PORTS["whisper"], "localhost")]
    # ollama may be remote; only check if host looks local
    oll_url = (c.get("ollama", {}) or {}).get("base_url", "")
    if "localhost" in oll_url or "127.0.0.1" in oll_url:
        checks.append(("ollama", PORTS["ollama"], "localhost"))
    for name, port, host in checks:
        ok = port_open(host, port)
        if ok:
            print(green(f"[ok] {name} listening on :{port}"))
        else:
            problems.append(("WARN", f"{name} not listening on :{port}"))
            print(yel(f"[warn] {name} not listening on :{port}"))

    # 5. live health
    base = f"http://localhost:{c.get('port', 8500)}" if c else DEFAULT_BASE
    data, status = http_get_json(f"{base}/api/health")
    if data is None:
        problems.append(("FAIL", f"server health unreachable at {base}/api/health"))
        print(red(f"[FAIL] server health unreachable at {base}/api/health"))
    else:
        print(green(f"[ok] server health OK") + f"  ollama={data.get('ollama')} tts={data.get('tts')} whisper={data.get('whisper')}")
        if not data.get("ollama"):
            problems.append(("FAIL", "ollama backend down (LLM will not work)"))
        if not data.get("tts"):
            problems.append(("WARN", "tts backend down (voice disabled)"))

    print()
    if not problems:
        print(green(bold("All checks passed.")))
        return 0
    print(bold("Summary:"))
    for sev, msg in problems:
        color = red if sev == "FAIL" else yel
        print(f"  {color(sev)} {msg}")
    fails = [p for p in problems if p[0] == "FAIL"]
    return 1 if fails else 0


# --------------------------------------------------------------------------- #
# fix
# --------------------------------------------------------------------------- #
def _kill_port(port: int) -> bool:
    # macOS / Linux: lsof -tiTCP:PORT -sTCP:LISTEN
    if shutil.which("lsof"):
        r = run(f"lsof -tiTCP:{port} -sTCP:LISTEN")
        pids = [p for p in r.stdout.split() if p.strip()]
        if pids:
            run(f"kill {' '.join(pids)}")
            print(f"  killed stale pids on :{port}: {', '.join(pids)}")
            return True
    # fallback to fuser
    if shutil.which("fuser"):
        run(f"fuser -k {port}/tcp", stderr=subprocess.DEVNULL)
        return True
    return False


def _ensure_venv(path: Path, req: Path, skip_deps: bool = False):
    if not path.exists():
        print(f"  creating venv {path}")
        run([sys.executable, "-m", "venv", str(path)])
    if req.exists() and not skip_deps:
        print(f"  installing {req}")
        run([str(path / 'bin' / 'pip'), "install", "-q", "--upgrade", "pip"])
        run([str(path / 'bin' / 'pip'), "install", "-q", "-r", str(req)])


def cmd_fix(args):
    print(bold("VirusGPT fix\n"))

    # 1. kill stale ports
    if args.kill_ports:
        print("[fix] clearing stale ports")
        for port in [PORTS["server"], PORTS["pockettts"], PORTS["whisper"]]:
            if port_open("localhost", port):
                _kill_port(port)
            else:
                print(f"  :{port} already free")

    # 2. ensure venvs + deps
    if args.reinstall or args.ensure_venv:
        print("[fix] ensuring venvs")
        _ensure_venv(ROOT / ".venv", ROOT / "requirements.txt", args.skip_deps)

    # 3. restart full stack
    if args.restart:
        print("[fix] restarting full stack via launch.sh")
        launch = ROOT / "launch.sh"
        if launch.exists():
            # launch.sh is health-aware and idempotent
            subprocess.Popen(["bash", str(launch)], cwd=str(ROOT),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print("  launched (background). Run: vgctl doctor  to verify.")
        else:
            print(red("  launch.sh not found"))

    if not (args.kill_ports or args.reinstall or args.ensure_venv or args.restart):
        print("Nothing to do. Use flags: --kill-ports --ensure-venv --reinstall --restart")
        return 1
    print(green("fix complete."))
    return 0


# --------------------------------------------------------------------------- #
# settings
# --------------------------------------------------------------------------- #
def _coerce(val: str):
    low = val.lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("null", "none", "nil"):
        return None
    try:
        return int(val)
    except ValueError:
        pass
    try:
        return float(val)
    except ValueError:
        pass
    return val


def cmd_settings(args):
    c = cfg()
    if args.action == "list":
        print(json.dumps(c, indent=2))
        return 0
    if args.action == "get":
        v = get_path(c, args.key)
        if v is None:
            print(red(f"key not found: {args.key}"))
            return 1
        print(json.dumps(v) if isinstance(v, (dict, list)) else v)
        return 0
    if args.action == "set":
        if not set_path(c, args.key, _coerce(args.value)):
            print(red(f"could not set key: {args.key}"))
            return 1
        write_cfg(c)
        print(f"set {args.key} = {args.value}  (applied on next server boot)")
        return 0
    if args.action == "reset":
        # reset a key to its default by removing it (server falls back)
        parts = args.key.split(".")
        cur = c
        for p in parts[:-1]:
            if not isinstance(cur.get(p), dict):
                print(red(f"key not found: {args.key}"))
                return 1
            cur = cur[p]
        cur.pop(parts[-1], None)
        write_cfg(c)
        print(f"reset {args.key} (removed; server uses built-in default)")
        return 0
    print(red("unknown settings action"))
    return 1


# --------------------------------------------------------------------------- #
# entrypoint
# --------------------------------------------------------------------------- #
def build_parser():
    p = argparse.ArgumentParser(prog="vgctl", description="VirusGPT control CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    hp = sub.add_parser("health", help="probe /api/health")
    hp.add_argument("--base", default=None, help="base URL (default from config or localhost:8500)")
    hp.set_defaults(func=cmd_health)

    dp = sub.add_parser("doctor", help="full diagnostic")
    dp.set_defaults(func=cmd_doctor)

    fp = sub.add_parser("fix", help="repair common issues")
    fp.add_argument("--kill-ports", action="store_true", help="kill stale processes holding the stack ports")
    fp.add_argument("--ensure-venv", action="store_true", help="create the server venv if missing")
    fp.add_argument("--reinstall", action="store_true", help="recreate venv + install requirements")
    fp.add_argument("--restart", action="store_true", help="restart full stack via launch.sh")
    fp.add_argument("--skip-deps", action="store_true", help="with --ensure-venv/--reinstall, skip pip installs")
    fp.set_defaults(func=cmd_fix)

    sp = sub.add_parser("settings", help="view/change config.json")
    sp.add_argument("action", choices=["list", "get", "set", "reset"])
    sp.add_argument("key", nargs="?", help="dotted key e.g. ollama.default_model")
    sp.add_argument("value", nargs="?", help="value for 'set' (auto-typed: bool/int/float/str)")
    sp.set_defaults(func=cmd_settings)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
