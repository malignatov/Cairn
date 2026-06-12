# AGENTS.md — orientation for AI agents working on Cairn

If you're another Claude (or any agent) opening this repo cold, read
this once. It's the orientation that ten conversations of context
won't give you.

For agents *using* Cairn at runtime through MCP — what tools exist,
how to find the right skill — read `guide://overview` and
`constitution://main` instead. This file is for agents *modifying*
the codebase.

## What Cairn is

A personal-use MCP server, written in Python, backed by one SQLite
file. It stores a single user's strategic state — projects,
decisions, commitments, stakeholders, interests, life-area ratings,
capabilities, resources, observations, suggestions, ranked principles,
ideas — and serves the procedural skills (markdown files) that tell a
connected LLM how to behave.

Cairn does no reasoning of its own. The LLM does the reasoning;
Cairn holds the data and the playbook.

It is **not** a general-purpose framework. Every design decision
optimizes for one strategically-engaged user, one machine, one local
SQLite file. Multi-user, multi-tenant, remote sinks, observability
SaaS — none of that. If a change you're considering only makes sense
at scale, it doesn't make sense here.

## The constitution

`constitution.md` is the operating philosophy. Tone, restraint,
honesty, the seven virtues. Read it before writing any user-facing
content (skills, schemas, error messages). The codebase's tone
deliberately echoes it.

## Version history (the shape of the system)

Each version added one coherent capability. Reading them in order
explains why the architecture looks the way it does.

| Version | What it added |
|---------|---------------|
| v1      | Capture flow: `projects`, `decisions`, `drafts` (named `proposals` until v6.2), `sources`. Draft/commit safety net. |
| v2      | `suggestions` (originally `idea_proposals`) + `opportunity_research` skill. Outward proactive layer. |
| v3      | Strategic substrate: Strack's 16 SLUs + Seligman's PERMA-V. Three intake/scan skills. |
| v3.1    | `commitments`, `stakeholders`, `interests` — gaps the v1 brief had explicitly deferred. |
| v3.2    | Audit hardening: actionable skill descriptions, cold-start scanner guards, constitution cue in every skill, `guide://overview`. |
| v3.3    | Rename: `idea_proposals` → `suggestions`. Live data migration. |
| v4      | Inventory: `capabilities`, `resources`, `capability_requirements`. Adds structural patterns (expiring, atrophying, runway, gap) to the existing behavioral scanner. |
| v5      | FTS5 full-text search across every indexed entity. `state_search` tool + `find` skill. |
| v5.1    | Patch / quick-update flows for the SLU portfolio. `session_type` on `slu_assessments`. |
| v6      | End-to-end logging substrate (`chats`, `operations`, `tool_calls`, `resource_reads`). Dispatch-layer wrapper auto-logs every call. `trace` skill. |
| v6.1    | Tool-wrapped content access (`state_read_constitution`, `state_list_skills` + `state_read_skill`, same for schemas and guides). Closes the discovery gap where tool-only MCP clients (Desktop, Cowork, claude.ai) couldn't reach the resource layer at all. |
| v6.2    | Rename: `proposals` table → `drafts`, and the five capture tools (`state_propose` → `state_draft`, etc.). Ends the near-synonym collision with `suggestions`. Live data migration; old literals preserved in the migration detector. Suggestion skills swept to stop calling suggestions "proposals". New `schema://drafts`. |
| v7      | Ranked principles: `principles`, `principle_revisions`, `decision_principle_evaluations`. Prescriptive operating rules (govern how the user chooses) vs PERMA-V's descriptive measure. Atomic rank-reordering; append-only revision log; the three-case decision↔principle model (aligned / justified_override / unjustified_departure). New `define_principles` skill; capture step 3c; opportunity_research principle filter; scanner `repeated_unjustified_departure`. Additive tables (no migration fn — SCHEMA's `IF NOT EXISTS` creates them). |
| v8      | Ideas: `ideas` — retained sparks, pre-commitment. Lifecycle spark→exploring→someday→promoted/released; **heat is derived** from `last_touched_at` (warm/neutral/cooling), never stored; only spark/exploring can cool. The point is maturation (move or die), not storage. New `capture_idea` + `review_ideas` skills; capture spark-recognition; opportunity_research feedstock + idea-viability; scanner `cooling_idea` (patient: 6mo threshold, quarterly, batch, never `someday`). `promote_idea` creates a project. Additive table (no migration fn). |
| v9      | Interest digest: one new skill `digest`, **no tables, no tools**. Reads active interests (`state_query`), researches what's genuinely new in each via the LLM's web search, synthesizes readable prose (not link dumps), optionally sparks an idea. Divergent/exploratory — the curiosity-feeding counterpart to opportunity_research's convergent/actionable hunting; deliberately *not* a `scan_*` (those are diagnostic). Closes the loop: interests → digest → ideas → opportunity_research → projects. Known v9 limitation: no digest-history, so reruns can repeat (manual-only mitigates; history-awareness via v6 op logs is a future add). |

