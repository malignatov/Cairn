#!/usr/bin/env bash
# Build the unsigned Cairn menu-bar app → dist/Cairn.app
#
# This produces the *unsigned* bundle. Signing + notarization is a separate
# step (notarize.sh), run by whoever holds the Apple Developer ID.
#
# Run on an Apple Silicon Mac from anywhere:
#     ./packaging/macos-app/build-app.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

VENV=".build-venv-app"

# Cairn needs Python >=3.12; macOS ships 3.9, so pick a real interpreter.
PYTHON=""
for cand in python3.13 python3.12 python3; do
  if command -v "$cand" >/dev/null 2>&1 \
     && "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,12) else 1)'; then
    PYTHON="$cand"; break
  fi
done
[ -n "$PYTHON" ] || { echo "error: need Python >=3.12 (try: brew install python@3.13)" >&2; exit 1; }
echo "==> Using $("$PYTHON" --version)"

echo "==> Preparing build venv ($VENV)"
[ -d "$VENV" ] || "$PYTHON" -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
# App deps: Cairn itself + the menu-bar UI (rumps → pyobjc) + server + freezer.
"$VENV/bin/pip" install --quiet . rumps uvicorn pyinstaller

echo "==> Freezing Cairn.app with PyInstaller"
"$VENV/bin/pyinstaller" --noconfirm --clean packaging/macos-app/cairn-app.spec

echo
echo "Built dist/Cairn.app"
echo "Headless smoke-test (server only, no menu bar):"
echo "    dist/Cairn.app/Contents/MacOS/Cairn --serve-only"
echo "Sign + notarize (needs an Apple Developer ID):"
echo "    ./packaging/macos-app/notarize.sh"
