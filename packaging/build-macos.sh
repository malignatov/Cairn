#!/usr/bin/env bash
# Build the standalone arm64 Cairn binary for macOS.
#
# Produces dist/cairn — a self-contained stdio MCP server that Claude Desktop
# spawns directly. No Python, no Docker, no Colima needed on the machine that
# runs it. Run on an Apple Silicon Mac from anywhere:
#
#     ./packaging/build-macos.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VENV=".build-venv"

# Cairn needs Python >=3.12; macOS ships /usr/bin/python3 as 3.9, so pick a
# suitable interpreter explicitly rather than trusting bare `python3`.
PYTHON=""
for cand in python3.13 python3.12 python3; do
  if command -v "$cand" >/dev/null 2>&1 \
     && "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,12) else 1)'; then
    PYTHON="$cand"
    break
  fi
done
[ -n "$PYTHON" ] || { echo "error: need Python >=3.12 (try: brew install python@3.13)" >&2; exit 1; }
echo "==> Using $("$PYTHON" --version) ($(command -v "$PYTHON"))"

echo "==> Preparing build venv ($VENV)"
if [ ! -d "$VENV" ]; then
  "$PYTHON" -m venv "$VENV"
fi
"$VENV/bin/pip" install --quiet --upgrade pip
# The app (for its runtime deps) plus the freezer itself.
"$VENV/bin/pip" install --quiet . pyinstaller

echo "==> Freezing with PyInstaller"
"$VENV/bin/pyinstaller" --noconfirm --clean packaging/cairn.spec

echo "==> Ad-hoc code-signing (lets Gatekeeper run the spawned binary)"
codesign --force --sign - dist/cairn

echo
echo "Built dist/cairn ($(du -h dist/cairn | awk '{print $1}'))."
echo "Verify it speaks MCP:        ./packaging/smoke-test.sh"
echo "Install for Claude Desktop:  see packaging/README.md"
