# Cairn

A small [MCP](https://modelcontextprotocol.io) server that stores your
projects, decisions, commitments, principles, ideas, and life signals so an
LLM client (Claude Desktop, the Claude apps, or anything that speaks MCP) can
help you think across days, weeks, quarters, and years. The server does no
reasoning of its own — it's a thin, auditable staging layer over one SQLite
file, plus a static-resource server for the markdown *skills* and *schemas*
that tell the LLM how to behave.

The philosophy lives in [`constitution.md`](./constitution.md). Start there if
you want to know what Cairn is *for*.

> ### This is a personal system — fork it empty
>
> Cairn holds one person's private life-graph. **This repository ships no
> user data.** The database is created **empty** on first run (seeded only
> with the 16 Strategic Life Units everyone starts from). When you clone or
> fork it you get the empty skeleton — which is exactly what you want: it's
> *your* memory to fill, not anyone else's.
>
> The live database (`data/meta.db`) and every backup (`backups/`) are
> **git-ignored and must never be committed** — they are deeply personal
> (people, finances, health, family). If you contribute, double-check your
> diff never includes a `.db` or a `backups/` file.

---

## Installing

Two ways to run Cairn. Pick one.

### Option A — native macOS menu-bar app (recommended on a Mac)

A signed-or-ad-hoc `Cairn.app` that lives in your menu bar, runs the server
locally, and needs no Docker. See
[`docs/macos-install.md`](./docs/macos-install.md) for the install + first-run
walkthrough, and [`packaging/macos-app/`](./packaging/macos-app/) to build the
`.dmg` yourself.

### Option B — Docker (any platform)

```sh
docker compose up --build
```

The server binds inside the container and is published to **`127.0.0.1:8000`
only** (loopback — not your LAN). It writes its SQLite file to `./data/meta.db`;
both survive `docker compose down`. Stop with `Ctrl-C` or `docker compose down`.

> **Exposing it beyond localhost?** Don't, unless you mean to. If you must,
> set `META_AUTH_TOKEN` (a long random string) and change the compose port
> mapping deliberately — the server warns loudly if it binds a non-loopback
> address without a token. Running from source (`python -m meta_assistant`)
> binds `127.0.0.1` by default.

### Running from source

```sh
pip install -e ".[dev]"
python -m meta_assistant            # HTTP on 127.0.0.1:8000
python -m meta_assistant --stdio    # stdio transport (what Claude Desktop spawns)
```

## Connecting a client

Once the server is running you should see Cairn's tools (`state_query`,
`state_draft`, …) and resources (`constitution://main`, `skill://capture`,
`schema://projects`, …) in your MCP client; the LLM picks them up
automatically. For the HTTP transport, add a custom connector pointing at
`http://localhost:8000/mcp` (no auth needed for local use). The macOS app and
`docs/macos-install.md` cover the Claude Desktop wiring end to end.

## Seeding from an existing conversation

Starting fresh but already have history (e.g. a long ChatGPT/Claude thread)?
[`docs/seed-upload-guide.md`](./docs/seed-upload-guide.md) walks through the
one-time intake that drafts your existing projects/decisions/people for you to
confirm — nothing lands without your OK.

## The capture flow

When you finish a meaningful conversation, ask the assistant to run the capture
skill:

> "Read `skill://capture` and apply it to this conversation."

The model will (1) **draft** entries via `state_draft`, (2) attach the relevant
chat excerpts with `state_attach_source`, (3) show them to you as a short list,
(4) wait for "all good" / "drop 2" / "edit 3", and (5) commit only what you
confirmed. No write to the canonical tables happens without your explicit OK —
that's the whole point of the `drafts` table: a safety net for an LLM that
might otherwise enthusiastically record things you didn't quite mean.

## Commitments, stakeholders, interests (v3.1)

- **Commitments** — small promises you make, to yourself or someone else.
  Different from decisions (which choose) and projects (which have scope). The
  scanner watches for commitments aging past their due date without resolution.
- **Stakeholders** — people you've *explicitly* chosen to track. Not everyone
  mentioned in chat — auto-extracting people into a tracked list is exactly the
  surveillance failure mode this design avoids.
- **Interests** — topics your mind keeps returning to that aren't projects.