## File layout

```
Cairn/
├── constitution.md           — operating philosophy (LLM-facing)
├── README.md                 — user-facing setup + flow walk-through
├── AGENTS.md                 — this file
├── pyproject.toml            — Python package + test config
├── Dockerfile                — server image
├── docker-compose.yml        — local deployment
├── src/meta_assistant/
│   ├── storage.py            — Storage class: tables, validation, queries, helpers
│   ├── server.py             — FastMCP server: tool registrations, logging wrapper
│   └── __main__.py           — `python -m meta_assistant`
├── skills/                   — procedural markdown (LLM reads these)
├── schemas/                  — per-entity field docs (LLM reads these)
├── guides/                   — meta-orientation (currently just overview.md)
├── scripts/
│   └── mcp-http-bridge.py    — stdio↔HTTP bridge for Claude Desktop
├── tests/                    — pytest suite, one file per version
├── docs/
│   ├── seed-upload-guide.md  — one-off intake procedure
│   └── deployment.md         — moving Cairn between machines
├── data/                     — bind-mounted; holds meta.db (gitignored)
└── backups/                  — pre-version-bump backups (gitignored)
```

## Core patterns

### 1. Polymorphic entity tools

`state_write` / `state_query` / `state_update` / `state_draft` all
take an `entity_type` parameter (`project`, `decision`, `commitment`,
`stakeholder`, `interest`, `suggestion`, `capability`, `resource`,
`principle`, `idea`) and dispatch to per-type creators inside
`Storage.write` / etc.

Validation lives in `_create_<type>` methods. Type-allowed sets are
declared as module-level constants (`VALID_ENTITY_TYPES`,
`VALID_UPDATE_TYPES`, `VALID_DRAFT_ENTITY_TYPES`,
`VALID_LINK_TARGETS`, `VALID_SLU_LINK_ENTITY_TYPES`).

**Adding a new entity type** is mechanical:
1. Add `ENTITY_<NAME>` constant and add it to the relevant sets.
2. Add `<plural> -> table_name` mapping to `_TABLE`.
3. Add a `CREATE TABLE IF NOT EXISTS <plural>` block to `SCHEMA`.
4. Write a `_create_<name>(self, data)` method that validates fields
   and inserts.
5. Add a dispatch branch in `Storage.write`.
6. Optionally extend `Storage.update` to validate any type-specific
   transitions.
7. Add to `SEARCHABLE_ENTITIES` (the v5 FTS5 list) with title/body
   expressions, AND add a row to `entity_slu_links` valid types if it
   should be SLU-taggable.
8. Write `schemas/<plural>.md`.
9. Write tests.

### 2. Draft/commit flow

The capture skill never writes user-stated content directly to the
canonical tables. It goes through `drafts` (status `pending` →
`committed` / `rejected` / `amended`). The user reviews before any
write lands. This is the system's structural defense against an LLM
inferring entries the user didn't actually claim. (The table and its
tools were named `proposals` / `state_propose` until v6.2, renamed to
end the near-synonym collision with `suggestions`.)

