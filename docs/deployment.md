# Moving Cairn to another machine

Two phases: package up the source, then bring up the target.

The data file (`data/meta.db`) is the only thing that can't be
rebuilt. Everything else — code, schemas, skills, the Docker image,
the Python bridge — regenerates from the repo. Treat the migration
as "move the DB; bootstrap everything else."

This guide assumes both machines are macOS with Colima. The same
shape applies to Docker Desktop on Mac / Linux / Windows — only the
mount-config step changes.

## On the source machine

Stop the container cleanly and produce a single artifact you can
transfer.

```sh
cd ~/cairn

# 1. Stop the container. This leaves the image alone and only
#    affects the running process. data/meta.db stays put.
docker compose stop

# 2. Take one last fresh backup, just to have it.
TS=$(date +%Y%m%d-%H%M%S)
cp data/meta.db "backups/meta.db.${TS}.pre-migration"

# 3. Bundle everything you'll need on the target machine.
#    Exclude .venv (rebuilds locally), .git history is up to you,
#    backups/ is large and optional.
cd ..
tar --exclude='Cairn/.venv' \
    --exclude='Cairn/__pycache__' \
    --exclude='Cairn/**/__pycache__' \
    --exclude='Cairn/.pytest_cache' \
    --exclude='Cairn/backups/*' \
    -czf cairn-migration.tar.gz Cairn

ls -lh cairn-migration.tar.gz
```

Transfer `cairn-migration.tar.gz` to the target machine — USB stick,
secure copy, syncthing, whatever's convenient. It will be in the
1–10 MB range typically.

If you're going to keep using the source machine in parallel
("multi-device, two-way sync"), **don't**. Cairn doesn't merge.
Pick one machine to be canonical. Sync via the same backup-restore
mechanism if you ever need to migrate again.

## On the target machine

Three things to install: Colima + Docker, Python (for the bridge),
and a recent enough Claude Desktop.

### 1. Prerequisites

```sh
# Install Homebrew if you haven't
# /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

brew install colima docker docker-compose
# Python 3.13 from python.org or via Homebrew. /usr/bin/python3 (Apple's)
# is sufficient for the bridge — it only needs stdlib.

# Optional: confirm /usr/bin/python3 is there and at least 3.9
/usr/bin/python3 --version
```

### 2. Configure Colima with home-directory mount

**Critical.** Without this, the container won't be able to read or
write to the host's data directory, and you'll lose data on the
first container restart. See the conversation history for the full
story; the short version is below.

```sh
# Start Colima once to create the default profile config
colima start
colima stop

# Edit ~/.colima/default/colima.yaml — find the `mounts:` block and
# replace its contents with:
#
#   mounts:
#     - location: /Users/<your-username>
#       writable: true
#     - location: /var/folders
#       writable: true
#
# (`~` does NOT work in colima.yaml on at least one version; use the
# absolute path.)
```

After editing, start Colima with an explicit `--mount` flag so the
config actually takes effect:

```sh
colima start --mount /Users/$(whoami):w
```

Verify the mount is genuinely working both directions before
proceeding:

```sh
colima ssh -- ls -la /Users/$(whoami)/Documents | head -5
# Should list your actual Documents/ contents. If empty, the mount
# isn't live — fix that first or data persistence will silently fail.
```

