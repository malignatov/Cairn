# Cairn on macOS — install, run, and connect

This is the early-adopter manual for the native builds of Cairn — no Docker,
no Colima, no commands left running in the background. It covers the onboarding
journey (seed Cairn from your ChatGPT memory, then live in Claude), getting
Cairn in place, where your data lives, and connecting a client — Claude Desktop
(the simple, fully local path) or the ChatGPT desktop app (a deliberate,
temporary detour).

Cairn is a personal system. It holds a single person's strategic state —
projects, decisions, principles, life-area ratings, the lot. Keep that in
mind as you choose where to run it and what to expose.

---

## The onboarding journey — seed from ChatGPT, then live in Claude

Most people arrive already using ChatGPT, with useful "memory" about themselves
living there. The goal is to land that in Cairn and then **live in Claude** —
fully local, nothing exposed. There are two ways to do the *seeding* step; the
end state is identical.

**Two run-forms of Cairn, so the rest of this makes sense:**

- **The stdio binary** (`cairn`, §1) — Claude Desktop spawns it directly. Fully
  local, no port, no server. Best for everyday Claude use.
- **The menu-bar app** (`Cairn.app`, built via `packaging/macos-app/`) — runs an
  HTTP server you reach by URL. Needed only if you want to *temporarily* tunnel
  to ChatGPT, or run an always-on local server. See
  `packaging/macos-app/README.md`.

### Variant A — copy-paste (recommended; no tunnel, nothing exposed)

1. **Install** the stdio binary and connect Claude Desktop (§1, §3a).
2. **In a plain ChatGPT chat** (no connector, no tunnel), paste the prompt below;
   ChatGPT writes out what it knows about you as text.
3. **In Claude Desktop**, paste ChatGPT's output and say: *"Import this into
   Cairn using the capture/seed flow — stage each item as a draft, show me the
   batch, commit only what I confirm."* You vet every entry before it lands.

> Export everything you know and remember about me, for import into my personal
> strategic-state system. Group it under these exact headings, as markdown
> bullet lists. Be specific; pull from your saved memories and our past chats.
> Omit a heading if you have nothing for it.
>
> - **Projects** — things I'm working on or have killed (name + one-line + active/killed).
> - **Decisions** — choices I've made and the reasoning I had at the time.
> - **Commitments** — promises I've made, to myself or others (what + to whom + any due date).
> - **People I track** — only people I've clearly chosen to follow up with (name + relationship + anything I owe them). Be conservative; skip anyone mentioned only in passing.
> - **Interests** — recurring topics I return to that aren't projects.
> - **Principles / values** — operating rules I seem to hold, in my own words.
> - **Ideas** — sparks I've floated but not committed to.
> - **Skills & resources** — capabilities I have and key resources (money/time/tools) I've mentioned.
>
> Mark anything you're inferring rather than certain about with "(inferred)".
> Don't invent detail to fill gaps.

That's it — your data's in Cairn, you're in Claude, and ChatGPT never touched
your database. (Also worth pasting in: ChatGPT → Settings → Personalization →
**Manage memories**, the literal stored-memory list.)

### Variant B — live ChatGPT over a tunnel (only if you want GPT to do the writing)

Use the **menu-bar app** here — it serves the HTTP that you tunnel.

1. **Install & launch `Cairn.app`** — it serves `http://localhost:8000/mcp` from
   the menu bar.
2. **Tunnel + connect ChatGPT** (§3b): `cloudflared tunnel --url
   http://localhost:8000`, add `https://…/mcp` as a *no-auth* ChatGPT connector,
   run the init session. ⚠️ While the tunnel is up the server is open to anyone
   with the URL — ChatGPT can't present a token, so there's no real auth. Do it
   against a throwaway DB or accept the exposure, and keep it short.
3. **Remove the tunnel** (Ctrl-C). The public URL dies; the app keeps serving
   locally.
4. **Point Claude Desktop at the local app**: Settings → Connectors → add a
   custom connector at `http://localhost:8000/mcp` (no tunnel, no exposure).
   Same data, now local.

### Which to pick

Variant A, unless you specifically want ChatGPT to push the data itself. A is
simpler, exposes nothing, and the draft/commit review happens in Claude — where
you're headed anyway. Both leave you in the same place: **Claude + Cairn, local.**

---

## 0. What you need

- An **Apple Silicon Mac** (M-series). This build is arm64 only; it will not
  run on Intel.
- The `cairn` binary — either built by you or handed to you by whoever did.
- A client that speaks MCP: **Claude Desktop**, or the **ChatGPT desktop app**
  (Plus / Pro / Business / Enterprise / Edu — Developer Mode is required).

---

## 1. Get the binary in place

### If you were given the binary