The draft flow is enabled per-entity-type via inclusion in
`VALID_DRAFT_ENTITY_TYPES`. As of v3.1 every captured entity goes
through it. Suggestions explicitly do NOT — they're the assistant's
voice, reviewed via the separate `inbox` flow.

### 3. Suggestions and the inbox

`suggestions` (v2; renamed from `idea_proposals` in v3.3) are
proactive findings produced by `opportunity_research`, `scan_life_units`,
or `scan_inventory`. Every suggestion **must** anchor to specific
internal data (`linked_evidence.internal_anchors`) — storage rejects
unanchored writes. That's the structural defense against generic
LLM-shaped noise.

The inbox flow (`skill://inbox`) handles disposition: pending → accepted
/ rejected / deferred. Rejection reasons feed back into future scans
as "don't re-pitch this."

### 4. Scanner observation→suggestion promotion

Scanners (`scan_life_units`, `scan_inventory`) write `slu_observations`
silently on every run. Only observations crossing severity thresholds
get promoted to suggestions. This noise gate lets the scanner run
often without spamming the inbox. Multiple medium observations on the
same item across runs compound and eventually promote.

### 5. Discovery layered through markdown

The LLM's discovery path:
1. `resources/list` returns 30+ resources with rich frontmatter
   descriptions. Triggers + "not for" sections live in YAML frontmatter
   and are parsed at registration time (`_parse_skill_description`).
2. `guide://overview` maps user intents to skills.
3. Each skill's body contains the procedure; the LLM reads only the
   matching one.
4. Each skill begins with a constitution cue (`Read constitution://main
   once per session`).

Adding a new skill: drop a markdown file in `skills/` with proper
YAML frontmatter (`name:`, `description:` containing WHAT / Triggers /
Not for). The server picks it up at next start automatically.

### 5b. Two access paths for content (v6.1)

A discovery problem the early architecture didn't anticipate:
**most production MCP clients (Claude Desktop, Cowork, claude.ai
chat) don't surface resources to the LLM at all.** They show the
model the tool list but not the resource list, and offer no
`read_resource` capability. Resource-aware clients (Claude Code,
custom MCP setups) do surface both.

To close this without forcing every skill to be a tool, Cairn exposes
the same content two ways:

| Path | Client | Examples |
|---|---|---|
| **Resource URIs** | Resource-aware | `resources/read("skill://capture")`, `constitution://main`, `guide://overview` |
| **Tool wrappers** | Tool-only | `state_read_constitution()`, `state_list_skills()`, `state_read_skill("capture")`, `state_list_schemas()`, `state_read_schema("projects")`, `state_list_guides()`, `state_read_guide("overview")` |

Both paths return identical bytes (the `_parse_skill_description`
helper is shared, so trigger-rich descriptions show up in both).
Resources stay registered for clients that support them; the
wrappers are purely additive.

When you add a new skill / schema / guide markdown file, both paths
pick it up automatically — no per-skill registration needed in the
tool surface.

For the model's own behavior: skills still say *"read skill://X"* in
their text. A capable model interprets that as "use the right path
for my client" — call `state_read_skill("X")` if it's only seeing
tools, follow the URI otherwise. The `guide://overview` doc has a
"Two access paths" table that spells this out for any model that
gets confused.

### 6. Dispatch-layer logging (v6)

Every tool call and resource read is logged automatically via
`_logged_tool` and `_logged_resource_read` wrappers, applied to all
registered tools/resources at the end of `build_server` by
`_wrap_all_tools_for_logging` / `_wrap_all_resources_for_logging`.

You don't need to add logging to a new tool. Register it normally
with `@mcp.tool(...)`, and the post-registration sweep wraps it.

If you add a tool that itself manages logs (purges, queries them
heavily), add it to `_UNLOGGED_TOOLS` to prevent recursion.

The wrapper uses `id(session_object)` as a session identifier (NOT
`request_id`, which is per-call) so the chat correlation is stable
within an MCP connection.

### 7. Migrations

Migrations run from `Storage._run_migrations()` BEFORE the SCHEMA
script. They handle renames (`v3.3: idea_proposals → suggestions`)
and ALTER TABLE additions (`v5.1: session_type column`). All
idempotent — opening the same DB twice is a no-op the second time.

