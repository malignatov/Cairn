# Cairn as a macOS menu-bar app (remote / HTTP MCP server)

This branch packages Cairn as **`Cairn.app`** — a menu-bar app (no Dock icon)
that runs Cairn as an **HTTP MCP server**, so clients connect by *URL* rather
than by spawning a local process. It's the "remote MCP server" form, and it
deliberately steps outside Cairn's local-only, no-auth design — which is why it
lives on a branch.

> **Two ways to ship it:** for a few trusted machines, `make-dmg.sh` packages an
> ad-hoc-signed `.dmg` with **no Apple account** (recipients clear Gatekeeper
> once). For public distribution, whoever holds the Apple Developer ID runs
> `notarize.sh` to sign + notarize the `.dmg` so it opens with a clean
> double-click.

```
build-app.sh        → dist/Cairn.app                          (you, on any Apple-Silicon Mac)
make-dmg.sh         → dist/Cairn.dmg  (ad-hoc, NO Apple account)  (you)
notarize.sh         → signed + notarized dist/Cairn.dmg          (the Developer-ID holder)
cairn-app.spec      PyInstaller recipe (menu-bar, LSUIElement, arm64)
cairn_app_entry.py  frozen entry shim
entitlements.plist  hardened-runtime entitlements for notarization
```

## Build & test

```sh
./packaging/macos-app/build-app.sh
# headless smoke test of the bundled server (no menu bar needed):
dist/Cairn.app/Contents/MacOS/Cairn --serve-only
```

Launched normally (double-click), it shows a **● Cairn** menu-bar item with the
server URL, a copy action, start/stop, and quit. Serves `http://localhost:8000/mcp`
by default.

## Connecting a client — and the auth truth

The default is **localhost**, which is the safe, common case: add it as a custom
HTTP MCP connector in a desktop client running on the **same Mac**. Nothing
leaves the machine; no auth needed.

Reaching it from elsewhere is where it gets real. The verified landscape (2026):

| Client | How it connects | Auth it accepts |
|---|---|---|
| **ChatGPT** (desktop/web) | remote HTTPS connector, `/mcp` | **OAuth 2.1 or none** — *cannot* send a token/header |
| **claude.ai / Claude Desktop** (custom connector) | remote HTTPS connector | **OAuth or none** — UI has no token/header field |
| **Claude Messages API** | `authorization_token` in the call | static bearer token ✅ |
| **Claude Code / curl / MCP Inspector** | you set the header | static bearer token ✅ |

So:

- **There is no "login + password."** Hosted apps (ChatGPT, claude.ai) only do
  OAuth or no-auth. A real OAuth server is a large build and against Cairn's
  grain — out of scope here.
- **The optional bearer token** (below) protects the endpoint against random
  scanners on a tunnel and works for the *header-capable* clients in the table.
  It will **not** let ChatGPT or claude.ai-web in — those need OAuth.
- **Remote exposure needs a public HTTPS endpoint** (a tunnel — `cloudflared
  tunnel --url http://localhost:8000`, or ngrok). The app serves HTTP; it can't
  make itself public.

### Enabling the optional token

The server requires `Authorization: Bearer <token>` when `META_AUTH_TOKEN` is
set, and is open when it isn't. To run the app with auth on (e.g. an always-on
deployment), set it in the environment — most cleanly via a LaunchAgent plist's
`EnvironmentVariables`, or for a quick test:

```sh
META_AUTH_TOKEN="$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')" \
  dist/Cairn.app/Contents/MacOS/Cairn --serve-only
```

(An in-menu "generate & require token" toggle is the obvious next enhancement;
the env-var path is what's wired and tested today.)

## Distribute without a Developer account *(recommended for a few machines)*

You don't need the Apple account at all. `Cairn.app` is **ad-hoc signed** —
which is all Apple Silicon needs to *run* a binary. Skip `notarize.sh` and just
make a disk image:

```sh
./packaging/macos-app/build-app.sh      # → dist/Cairn.app
./packaging/macos-app/make-dmg.sh       # → dist/Cairn.dmg  (no account)
```

Send `Cairn.dmg`. The *only* cost of skipping notarization: macOS quarantines a
downloaded/AirDropped copy, so Gatekeeper blocks the first launch. Each recipient
clears it **once**:

1. Open the `.dmg`, drag **Cairn.app** to **Applications**.
2. Clear Gatekeeper (either works):
   - **Terminal — most reliable, every macOS version:**
     ```sh
     xattr -dr com.apple.quarantine /Applications/Cairn.app
     ```
   - **GUI — macOS 15 (Sequoia) / 26+:** launch it once (it'll be blocked), then
     System Settings → **Privacy & Security** → scroll to Security →
     **Open Anyway** → authenticate. (The old right-click → Open route was
     removed in Sequoia.)

Because it's a **menu-bar app with no window**, prefer the `xattr` step *before*
first launch — a blocked launch otherwise just silently does nothing and looks
broken. After that one step it runs forever; quarantine isn't re-applied. A copy
you build and keep on your *own* machine isn't quarantined at all, so it just runs.

This is right for you and a handful of trusted people. It is **not** for public
distribution — you shouldn't tell strangers to disable Gatekeeper checks. For
that, notarize:

## Signing & notarization (the Developer-ID holder)

1. Build the app: `./packaging/macos-app/build-app.sh`.
2. Open `notarize.sh`, fill in the three TODOs (Developer ID identity, notarytool
   keychain profile, bundle id).
3. Run `./packaging/macos-app/notarize.sh`. It signs nested code inner-out with
   the hardened runtime + `entitlements.plist`, submits to notarytool, staples,
   and builds `dist/Cairn.dmg`.

The entitlements relax library-validation and executable-memory rules — a
PyInstaller/Python app won't launch under the hardened runtime without them.
The App Sandbox is intentionally **off** (a sandboxed app can't bind a server
socket without extra entitlements, and notarization doesn't require the sandbox).

## Security — read before exposing it

Cairn holds one person's whole strategic record and has no real authn/authz. A
tunnel turns it into a public endpoint; the optional token is a coarse gate, not
real protection, and the hosted apps can't even use it. **Don't stand up a
permanent public endpoint with your real data.** For a one-off, expose briefly
against a throwaway DB (`META_DB_PATH=/tmp/cairn-test.db`) and tear the tunnel
down. For daily use, prefer the local stdio build with Claude Desktop
(`docs/macos-install.md`) — fully local, no exposure.

## What's verified vs. what needs a real desktop

Verified headless here: the bundle builds; the embedded server runs; baked-in
content (constitution/skills) resolves; the DB defaults to Application Support;
and the bearer-token gate returns 401/200 correctly. **Not** verifiable without
a GUI login session: the menu-bar rendering and click behaviour, and Gatekeeper
acceptance after notarization — verify those on a real Mac.
