"""Patch the installed pywebview (6.2.x) for a dark, frameless, non-flashing
macOS desktop window.

VirusGPT's desktop app is a native WKWebView. Out of the box pywebview:
  • leaves the WKWebView opaque white until the page paints  -> white flash
  • forces the titlebar to the system (light) windowBackgroundColor -> white strip

This script makes the patch reproducible after a fresh `pip install` / venv
recreate, so the fix survives without manual edits. Run it from the install
flow (see install.sh / setup) or directly:

    python desktop/patch_pywebview.py

It is idempotent: already-patched installs are left untouched.

Platform: macOS only (touches platforms/cocoa.py). On other platforms it is a
no-op.
"""
from __future__ import annotations

import importlib.util
import os
import sys

WEBVIEW_MIN = (6, 0, 0)


def _cocoa_path() -> str | None:
    spec = importlib.util.find_spec("webview")
    if not spec or not spec.origin:
        return None
    pkg_dir = os.path.dirname(spec.origin)
    p = os.path.join(pkg_dir, "platforms", "cocoa.py")
    return p if os.path.exists(p) else None


def _patch(src: str) -> str | None:
    """Return patched source, or None if no change was needed/applied."""
    s = src

    # 1) Non-transparent mode: make the WKWebView layer transparent so the dark
    #    window background shows through until the page paints (no white flash).
    anchor1 = (
        "        else:\n"
        "            self.window.setBackgroundColor_(BrowserView.nscolor_from_hex(window.background_color))\n"
    )
    repl1 = (
        "        else:\n"
        "            self.window.setBackgroundColor_(BrowserView.nscolor_from_hex(window.background_color))\n"
        "            # Make the WKWebView transparent so the dark window background shows\n"
        "            # through until the page paints (prevents a white flash on load).\n"
        "            # BOTH setOpaque_(False) AND drawsTransparentBackground are required;\n"
        "            # without setOpaque_(False) the WKWebView stays opaque white.\n"
        "            try:\n"
        "                self.window.setOpaque_(False)\n"
        "                self.webview.setValue_forKey_(True, 'drawsTransparentBackground')\n"
        "            except Exception:\n"
        "                pass\n"
    )
    if anchor1 in s and "drawsTransparentBackground" not in s.split(anchor1, 1)[1].split("window.vibrancy", 1)[0]:
        s = s.replace(anchor1, repl1, 1)

    # 2) Titlebar: make it transparent + dark instead of the light system color.
    anchor2 = (
        "        else:\n"
        "            # Set the titlebar color (so that it does not change with the window color)\n"
        "            self.window.contentView().superview().subviews().lastObject().setBackgroundColor_(\n"
        "                AppKit.NSColor.windowBackgroundColor()\n"
        "            )\n"
    )
    repl2 = (
        "        else:\n"
        "            # Make the titlebar transparent + dark so there is no light/white\n"
        "            # strip on first paint (the default uses windowBackgroundColor).\n"
        "            try:\n"
        "                self.window.setTitlebarAppearsTransparent_(True)\n"
        "                self.window.contentView().superview().subviews().lastObject().setBackgroundColor_(\n"
        "                    BrowserView.nscolor_from_hex(window.background_color)\n"
        "                )\n"
        "            except Exception:\n"
        "                pass\n"
    )
    if anchor2 in s:
        s = s.replace(anchor2, repl2, 1)

    return s if s != src else None


def main() -> int:
    if sys.platform != "darwin":
        print("[patch_pywebview] not macOS — skipping (no-op).")
        return 0

    path = _cocoa_path()
    if not path:
        print("[patch_pywebview] webview not installed or no cocoa.py — skipping.")
        return 0

    with open(path, "r", encoding="utf-8") as f:
        src = f.read()

    patched = _patch(src)
    if patched is None:
        print(f"[patch_pywebview] already patched (or nothing to do): {path}")
        return 0

    with open(path, "w", encoding="utf-8") as f:
        f.write(patched)
    print(f"[patch_pywebview] patched: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