**Critical gotcha discovered the hard way**: if you do a global
find-and-replace across the codebase (e.g., renaming an identifier
across all files), the migration code itself will get touched. The
migration's job is to detect and rewrite the OLD name — so the OLD
literal strings must stay in that function. There's an inline comment
in `_migrate_v3_3_rename_idea_proposals_to_suggestions` warning
future-you about this.

### 8. FTS5 search

The `search_index` virtual table mirrors searchable text from every
major entity. Three triggers per source table (insert/update/delete)
keep it in sync automatically. Configuration in
`SEARCHABLE_ENTITIES` (tuples of entity_type, table, title expr,
body expr, created-at column).

The `state_search` tool wraps the user's query as a phrase by
default; queries containing `AND` / `OR` / `NOT` / `*` pass through
as-is. Tokenizer is `unicode61 remove_diacritics 2` (Valéncia ≡
Valencia).

### 9. Read-time enrichment (derived fields & label resolution)

Reads add computed fields the tables don't store — `source_count`
(interests), `heat` (ideas), `is_satisfied` (capability_requirements).
These are derived fresh on every read precisely so they can't go stale.

The same idiom resolves opaque FK UUIDs to human labels: every
user-facing read adds a `<field>_name` (or `_label`) **alongside** the
id — never replacing it (the LLM needs the id for tool calls), never
stored (a rename would otherwise leave a stale copy). The machinery:

- `_DISPLAY_FIELD` maps each table to its human column (projects→name,
  decisions→what, ideas→title, …); `_LABEL_MAXLEN` clips long free text.
- `_label_for(conn, table, id)` and `_label_for_entity(conn,
  entity_type, id)` (the latter for polymorphic link/anchor refs) do the
  lookup; both return None for null/missing.
- `_QUERY_FK_LABELS` declares which FK columns `query()` resolves per
  entity type (decision→project_name, commitment→related_project_name,
  idea→promoted_to_project_name). The bridge/link readers
  (`list_principle_departures`, `list_entity_slu_links`,
  `list_observations`, `list_capability_requirements`,
  `list_stakeholder_project_links`) and suggestion anchors enrich inline.

**Adding a new FK or entity type:** add the table's display column to
`_DISPLAY_FIELD`, and either add the FK to `_QUERY_FK_LABELS` (for a
top-level entity read) or resolve it inline in the relevant list method.
Keep the id; add the label; never store it.

## Sensitivity tone

Some skills touch sensitive domains — money, health, relationships,
meaning. Scanner output and capture drafts for those areas use
gentler framing. Specific patterns:

- Money suggestions: facts + invitations, never prescriptive coaching.
- Sensitive-domain blindspots: severity is down-weighted one notch
  before promotion; at most one per scanner run.
- "Stakeholders are opt-in per person" — never auto-extract people
  from chat into the stakeholder table.

When you add or modify content in these areas, re-read the
constitution and the scanner's "tone calibration" sections.

## Testing conventions

- One test file per version: `test_storage.py`, `test_idea_proposals.py`
  → `test_suggestions.py`, `test_v3.py`, `test_v3_1.py`, `test_v3_2.py`,
  `test_v4_inventory.py`, `test_v5_search.py`, `test_v5_1_patch_flows.py`,
  `test_v6_logging.py`.
- A shared `conftest.py` provides a `storage: Storage` fixture
  backed by `tmp_path`.
- Migration tests use raw sqlite3 to construct pre-version DBs, then
  open with `Storage(db_path)` to verify the migration ran.
- End-to-end skill behavior isn't directly testable (it's
  LLM-mediated); instead, tests verify that every skill markdown
  references the required tools (`state_begin_operation`,
  `state_end_operation`, `constitution://main`) and that handoff
  skills close their operation before invoking the next.
- Run `.venv/bin/pytest -q` from project root. The full suite runs
  in ~10s.

## When you ship a change