Copy it somewhere **outside** `~/Documents`, `~/Desktop`, and `~/Downloads`.
macOS's privacy sandbox blocks apps (including the ones your AI client
spawns) from reading those folders, and it'll fail in confusing ways. The
conventional spot:

```sh
mkdir -p ~/.local/bin
cp /path/to/cairn ~/.local/bin/cairn
chmod +x ~/.local/bin/cairn

# A binary copied from another machine carries a quarantine flag. Clear it
# once, or macOS will refuse to run it:
xattr -dr com.apple.quarantine ~/.local/bin/cairn
```

### If you're building it yourself

From a checkout of the repo, on an Apple Silicon Mac:

```sh
./packaging/build-macos.sh      # → dist/cairn
./packaging/smoke-test.sh       # confirms it speaks MCP (optional but nice)
cp dist/cairn ~/.local/bin/cairn
```

The build bakes the skills, schemas, guides, and constitution into the
binary, so there are no extra files to ship.

---

## 2. Where your data lives

Cairn keeps everything in one SQLite file:

```
~/Library/Application Support/Cairn/meta.db
```

It's created automatically on first run (and seeded with the 16 life units).
**That one file is your entire database** — it's the only thing worth backing
up. Copy it somewhere safe now and then:

```sh
cp ~/Library/Application\ Support/Cairn/meta.db ~/Backups/cairn-$(date +%Y%m%d).db
```

**Migrating from a previous (Docker) setup?** Copy your existing database into
place *before* the first launch, or Cairn will start with an empty one:

```sh
mkdir -p ~/Library/Application\ Support/Cairn
cp /old/path/data/meta.db ~/Library/Application\ Support/Cairn/meta.db
```

---

## 3a. Connect Claude Desktop  *(simplest — fully local, recommended)*

Claude Desktop launches the binary itself and talks to it over a private
pipe. Nothing listens on a network port; nothing leaves your machine.

