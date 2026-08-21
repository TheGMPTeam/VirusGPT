# VirusGPT Desktop — Windows dependency installer (PowerShell)
#
# A fresh Windows box may not have `python` on PATH. We prefer the Python
# Launcher (`py`, installed with Python on Windows) and fall back to
# `python` / `python3`. The venv is created with that launcher so the install
# does not depend on a `python` alias being on PATH.
param(
    [string]$Venv = ".venv"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition
# If launched from desktop/, step up to the repo root.
if ((Split-Path $RepoRoot -Leaf) -eq "desktop") { $RepoRoot = Split-Path $RepoRoot -Parent }
Set-Location $RepoRoot

# Resolve a python launcher: prefer `py` (Python Launcher for Windows), then
# `python`, then `python3`.
function Find-Python {
    foreach ($c in @("py", "python", "python3")) {
        try {
            $p = Get-Command $c -ErrorAction SilentlyContinue
            if ($p) { return $p.Source }
        } catch { }
    }
    return $null
}

$Launcher = Find-Python
if (-not $Launcher) {
    Write-Host "[install-windows] ERROR: no Python found. Install Python from https://python.org (tick 'Add to PATH' + 'Install launcher for all users')." -ForegroundColor Red
    exit 1
}
Write-Host "[install-windows] using launcher: $Launcher"

# 1) Create the venv if missing.
if (-not (Test-Path "$Venv\Scripts\python.exe")) {
    Write-Host "[install-windows] creating venv $Venv ..."
    & $Launcher -m venv $Venv
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

$Py = (Resolve-Path "$Venv\Scripts\python.exe").Path

# 2) Install desktop requirements.
Write-Host "[install-windows] installing desktop requirements..."
& $Py -m pip install -r desktop/requirements.txt
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# 3) Re-apply the pywebview patch (no-op on Windows; keeps macOS parity).
Write-Host "[install-windows] applying pywebview patch (no-op on Windows)..."
& $Py desktop/patch_pywebview.py

Write-Host "[install-windows] done. Build with:  python vgctl.py desktop build --platform windows" -ForegroundColor Green
exit 0
