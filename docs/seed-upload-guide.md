# Seed upload — commitments, stakeholders, interests

This is a one-time procedure for landing the deferred seed-extraction
writes against the v3.1 schema. When the Cowork session originally ran,
projects and decisions were written but commitments, stakeholders, and
interests were rejected because the schema didn't yet have homes for
them. v3.1 added those homes. This guide walks the agent through
replaying the writes correctly.

Paste this whole document into a Cowork chat (or hand it to any MCP
client connected to Cairn) along with the seed JSON file. The agent
follows the steps; the user confirms each draft as it lands.

## Prerequisites

- Cairn is running and reachable at `http://localhost:8000/mcp`.
- The MCP client is connected and the `cairn` tool surface is visible.
- The seed JSON file (typically `meta-assistant-seed.json`) is
  available. It should contain three lists keyed by entity type:
  `commitments` (2 entries), `stakeholders` (3 entries), `interests`
  (10 entries) — though the exact counts vary by seed.
- v3.1 is deployed (verify with `state_list_active_slus` returning the
  16 SLUs — if that works, the rest is there).

## What the agent should do

You are uploading three categories of seeded data. Each one goes through
the **draft/commit flow**: you stage a draft, the user confirms,
the entry lands. You do **not** call `state_write` directly for these —
the staging step is the whole point. It's how the user opts in to each
addition rather than waking up to a database full of inferred entries.

Generate one `session_id` (a uuid) at the start and reuse it across all
drafts from this upload. That makes it trivial to inspect the whole
batch later with `state_list_drafts(session_id=...)`.

### 1. Commitments

For each commitment in the seed, call:

```
state_draft(
  entity_type="commitment",
  data={
    "what":               <short sentence: the promise>,
    "to_whom":            <name, or "self">,
    "due_at":             <ISO timestamp, or null>,
    "related_project_id": <project id if applicable, else null>
  },
  session_id=<the batch session_id>
)
```

Required fields are `what` and `to_whom`. Use `"self"` for
self-promises. `due_at` and `related_project_id` are optional; include
them when the seed has them.

If a commitment in the seed references a project by name, look it up via
`state_query("project", {"name": "..."})` first and use the id. If the
name doesn't match an existing project, leave `related_project_id` null
and mention the gap to the user — don't invent a project.

### 2. Stakeholders

For each stakeholder, propose **one at a time** and show the user before
the next:

```
state_draft(
  entity_type="stakeholder",
  data={
    "name":                  <how the user refers to this person>,
    "role":                  <one-phrase descriptor, optional>,
    "relationship_strength": <"close" | "regular" | "weak", optional>,
    "owed_action":           <what's outstanding, optional>,
    "notes":                 <free text, optional>
  },
  session_id=<the batch session_id>
)
```

Only `name` is required. Don't pre-populate `last_contact_at` from the
seed unless the seed has an actual dated contact event — better null
than guessed.

Be conservative. Stakeholders are deliberately opt-in per person; if
the seed mentions someone only in passing, ask the user whether to
include them rather than proposing automatically.

### 3. Interests

For each interest, call:

```
state_draft(
  entity_type="interest",
  data={
    "topic":       <short label, the user's framing>,
    "description": <one or two sentences, optional>,
    "status":      "active"  // unless the seed says otherwise
  },
  session_id=<the batch session_id>
)
```

Only `topic` is required. `source_count` is **derived at query time**
from `source_links` — don't try to set it; it'll be zero until sources
are attached.

## Show the user what you've staged

After all drafts are written (don't drip them out one chat turn at a
time — batch the writes, then show), render a single short summary:

```
Staged for confirmation:

Commitments (2):
  1. Send long letter to parents. Due 2026-06-01.
  2. Draft new portfolio post. Self. Due 2026-05-31.

Stakeholders (3):
  3. Parents. Family, close. Owed: long letter.
  4. Surf Bar owner. Club host, regular.
  5. Club regulars. Weekly crowd, regular.

Interests (10):
  6. Russian acmeist poetry
  7. Long-form essays on attention
  ... [the rest]
```

Keep it terse. The user is confirming, not reading a report.

## Wait for confirmation

The user will respond with one of:

- **"All good"** → call `state_commit_draft(id)` on every staged
  draft. The draft/commit flow is the safety net; once the user
  says yes, the writes go through cleanly.

- **"Drop N, M, …"** → call `state_reject_draft(id, reason?)` for
  the dropped ones and `state_commit_draft(id)` for the rest.
  Capture any reason the user offers.

- **"Edit N: [change]"** → call `state_amend_draft(id, patch)`
  with the change, then re-confirm before committing.

Do not commit without explicit confirmation. The whole point of the
draft flow is that the user gets a clean veto.

## Optional follow-ups

These aren't required for the upload to succeed but make the data more
useful for the scanner. Mention them to the user after the main batch
commits and let them choose:

### Tag interests with Strategic Life Units

For each interest the user wants tagged:

```
state_list_active_slus()                       # find matching SLU ids
state_link_entity_to_slu(
  entity_type="interest",
  entity_id=<interest id>,
  life_unit_id=<slu id>
)
```

Most interests touch one SLU (e.g. "Russian acmeist poetry" →
"Hobbies and interests"; "olympic-style weightlifting" → "Physical
health"). Some touch more than one. Be conservative — two clean tags
beat five fuzzy ones.

### Link stakeholders to projects

For each stakeholder who's involved in a tracked project:

```
state_link_stakeholder_to_project(
  stakeholder_id=<id>,
  project_id=<id>,
  role_in_project=<one-phrase descriptor, optional>
)
```

This powers the scanner's eventual "boundary leak" pattern (a project
the user is grinding on with no acknowledgment of the people in it).

### Attach the original source excerpts

If the seed JSON includes the original journal/chat excerpts each
entry was extracted from, attach them so the lineage is preserved:

```
state_attach_source(
  target_type=<"commitment" | "stakeholder" | "interest">,
  target_id=<entity id>,
  source_type=<"chat" | "paste" | "file">,
  content=<the original excerpt>
)
```

For interests, this also makes `source_count` start at one (or however
many excerpts you attach) rather than zero — the derived counter
becomes meaningful immediately.

## Close

After commits and any follow-ups land, output a one-line summary:

```
Uploaded: 2 commitments, 3 stakeholders, 10 interests. (N rejected, M deferred.)
Tags: K interests linked to SLUs. J stakeholders linked to projects.
```

Then stop. Don't propose next steps; the user knows what to do next.

## Notes on judgment

- The seed JSON is scratch material, not gospel. If a seed entry feels
  wrong (a "stakeholder" who was mentioned once in passing; an
  "interest" that's actually a one-off curiosity), say so when you
  surface it for confirmation. The user can drop it without ceremony.

- Don't add commitments to past dates as `done`. If the seed shows a
  promise that's already been fulfilled, mention it to the user and
  ask whether to log it as `done` with an `outcome`, or skip it
  entirely. Backfilled commitments rarely earn their keep.

- Don't write `suggestions` from anything in the seed. The seed is
  the user's own past words, not the system's suggestions; routing it
  through `suggestions` mis-labels its provenance.

- If the seed contains "PERMA-V signals" or similar — fragments
  suggesting how the user feels about life dimensions — do **not** try
  to write them as `great_life_dimensions` rows. Those require user
  ratings (0–10), which can't be backfilled from documents. Keep those
  signals in the seed JSON for the next `define_great_life` session;
  they'll be passed in as `prior_signals` context there.
