# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec — standalone macOS (arm64) Cairn binary.
#
# Build:   ./packaging/build-macos.sh          (preferred; provisions a venv)
#   or:    pyinstaller packaging/cairn.spec     (if PyInstaller is on PATH)
# Output:  dist/cairn — a self-contained stdio MCP server. No Python, no
#          Docker, no Colima on the machine that runs it. Skills, schemas,
#          guides, and the constitution are baked in (frozen content; editing
#          them means a rebuild). The SQLite DB is created on first run under
#          ~/Library/Application Support/Cairn/.

import os
from PyInstaller.utils.hooks import collect_all, collect_submodules, collect_data_files

# SPECPATH is injected by PyInstaller; anchor to the repo root so the build
# works regardless of the directory pyinstaller is invoked from.
ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))

# Bake the markdown content into the bundle. server.py resolves these via
# sys._MEIPASS when frozen (see _content_default there). Dest "." for the
# single file; the directory name for the dirs.
datas = [
    (os.path.join(ROOT, "skills"), "skills"),
    (os.path.join(ROOT, "schemas"), "schemas"),
    (os.path.join(ROOT, "guides"), "guides"),
    (os.path.join(ROOT, "constitution.md"), "."),
]
binaries = []
hiddenimports = []

# pydantic v2 and its compiled Rust core do dynamic imports PyInstaller's
# static analysis misses; collect_all sweeps submodules, data, and the .so.
for pkg in ("pydantic", "pydantic_core"):
    pkg_datas, pkg_bins, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_bins
    hiddenimports += pkg_hidden

# mcp: collect only the server + shared subtrees — the stdio (and http)
# transports are imported lazily inside FastMCP.run, so static analysis can
# miss them. Deliberately NOT collect_all("mcp"): that walks mcp.cli, which
# does sys.exit(1) at import time when the optional `typer` dep is absent,
# killing the build. We never use the CLI, so skip it.
hiddenimports += collect_submodules("mcp.server")
hiddenimports += collect_submodules("mcp.shared")
datas += collect_data_files("mcp")

a = Analysis(
    [os.path.join(ROOT, "packaging", "cairn_entry.py")],
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

# onefile: binaries + datas folded into a single executable.
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="cairn",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,          # stdio CLI spawned by Claude Desktop, not a GUI app
    target_arch="arm64",   # Apple Silicon only, per project decision
    codesign_identity=None,
    entitlements_file=None,
)