If Colima ever shows the mount as read-only after a restart (the
`writable: true` setting in `colima.yaml` doesn't reliably apply),
restart with the explicit `--mount /Users/<you>:w` flag.

### 3. Unpack and start

```sh
mkdir -p ~
cd ~
tar -xzf ~/Downloads/cairn-migration.tar.gz   # or wherever you put it

cd Cairn

# Sanity-check the bundle came across intact
ls data/meta.db
sqlite3 data/meta.db "SELECT 'projects', COUNT(*) FROM projects \
  UNION ALL SELECT 'decisions', COUNT(*) FROM decisions \
  UNION ALL SELECT 'suggestions', COUNT(*) FROM suggestions;"

# Build the image and bring it up. The first run builds; subsequent
# starts are fast. Any pending schema migrations run automatically.
docker compose up -d --build

# Watch it come up; takes ~5 seconds
docker compose logs --tail 20 meta-assistant

# Should see "Uvicorn running on http://0.0.0.0:8000"

# Confirm the wire is alive
curl -s -o /dev/null -w "HTTP %{http_code}\n" -m 5 -X POST \
  http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"hc","version":"1"}}}'
# HTTP 200 = good
```

### 4. Verify data integrity post-migration

```sh
# Row counts should match the source machine. If you didn't note
# them, compare to your latest backup file by opening it with
# sqlite3 as a sanity check.
sqlite3 data/meta.db "
  SELECT 'projects',           COUNT(*) FROM projects
  UNION ALL SELECT 'decisions',        COUNT(*) FROM decisions
  UNION ALL SELECT 'commitments',      COUNT(*) FROM commitments
  UNION ALL SELECT 'stakeholders',     COUNT(*) FROM stakeholders
  UNION ALL SELECT 'interests',        COUNT(*) FROM interests
  UNION ALL SELECT 'suggestions',      COUNT(*) FROM suggestions
  UNION ALL SELECT 'capabilities',     COUNT(*) FROM capabilities
  UNION ALL SELECT 'resources',        COUNT(*) FROM resources
  UNION ALL SELECT 'slu_assessments',  COUNT(*) FROM slu_assessments
  UNION ALL SELECT 'sources',          COUNT(*) FROM sources;
"
```

### 5. Install the Python bridge

The bridge is a stdio↔HTTP wrapper Claude Desktop spawns to talk to
the local server. It lives outside `~/Documents` deliberately —
macOS's privacy sandbox blocks GUI-spawned children from reading
`~/Documents` without explicit permission, and putting the bridge
where Desktop will reliably read it removes a class of frustrating
errors.

```sh
mkdir -p ~/.local/bin
cp scripts/mcp-http-bridge.py ~/.local/bin/cairn-mcp-bridge.py
chmod +x ~/.local/bin/cairn-mcp-bridge.py

# Smoke-test the bridge
{
  echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"bridge-test","version":"1"}}}'
  echo '{"jsonrpc":"2.0","method":"notifications/initialized"}'
  echo '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
} | /usr/bin/python3 ~/.local/bin/cairn-mcp-bridge.py http://localhost:8000/mcp \
  | head -3
# Should return one [mcp-http-bridge] info line and two JSON responses
```

### 6. Connect Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`
and add the `cairn` entry under `mcpServers`. Replace
`<your-username>` with `$(whoami)`'s output:

```json
{
  "mcpServers": {
    "cairn": {
      "command": "/usr/bin/python3",
      "args": [
        "/Users/<your-username>/.local/bin/cairn-mcp-bridge.py",
        "http://localhost:8000/mcp"
      ]
    }
  }
}
```

If you already have other MCP servers in the config, add `cairn` as
a sibling key inside the same `mcpServers` object (don't nest).
Validate the file before launching:

```sh
python3 -m json.tool "$HOME/Library/Application Support/Claude/claude_desktop_config.json" > /dev/null && echo "valid JSON"
```

Fully quit Claude Desktop (Cmd-Q, not just close the window) and
relaunch. The Cairn tool surface should appear within a few seconds.

Watch for issues in the log:

```sh
tail -f ~/Library/Logs/Claude/mcp-server-cairn.log
```

Healthy reconnect prints `[mcp-http-bridge] bridging stdin/stdout ↔
http://localhost:8000/mcp` and `session established: ...` — and no
transport-closed lines.

## Validating the move worked

A meaningful test is more useful than a structural one:

```
You: /trace
```

If the trace skill returns recent operations from the source machine
(any logged activity from before the move), the data made it across
intact. If the trace shows only operations from after the new
container started — also fine, just means there wasn't logged
activity before v6.

A different meaningful test:

```
You: /find <something you know you have>
```

If `state_search` returns results from your old data, the FTS5 index
either survived the migration or was rebuilt from scratch on
container start (the seeding logic in `_seed_search_index` handles
both cases idempotently).

## Common failure modes

**Bridge fails with `Operation not permitted` reading the script**.
You put the bridge inside `~/Documents/`. Move it to
`~/.local/bin/` — macOS doesn't let GUI-spawned processes read
`~/Documents` without explicit permission.

**Tool calls work then fail at random ("transport closed")**. The
`HTTP_PROXY` / `HTTPS_PROXY` env vars are set on the new machine
pointing at a proxy that isn't running (Zscaler, Cisco AnyConnect,
etc.). The bridge ignores proxies by design — but if the user is on
a different shell that doesn't inherit those env vars when spawning
Desktop, the same issue can show up indirectly. Check:

```sh
env | grep -i proxy
```

If you see `HTTP_PROXY=http://127.0.0.1:<port>` set and the proxy
isn't running, either start the proxy or unset the var. The bridge
itself disables proxy lookup via `urllib.request.install_opener`,
so within the bridge there shouldn't be a problem — but other
clients (cowork sessions, anything not going through this bridge)
may hit it.

**Container starts, then immediately exits with FK errors on first
write**. The Colima mount is read-only. Stop and restart with the
`--mount /Users/<you>:w` flag.

**Data appears empty inside the container even though `data/meta.db`
exists on host**. The bind-mount didn't actually take effect — the
container is reading its own internal `meta.db` (an empty stub
created at first run when no real one was visible). Stop the
container, fix the Colima mount (see step 2 above), and restart.
The host file becomes visible; the container picks it up.

## What about transferring image instead of rebuilding?

The image is small (~100 MB) and builds in ~30 seconds from the
Dockerfile. You can `docker save` and `docker load` it across
machines if you want, but it's almost never worth the complexity —
the rebuild is fast enough that the source is the canonical
artifact.

The data file is the artifact you actually need to preserve.

## A note on architecture

Cairn was designed from v1 to be portable in this exact way: one
SQLite file, no external dependencies, declarative skills as
markdown files, all migrations idempotent. Every version since has
maintained that property deliberately.

If you ever find yourself wishing for "easier deployment," the
answer is almost certainly "make the change you're considering more
boring," not "add deployment tooling." The deployment is one tarball
and three commands. Keep it that way.