1. **Backup the live DB first.** Always. Even for "safe" changes.
   `cp data/meta.db backups/meta.db.$(date +%Y%m%d-%H%M%S).pre-<name>`
2. **Run the test suite before AND after.** If "after" passes, the
   change is safe to deploy.
3. **Rebuild the container.** `docker compose up -d --build` —
   migrations run on first start of the new image.
4. **Verify migration on live data.** Confirm row counts of existing
   tables, confirm new tables exist if added, confirm migration
   markers (e.g., `session_type='full'` backfill).
5. **Smoke test through the MCP wire.** Initialize, call a tool,
   check the response. The earlier conversation has the exact curl
   incantation if you need it.

If anything looks wrong on step 4, the backup from step 1 restores
in seconds: `docker compose down; cp backups/<file>.db data/meta.db;
docker compose up -d`.

## Deployment to another machine

See `docs/deployment.md` for the full procedure (Colima + Docker,
mount config, bridge script, Claude Desktop config). The short
version: copy the repo + `data/meta.db` to the new machine, install
Docker/Colima, configure the mount, `docker compose up -d --build`.
Migrations bring the DB up to current schema automatically.

## Known footguns

The kind of thing that bites a quarter later and isn't obvious from
reading the code. Future agents — including future-you — save time
by checking these first when something feels off.

- **Colima `writable: true` in `colima.yaml` doesn't reliably
  apply.** On at least one Colima version, the config value gets
  silently dropped and the mount comes up read-only. The CLI flag
  works. After any `colima restart`, start with `colima start
  --mount /Users/<you>:w` explicitly. Symptom: SQLite errors
  *"attempt to write a readonly database"* from the running
  container. Fix: `colima stop && colima start --mount
  /Users/<you>:w`.

- **macOS Documents folder is privacy-restricted for GUI children.**
  If you spawn the MCP bridge from Claude Desktop and the bridge
  script lives under `~/Documents/`, the spawn fails with
  *"Operation not permitted"* before Python runs anything. Keep the
  bridge in `~/.local/bin/cairn-mcp-bridge.py` (or anywhere outside
  `~/Documents/`, `~/Desktop/`, `~/Downloads/`). The canonical copy
  lives in `scripts/mcp-http-bridge.py` in the repo; deploy by
  `cp`ing to `~/.local/bin/`.

- **Bridge silently routes through stale HTTP proxy env vars.**
  Corporate VPN clients (Zscaler etc.) set `HTTP_PROXY` /
  `HTTPS_PROXY` on the user account; these get inherited by
  Desktop's children. When the proxy is down (off VPN, at home), the
  bridge fails to reach localhost:8000 with confusing transport
  errors. The bridge already installs an empty `ProxyHandler` at
  startup to ignore proxy env vars entirely (see the top of
  `scripts/mcp-http-bridge.py`). If you ever modify the bridge,
  preserve that block.

- **Tool names cannot contain dots.** The Anthropic API validates
  MCP tool names against `^[a-zA-Z0-9_-]{1,64}$`. Cairn used dotted
  names through v3.2 (`state.query`, `state.write`) and Desktop
  rejected every message until they were renamed in v3.3 to
  underscores. Pattern to avoid: when you add a new tool, name it
  `state_xxx`, not `state.xxx`.

- **Single-row "full" sessions from before v5.1 confuse
  `state_get_latest_full_session`.** The v5.1 migration backfilled
  every legacy `slu_assessments` row as `session_type='full'` per
  the brief. If a legacy single-row write was actually a
  quick-correction (the user retroactively edited one rating), it
  reads as a one-rating "full" session and `patch_portfolio` would
  treat it as the baseline, missing the other 15 ratings. Check
  with `SELECT session_id, COUNT(*) FROM slu_assessments WHERE
  session_type IN ('full','patch') GROUP BY session_id`. Single-row
  full sessions are usually misclassified quick-updates; fix with
  `UPDATE slu_assessments SET session_type='quick_update',
  session_id=NULL WHERE session_id='<that-id>'`.

