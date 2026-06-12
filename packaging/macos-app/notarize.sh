#!/usr/bin/env bash
# Sign + notarize + staple dist/Cairn.app, then package a .dmg.
#
# RUN BY: whoever holds the Apple Developer account. Fill in the three TODOs
# below, then run from the repo root after ./build-app.sh has produced
# dist/Cairn.app.
#
# Prereqs (one-time, on the signer's Mac):
#   - Xcode command-line tools.
#   - A "Developer ID Application" certificate in the login keychain.
#   - A notarytool keychain profile holding an App Store Connect API key or an
#     app-specific password:
#       xcrun notarytool store-credentials AC_PROFILE \
#         --apple-id "you@example.com" --team-id "TEAMID" --password "app-specific-pw"
set -euo pipefail

# ──────────────────────────────────────────────────────────────────────────
# TODO (signer): fill these three in.
DEV_ID_APP="Developer ID Application: YOUR NAME (TEAMID)"   # codesign identity
NOTARY_PROFILE="AC_PROFILE"                                  # notarytool keychain profile
BUNDLE_ID="com.cairn.app"                                    # match cairn-app.spec
# ──────────────────────────────────────────────────────────────────────────

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

APP="dist/Cairn.app"
ENTITLEMENTS="packaging/macos-app/entitlements.plist"
DMG="dist/Cairn.dmg"

[ -d "$APP" ] || { echo "no $APP — run ./packaging/macos-app/build-app.sh first" >&2; exit 1; }

echo "==> Signing nested code, then the app (hardened runtime)"
# Sign inner-out: every nested dylib/.so/framework first, then the bundle.
find "$APP/Contents" \( -name "*.dylib" -o -name "*.so" \) -print0 \
  | xargs -0 -I{} codesign --force --timestamp --options runtime \
      --entitlements "$ENTITLEMENTS" --sign "$DEV_ID_APP" {}
codesign --force --timestamp --options runtime \
  --entitlements "$ENTITLEMENTS" --sign "$DEV_ID_APP" "$APP"

echo "==> Verifying signature"
codesign --verify --deep --strict --verbose=2 "$APP"

echo "==> Notarizing (submitting a zip, waiting for the result)"
ZIP="dist/Cairn-notarize.zip"
ditto -c -k --keepParent "$APP" "$ZIP"
xcrun notarytool submit "$ZIP" --keychain-profile "$NOTARY_PROFILE" --wait
rm -f "$ZIP"

echo "==> Stapling the ticket"
xcrun stapler staple "$APP"
xcrun stapler validate "$APP"

echo "==> Building $DMG for distribution"
rm -f "$DMG"
hdiutil create -volname "Cairn" -srcfolder "$APP" -ov -format UDZO "$DMG"

echo
echo "Done. Ship $DMG. Recipients drag Cairn.app to /Applications and launch it"
echo "— Gatekeeper passes because it's signed + notarized + stapled."
