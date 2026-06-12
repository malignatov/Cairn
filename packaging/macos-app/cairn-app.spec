# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec — Cairn menu-bar .app (macOS, arm64).
#
# Builds dist/Cairn.app: a menu-bar app (no dock icon, LSUIElement) that runs
# Cairn as an HTTP MCP server. This is the "remote MCP server" packaging — your
# friend with the Apple Developer account signs + notarizes the result (see
# notarize.sh). Content is baked in; the DB lives in
# ~/Library/Application Support/Cairn/, same as the CLI build.
#
# Build:   ./packaging/macos-app/build-app.sh
# Test:    dist/Cairn.app/Contents/MacOS/Cairn --serve-only   (headless server)

import os
from PyInstaller.utils.hooks import collect_all, collect_submodules, collect_data_files

ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir, os.pardir))  # repo root

datas = [
    (os.path.join(ROOT, "skills"), "skills"),
    (os.path.join(ROOT, "schemas"), "schemas"),
    (os.path.join(ROOT, "guides"), "guides"),
    (os.path.join(ROOT, "constitution.md"), "."),
]
binaries = []
hiddenimports = ["meta_assistant.macos_app", "meta_assistant.remote"]

# pydantic core + rumps (the menu-bar UI, which pulls pyobjc) do dynamic
# imports; collect_all sweeps submodules, data, and compiled extensions.
for pkg in ("pydantic", "pydantic_core", "rumps"):
    pkg_datas, pkg_bins, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_bins
    hiddenimports += pkg_hidden

# mcp server/shared subtrees (transports import lazily); never collect_all('mcp')
# — mcp.cli sys.exit()s at import when the optional `typer` dep is absent.
hiddenimports += collect_submodules("mcp.server")
hiddenimports += collect_submodules("mcp.shared")
datas += collect_data_files("mcp")

# uvicorn dynamically imports its loop/protocol implementations.
hiddenimports += collect_submodules("uvicorn")

# pyobjc frameworks rumps sits on.
hiddenimports += ["Foundation", "AppKit", "objc", "PyObjCTools", "PyObjCTools.AppHelper"]

a = Analysis(
    [os.path.join(ROOT, "packaging", "macos-app", "cairn_app_entry.py")],
    pathex=[os.path.join(ROOT, "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

# Windowed (GUI) executable, collected into a .app bundle.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Cairn",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # GUI/menu-bar app, not a terminal program
    target_arch="arm64",
    codesign_identity=None,  # signing happens in notarize.sh (your friend)
    entitlements_file=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Cairn")

app = BUNDLE(
    coll,
    name="Cairn.app",
    icon=None,
    bundle_identifier="com.cairn.app",  # your friend will likely rebrand this
    info_plist={
        "CFBundleName": "Cairn",
        "CFBundleDisplayName": "Cairn",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "0.1.0",
        "LSUIElement": True,            # menu-bar agent: no Dock icon
        "LSMinimumSystemVersion": "13.0",
        "NSHighResolutionCapable": True,
        "LSApplicationCategoryType": "public.app-category.productivity",
    },
)