1. Open `~/Library/Application Support/Claude/claude_desktop_config.json`
   (create it if it doesn't exist) and add a `cairn` entry. If you already
   have other servers, add `cairn` as a sibling key — don't nest it:

   ```json
   {
     "mcpServers": {
       "cairn": {
         "command": "/Users/YOUR_USERNAME/.local/bin/cairn",
         "args": ["--stdio"]
       }
     }
   }
   ```

   Replace `YOUR_USERNAME` with the output of `whoami`.

2. Fully quit Claude Desktop (**Cmd-Q**, not just closing the window) and
   reopen it.

3. In any conversation, the Cairn tools (`state_query`, `state_draft`, …)
   should appear within a few seconds. Try: *"Read the Cairn overview guide
   and tell me what you can do."*

That's the whole setup. If something's off, see Troubleshooting below.

---

## 3b. Connect the ChatGPT desktop app  *(for testing — read this first)*

ChatGPT connects to MCP servers in the **opposite** way to Claude Desktop. It
does **not** launch local programs and it **cannot reach `localhost`**. It
only talks to a **remote server over public HTTPS**. So to test Cairn with
ChatGPT you have to (1) run Cairn as an HTTP server and (2) expose that server
to the internet through an HTTPS tunnel, then (3) register the tunnel's URL as
a custom connector.

> ### ⚠️ Before you tunnel — understand what you're exposing
>
> Cairn has **no authentication**. It was built to live on one machine and
> never face the network. A tunnel turns it into a public HTTPS endpoint, and
> a "no auth" connector means **anyone who learns the URL can read and write
> your entire strategic database.**
>
> For testing, the safe move is to **run against a throwaway database**, not
> your real one (the commands below do this). Bring the tunnel down the moment
> you're done. Only point ChatGPT at your real data if you've added
> authentication to the tunnel (e.g. Cloudflare Access) and understand the
> trade-off.
>
> If you don't need ChatGPT specifically, **use Claude Desktop instead** (3a)
> — it keeps everything local and skips all of this.

### Step 1 — Run Cairn in HTTP mode (throwaway data)

Open a Terminal window and start the server. The `META_DB_PATH` override
points it at a disposable database so your real one is never exposed:

```sh
META_DB_PATH=/tmp/cairn-test.db ~/.local/bin/cairn --http
```

It binds to `http://localhost:8000/mcp` and prints `Uvicorn running on
http://0.0.0.0:8000`. Leave this window open — the server runs until you press
**Ctrl-C**.

> Already running the old Docker container on port 8000? Stop it first
> (`docker compose down`), or pick another port:
> `META_PORT=8010 META_DB_PATH=/tmp/cairn-test.db ~/.local/bin/cairn --http`
> (and use that port in Step 2).

### Step 2 — Expose it over HTTPS with a tunnel

In a **second** Terminal window, start a tunnel to port 8000. Either tool
works; **Cloudflare Tunnel** needs no account, so it's the quickest:

```sh
# Cloudflare (no signup): brew install cloudflared
cloudflared tunnel --url http://localhost:8000
```

It prints a public URL like `https://random-words.trycloudflare.com`. Your
**connector URL** is that, with `/mcp` appended:
`https://random-words.trycloudflare.com/mcp`.

```sh
# ngrok alternative (needs a free account + authtoken):
#   brew install ngrok && ngrok config add-authtoken <token>
#   ngrok http 8000
# → connector URL is https://<id>.ngrok-free.app/mcp
```

Keep this window open too; the URL is only live while the tunnel runs.

### Step 3 — Add the connector in ChatGPT

1. ChatGPT → **Settings → Apps & Connectors → Advanced settings**, and turn on
   **Developer mode**. (Menu wording varies by version; older builds call it
   just "Connectors.")
2. Back under **Apps & Connectors**, click **Create**.
3. Fill in:
   - **Name:** `Cairn`
   - **Description:** `Personal strategic state — projects, decisions, principles.`
   - **MCP Server URL:** the tunnel URL with `/mcp`, e.g.
     `https://random-words.trycloudflare.com/mcp`
   - **Authentication:** **No authentication**
4. Click **Create**. ChatGPT will connect and list Cairn's tools.

### Step 4 — Try it

Start a chat, enable the Cairn connector (Developer-mode connectors are opt-in
per conversation, via the **+** / tools menu), and ask something like:
*"Using Cairn, read the overview guide and list my active projects."* You'll
see ChatGPT call `state_read_guide`, `state_query`, and friends.

### When you're done

Press **Ctrl-C** in both Terminal windows to stop the server and the tunnel —
the public URL dies immediately. Delete the connector in ChatGPT if you won't
reuse it, and remove the test DB: `rm /tmp/cairn-test.db`.

### ChatGPT-specific caveats

- **`/mcp` vs `/sse`.** OpenAI's current docs use the streamable-HTTP `/mcp`
  endpoint, which is what `--http` serves and what's used above. Some older
  guides reference an `/sse` endpoint; if a future ChatGPT build insists on
  SSE, that's a different transport this binary doesn't expose yet — tell me
  and it's a small addition.
- **Tunnel URLs are ephemeral.** A free Cloudflare/ngrok URL changes every time
  you restart the tunnel, so you'll re-create the connector each session.
  That's fine for testing; it's not a setup to leave standing.

---

## 4. Verify it's actually working

- **Claude Desktop:** ask *"What Cairn tools do you have?"* — you should see
  `state_*` tools. Server logs are at
  `~/Library/Logs/Claude/mcp-server-cairn.log`.
- **ChatGPT:** the connector page shows a green/connected state and a tool
  count after you create it.
- **Either:** a real test beats a structural one — ask it to *run the find
  skill for something you know is in your data*. If it comes back with a hit,
  the whole path (client → server → SQLite → search) is alive.

---

## 5. Troubleshooting

- **"cannot be opened because the developer cannot be verified" / it won't
  launch.** Quarantine flag. Run `xattr -dr com.apple.quarantine
  ~/.local/bin/cairn`.
- **Claude Desktop shows no Cairn tools.** Check the path in
  `claude_desktop_config.json` is absolute and correct, the JSON is valid
  (`python3 -m json.tool < that-file`), and that you fully **Cmd-Q**'d and
  relaunched. Then read `~/Library/Logs/Claude/mcp-server-cairn.log`.
- **`Operation not permitted` when the client starts the binary.** The binary
  is under `~/Documents`/`~/Desktop`/`~/Downloads`. Move it to `~/.local/bin`.
- **ChatGPT won't connect to the tunnel.** Confirm the URL ends in `/mcp`,
  that both the server and the tunnel windows are still running, and that the
  tunnel's HTTPS URL loads. Re-copy the URL — it changes on every restart.
- **Port 8000 already in use.** The old Docker container or another process
  has it. Stop it, or run with `META_PORT=<other>` and tunnel that port.

---

## 6. The honest summary of the two clients

| | Claude Desktop | ChatGPT desktop |
|---|---|---|
| How it reaches Cairn | spawns the binary locally (stdio) | remote HTTPS connector |
| Network exposure | none — fully local | a public tunnel while testing |
| Setup | one config line | run `--http` + tunnel + connector |
| Auth | n/a (local) | none, unless you add it at the tunnel |
| Good for | daily use | trying Cairn out in ChatGPT |

Claude Desktop is the native fit. ChatGPT works, but it's a test-bench setup,
not a way to live with Cairn. Treat your data accordingly.
