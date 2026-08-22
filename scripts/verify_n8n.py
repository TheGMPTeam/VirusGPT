#!/usr/bin/env python3
"""Verify the n8n integration end-to-end.

One-command check used by docs/N8N_INTEGRATION.md. Prints a clear
pass/fail matrix so a broken n8n is diagnosed without guessing.

Run:
    .venv/bin/python scripts/verify_n8n.py
"""
from __future__ import annotations

import os
import sys

# Allow running from repo root without installing the package.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from services import n8n  # noqa: E402


def _mark(ok: bool, label: str, detail: str = "") -> None:
    sign = "[ok]  " if ok else "[FAIL]"
    line = f"{sign} {label}"
    if detail:
        line += f"  {detail}"
    print(line)


def main() -> int:
    base = n8n._base()
    key = n8n._api_key()

    # 1) reachability (unauthenticated)
    import asyncio
    try:
        healthy = asyncio.run(n8n.n8n_health())
    except Exception as exc:  # noqa: BLE001
        healthy = False
        err = str(exc)
    else:
        err = ""
    _mark(healthy, "n8n reachable", f"{base}  /healthz" + (f"  ({err})" if err else " 200"))

    # 2) key present
    _mark(bool(key), "api key present", f"(len {len(key)})" if key else "(empty)")

    if not healthy or not key:
        print("\nConclusion: n8n is down or the key is missing. See docs/N8N_INTEGRATION.md §4.")
        return 1 if not healthy else 0

    # 3) authenticated API call (the real test)
    import httpx
    try:
        r = httpx.get(
            f"{base}/api/v1/workflows",
            headers=n8n._auth_headers(),
            params={"limit": 1},
            timeout=n8n._timeout(),
        )
    except Exception as exc:  # noqa: BLE001
        _mark(False, "authenticated", f"request error: {exc}")
        print("\nConclusion: transport error. See docs/N8N_INTEGRATION.md §4.")
        return 1

    auth_ok = r.status_code == 200
    _mark(auth_ok, "authenticated", f"GET /api/v1/workflows -> {r.status_code}"
            + (f"  {r.text[:120]}" if not auth_ok else ""))

    if not auth_ok:
        print("\nAll auth attempts 401 identically => n8n rejects the key's signature.")
        print("=> Encryption-key / instance mismatch. See docs/N8N_INTEGRATION.md §5.")
        print("   Fix: regenerate API key in n8n (Settings > API) and update VG_N8N_TOKEN,")
        print("   OR make N8N_ENCRYPTION_KEY stable in the n8n container env.")
        return 1

    # 4) list count (optional, proves workflows endpoint works)
    try:
        data = r.json().get("data", [])
        _mark(True, "workflows endpoint", f"{len(data)} workflow(s) visible")
    except Exception:
        pass

    print("\nConclusion: n8n integration OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