All go through the draft/commit flow — the assistant drafts one at a time, you
confirm each.

## The strategic substrate (v3)

Something concrete to think *against*, so "blindspot" detection isn't vibes.

- **Once, then yearly** — `define_great_life`: a walk through Seligman's
  PERMA-V model; the output is a structured statement of what you want your life
  aimed at.
- **Quarterly** — `assess_life_portfolio`: Strack's 16 Strategic Life Units,
  scored on importance / satisfaction / hours, plotted on a 2×2. Strack reports
  95% of people have at least one unit in the upper-left (high importance, low
  satisfaction) — that's where the work is.
- **Between assessments** — captured entries get tagged with the life units they
  touch, and `scan_life_units` walks the portfolio for patterns (silence,
  erosion, mismatch), promoting only threshold-crossing observations to the
  inbox. When it flags a blindspot, it's pointing at a specific row in your own
  data.

## The proactive flow (v2)

Where capture records what you said, the proactive flow surfaces what you
*didn't* ask about. `opportunity_research` pulls your active projects/decisions,
forms its own research questions, does real web research, and writes a few
**suggestions** — each required to anchor to a specific entity in your data
(the storage layer rejects un-anchored noise). `inbox` triages them: accept /
reject (with a reason future runs read) / defer.

## Principles (v7), ideas (v8), digest (v9)

- **Principles** — your *ranked* operating rules, checked against decisions.
  Prescriptive (how you choose), distinct from PERMA-V (how life is going). A
  decision can override a lower-ranked principle to serve a higher one — that's
  the hierarchy working, not a failure. `define_principles` to set them up.
- **Ideas** — retained sparks, pre-commitment. They must mature (warm toward a
  project, or be parked/released on purpose) or the scanner gently surfaces them
  before they rot. `capture_idea` to jot, `review_ideas` to tend, `promote_idea`
  to graduate one into a project.
- **Digest** — `digest` reads your interests, researches what's genuinely new in
  each, and synthesizes a readable catch-up. Exploratory, the curiosity-feeding
  counterpart to `opportunity_research`.

Together: **interests → digest → ideas → opportunity_research → projects.**

## Editing skills and schemas

Drop a markdown file in `skills/` or `schemas/` (or `guides/`) and it's served
at `skill://<name>` / `schema://<name>` / `guide://<name>`. Editing an existing
file takes effect on the next read — resources re-read from disk on every fetch.

## What's stored

One SQLite file, a set of deliberately small tables: `projects`, `decisions`,
`commitments`, `stakeholders` (+ `stakeholder_project_links`), `interests`,
`drafts` (capture staging), `suggestions` (proactive inbox), `principles`
(+ `principle_revisions`, `decision_principle_evaluations`), `ideas`,
`sources` (+ `source_links`), the strategic substrate (`life_units`,
`slu_assessments`, `great_life_dimensions`, `entity_slu_links`,
`slu_observations`), the inventory (`capabilities`, `resources`,
`capability_requirements`), and the logging substrate (`chats`, `operations`,
`tool_calls`, `resource_reads`). Field-by-field docs live in
[`schemas/`](./schemas/). The schema is created idempotently with
`CREATE TABLE IF NOT EXISTS` plus a handful of in-code migrations, so it
upgrades itself on first start.

## Backups

`data/meta.db` is the entire database — copy it somewhere safe. The convention
here writes timestamped snapshots into `backups/` before risky changes; **that
directory is git-ignored** (it's your private data). Keep your own copies
off-machine.

## Running the tests

```sh
pip install -e ".[dev]"
pytest
```

No Docker needed — the suite exercises the storage layer directly and confirms
the server wires up tools and resources cleanly.

## Extending Cairn

If you (or another agent) modify the codebase, start with
[`AGENTS.md`](./AGENTS.md) — the patterns (polymorphic entity types,
draft/commit flow, derived read-time fields, scanner promotion gates,
dispatch-layer logging, migrations), where to make changes, and the design
decisions that are settled. The guiding principle when in doubt: *make the
change more boring.*

## Design ethos

Cairn is deliberately small: single-user, one machine, one SQLite file, no
multi-tenancy, no telemetry, no embeddings, lexical (transparent) search only.
The aim is a system you can fully read and trust with the most personal data
you have. Features are added only after living with the gap they fill.
