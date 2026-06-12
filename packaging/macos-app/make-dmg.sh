#!/usr/bin/env bash
# Package dist/Cairn.app into a distributable .dmg — NO Apple Developer account.
#
# The app is ad-hoc signed (required for Apple Silicon to run at all), not
# notarized. That's fine for a handful of trusted machines: the only cost is
# that each recipient clears Gatekeeper once on first launch (see the README →
# "Install without a Developer account"). For public distribution, use
# notarize.sh instead (needs the Developer ID).
#
# Run from the repo root after build-app.sh:
#     ./packaging/macos-app/make-dmg.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

APP="dist/Cairn.app"
DMG="dist/Cairn.dmg"
[ -d "$APP" ] || { echo "no $APP — run ./packaging/macos-app/build-app.sh first" >&2; exit 1; }

echo "==> Ensuring the app is ad-hoc signed (Apple Silicon won't launch it otherwise)"
codesign --force --deep --sign - "$APP"

echo "==> Building $DMG"
rm -f "$DMG"
# Stage with an /Applications symlink so the mounted window is the familiar
# drag-to-install layout.
STAGE="$(mktemp -d)"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "Cairn" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
rm -rf "$STAGE"

echo
echo "Built $DMG ($(du -h "$DMG" | cut -f1))."
echo "Recipients: open the .dmg, drag Cairn.app to Applications, then clear"
echo "Gatekeeper once — see packaging/macos-app/README.md."
