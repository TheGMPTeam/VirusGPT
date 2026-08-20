#!/usr/bin/env python3
"""Launcher for the VirusGPT desktop app. Boots the server + opens a native window."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if __name__ == "__main__":
    from desktop import app
    app.main()
