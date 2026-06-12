# Cairn as a standalone macOS binary

The Dockerless path. Instead of Colima + a container + the HTTP bridge,
Cairn ships as a single executable that Claude Desktop spawns directly over
stdio. No Python, no Docker, no port, no always-on server on the machine that
runs it.

Scope (decided deliberately):

- **Apple Silicon only.** arm64. No Intel build.
- **Claude Desktop / Claude Code only.** stdio transport — there's no HTTP
  endpoint, so claude.ai-web and Cowork can't reach it. (A source checkout
  still runs the HTTP server; only the frozen binary defaults to stdio.)
- **Frozen content.** skills/schemas/guides/constitution are baked into the
  binary. Editing them means rebuilding — unlike the bind-mounted container
  where edits were live.
- **Not notarized.** Fine for yourself and a handful of trusted machines;
  not for public download. See "Distributing to others" below.

## Build

On an Apple Silicon Mac, from the repo root:

```sh
./packaging/build-macos.sh      # → dist/cairn  (~40–60 MB)
./packaging/smoke-test.sh       # drives it over stdio with a throwaway DB
```

The build provisions a throwaway `.build-venv/`, installs the app's deps plus
PyInstaller, freezes per `cairn.spec`, and ad-hoc code-signs the result so
Gatekeeper will let Claude Desktop spawn it.

## Install

1. Put the binary somewhere **outside** `~/Documents`, `~/Desktop`, and
   `~/Downloads` — macOS's privacy sandbox blocks GUI-spawned children (which
   is what Claude Desktop's MCP servers are) from reading those folders. The
   repo's established spot:

   ```sh
   mkdir -p ~/.local/bin
   cp dist/cairn ~/.local/bin/cairn
   ```

2. **Migrating existing data?** Copy your DB into place *before* first launch,
   or the binary will create a fresh empty one:

   ```sh
   mkdir -p ~/Library/Application\ Support/Cairn
   cp data/meta.db ~/Library/Application\ Support/Cairn/meta.db
   ```

   Starting fresh? Skip this — the binary creates the DB (and seeds the 16
   life units) on first run.

3. Add it to `~/Library/Application Support/Claude/claude_desktop_config.json`
   (replace `<you>` with your username; add `cairn` as a sibling if you
   already have other servers — don't nest):

   ```json
   {
     "mcpServers": {
       "cairn": {
         "command": "/Users/<you>/.local/bin/cairn",
         "args": ["--stdio"]
       }
     }
   }
   ```

4. Fully quit Claude Desktop (Cmd-Q) and relaunch. The Cairn tools
   (`state_query`, `state_draft`, …) should appear within a few seconds.

The DB lives at `~/Library/Application Support/Cairn/meta.db` — that's the one
file to back up. Logs (the server's own diagnostics) go to
`~/Library/Logs/Claude/mcp-server-cairn.log`.

## Where things live

| | Source checkout (dev) | Frozen binary |
|---|---|---|
| Transport (default) | `streamable-http` on :8000 | `stdio` |
| DB | `./data/meta.db` | `~/Library/Application Support/Cairn/meta.db` |
| skills/schemas/guides/constitution | repo dirs (live-editable) | baked into the bundle |

Any `META_*` env var still overrides these in both modes — that's how the
smoke test points at a throwaway DB and how you'd relocate data.

## Distributing to others

Ad-hoc signing is enough for machines you control. For wider distribution the
binary needs Apple notarization, or recipients hit a Gatekeeper wall
("cannot be opened because the developer cannot be verified"). That needs an
Apple Developer account ($99/yr), a Developer ID certificate, real `codesign
--options runtime`, `notarytool submit`, and `stapler staple`. Out of scope
for now — revisit if Cairn goes past a handful of trusted users.

## Updating

Rebuild and re-copy. Since content is frozen, any change to a skill, schema,
guide, the constitution, or the code requires `./packaging/build-macos.sh`
again, then `cp dist/cairn ~/.local/bin/cairn` and a Desktop restart. The DB
in Application Support is untouched by a rebuild.
