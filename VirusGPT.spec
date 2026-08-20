# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['desktop/run.py'],
    pathex=[],
    binaries=[],
    datas=[('app', 'app'), ('config.json', 'config.json'), ('server.py', 'server.py'), ('desktop', 'desktop')],
    hiddenimports=['webview', 'server', 'uvicorn', 'fastapi', 'starlette', 'services', 'autonomous', 'memory', 'gateway'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='VirusGPT',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['/Users/Master/virusgpt-mac/desktop/VirusGPT.icns'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='VirusGPT',
)
app = BUNDLE(
    coll,
    name='VirusGPT.app',
    icon='/Users/Master/virusgpt-mac/desktop/VirusGPT.icns',
    bundle_identifier=None,
)