- **Don't run a global `sed s/old/new/g` over the storage migration
  function.** v3.3 nearly broke its own migration this way — the
  migration code reads the OLD name (`'idea_proposal'`) to detect
  what to rename, and a global rename made it look for the new
  name, turning the migration into a silent no-op. There's a
  comment in `_migrate_v3_3_rename_idea_proposals_to_suggestions`
  warning future-you. The v6.2 migration
  (`_migrate_v6_2_rename_proposals_to_drafts`, `proposals` → `drafts`)
  carries the identical warning for the same reason. Same principle
  for any future rename migration.

- **`docker compose down -v` deletes data volumes.** Use `docker
  compose down` (without `-v`) when you want to stop and restart.
  The `-v` flag wipes named volumes. If you ever do migrate to
  named volumes from bind mounts, this becomes load-bearing.

- **Cowork (claude.ai web) and Desktop use different MCP transports
  with different reliability.** Desktop spawns the Python bridge in
  `~/.local/bin/cairn-mcp-bridge.py` and that round-trip is solid
  (proven repeatedly). Cowork talks to MCP servers through claude.ai's
  own machinery and we've observed cases where its transport hangs
  for ~4 minutes on specific tool calls while the server itself
  responds in milliseconds — the request never reaches the server.
  Diagnostic: check `tool_calls` for any row from the affected chat
  with the affected tool_name. If there's no row, the call didn't
  reach Cairn. Fix: start a fresh Cowork chat, OR fall back to
  Desktop. Don't assume an alleged "Cairn hang" is server-side
  without checking the log first.

## What NOT to do

These are settled design decisions; don't relitigate them.

- **No multi-user / multi-tenancy.** Single-user system.
- **No semantic / vector search.** FTS5 lexical only. The
  transparency property (the user can see which word matched) is
  load-bearing.
- **No remote log shipping / telemetry.** Logs live in the same
  SQLite file as everything else.
- **No automatic retention policies.** Manual purge via
  `state_purge_logs`.
- **No transaction tracking for money.** Resources hold current
  values, not histories. If a feature belongs in a budgeting app,
  it doesn't belong here.
- **No "aspired" capabilities.** Aspirations are projects.
- **No auto-extraction of stakeholders.** Opt-in per person.
- **No suggestions without an internal anchor.** Storage enforces
  this at write time.
- **No async/queued processing.** Synchronous, in-process,
  one-thread-at-a-time.
- **No pre-seeded principles.** The user enters all principles fresh via
  `define_principles`. Cairn ships no default set (no Bushido, no anything).
- **No strict-mode principle enforcement.** Principles are soft — the user
  can always override with rationale. The system never blocks a decision.
- **No moral judgment of principles.** Capture, rank, and check against
  them; never evaluate whether a principle is "good." Don't infer principles
  from behavior, and never propose or adjust the ranking — the user ranks.
- **No surfacing single principle departures.** Only *repeated* unjustified
  departures reach the scanner. Justified overrides are never alarms. Only
  decisions are evaluated against principles at capture time.
- **No idea evaluation at capture time, and no auto-promotion.** Capturing
  an idea never triggers analysis or viability checks; ideas become projects
  only by explicit user choice (`promote_idea`). The system may *suggest*
  promotion (via opportunity_research) but never does it automatically.
- **No reference-counting for idea heat.** Heat derives from
  `last_touched_at` alone; mention-based warming is a future brief. No
  idea-to-idea linking / threads / graphs — ideas are flat in v8.
- **No aggressive cooling surfacing, and never nag `someday`.** Quarterly
  cadence, 6-month threshold, batch framing. The ideas layer is where the
  system is most patient; deliberately parked ideas are left alone.

If a brief explicitly contradicts one of these, the brief wins —
but the contradiction is worth noticing.

## A note on character

The codebase's voice — and the system's character — is deliberate.
Warm prose in user-facing surfaces, terse precision in code,
honesty about limits, restraint in what gets surfaced. The
constitution is the source of that voice.

When you write new content for Cairn (skills, schemas, error
messages, even commit messages), match it. The character carries
through the code into the user's experience; that's the load-bearing
property of a personal system.
