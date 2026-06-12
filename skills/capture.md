---
name: capture
description: |
  Extract structured entries (projects, decisions, commitments,
  stakeholders, interests) from the current chat or pasted material and
  stage them as drafts for the user to confirm. Conservative by
  design — a small set of well-formed entries is worth more than a flood
  of noise. No write to canonical tables happens without the user's
  explicit OK.

  Triggers: "/capture", "save this conversation", "record what we
  discussed", "extract from these notes", "absorb this transcript",
  "log this", "put this in the system", end-of-session wrap-up, user
  pastes a document/journal/transcript they want absorbed.

  Not for: proactive research the user didn't ask for (use
  opportunity_research); reviewing the assistant's proactively-surfaced
  suggestions (use inbox); interactive rating sessions (use
  assess_life_portfolio or define_great_life).
---

# Capture

## Before you begin

If you haven't this session, read `constitution://main` once. It sets the
tone the rest of this skill assumes — particularly the parts about
recording reasoning rather than feelings, and about preferring fewer
well-formed entries over many shallow ones.

You are closing a session. Your job is to harvest what's worth keeping
and write it to the memory layer through the MCP tools. Be conservative.
A small set of well-formed entries is worth more than a flood of noise.

## Begin the operation

Call `state_begin_operation("capture")` and hold the returned
`operation_id` for use in this skill's other tool calls. The server
uses this to group everything you do in this flow into a single
auditable operation in the trace log.


## 1. Gather sources

Identify what you're capturing from:
- The conversation itself (default).
- Any documents, transcripts, or notes the user has pasted or attached.
- Files the user has explicitly pointed at.

If sources are ambiguous, ask once. Don't guess.

## 2. Classify candidates

Read through the sources and identify candidate entries by type:
- **decision** — a choice was made, with rationale.
- **project** — a new project worth tracking, or a state change on an existing one.
- **commitment** — a promise the user made to themselves or someone else
  (`to_whom` defaults to `self`; otherwise capture the person's name).
  Different from a decision: a decision chose; a commitment will do.
- **stakeholder** — a person worth tracking deliberately. Draft
  stakeholders one at a time. Don't auto-extract everyone mentioned in
  passing; only people the user has clearly named as relevant to a
  project, relationship, or recurring concern.
- **interest** — a topic the user's mind returns to that doesn't fit
  as a project. Draft only when the topic has shown up across
  multiple sources or the user has named it explicitly.
- **idea** — a spark, a what-if, a thought the user floated without
  committing to it. Distinguished from a project by the absence of
  commitment: "I want to build X" is a project; "what if there were an X"
  or "interesting concept: X" is an idea. When you find an idea candidate,
  draft it as entity_type = "idea" with status = "spark". Do NOT promote
  it to a project during capture — ideas are pre-commitment by definition.
  If the user's language is genuinely committal, ask which they mean
  rather than assuming. (Ideas are not evaluated against principles in
  step 3c — that's decisions only — and they carry no SLU-tag requirement.)

If something doesn't fit any of these, leave it. The chat itself is
the backup; we're not trying to record everything.

## 2a. Check for duplicates

For each candidate decision, project, commitment, or interest, run
`state_search` with the candidate's name or what-clause, restricted
to that entity_type. If search returns a high-score match in the same
entity type, this candidate is probably a duplicate or an update to
an existing entry.

When a likely duplicate is found, surface it to the user:

  *"I noticed you already have a project called 'Move to Valencia'
  from September. Is this a new project, an update to that one, or
  were you thinking of the same one?"*

If it's an update, route to `state_update` on the existing entity
rather than drafting a new one. If genuinely new (e.g. a different
project that happens to share a word), proceed with `state_draft`
as before. If they're the same, drop the candidate.

Don't over-trigger this — only flag duplicates when the search score
is clearly high and the entity type matches. A search-result title
that just shares a word with the candidate isn't a duplicate. False
positives here are annoying.

## 3. Extract structured data

For each candidate, draft the structured entry using state_draft, not
state_write. Drafts go to a staging table for the user to confirm.

Required fields per type:

- decision: { what, rationale, alternatives_considered, revisit_at, project_id }
- project: { name, description, status, kill_criteria }
- commitment: { what, to_whom, due_at, related_project_id }
- stakeholder: { name, role, relationship_strength, owed_action, notes }
- interest: { topic, description, status }

If you can't fill a required field, ask the user once. If they don't
know either, skip the entry — incomplete entries pollute the DB.

Group all drafts from one capture under a single session_id (generate a UUID).

## 3a. Draft SLU tags

For each drafted entity (project, decision), identify which Strategic
Life Units it touches. Use state_list_active_slus to see the catalog;
match by name and content.

Most decisions touch one or two SLUs. Some touch more. Be conservative —
better to draft two clean tags than five fuzzy ones.

For each drafted entity, include a `slu_links` array in the draft
data with the matched life_unit_ids. On commit, the entity_slu_links
rows are created alongside the entity itself.

If you genuinely can't tell which SLU an entity touches, leave it
untagged. Better unlabeled than mis-labeled.

## 3b. Draft capability touches

For each drafted entity, identify whether it involves the user
exercising one or more of their capabilities. Use
`state_list_active_capabilities` to see the catalog.

If a capability is exercised, suggest calling
`state_mark_capability_exercised` with the entity id as the source
reference. The user can confirm or skip.

Be conservative. *"I used Python today"* → mark Python skill
exercised. *"I wrote some emails"* → don't mark anything. The point
is to keep `last_exercised_at` honest for capabilities that
meaningfully atrophy when not used, not to count every touch.

If the user appears to have acquired a NEW capability (passed an
exam, got a certification, bought significant equipment), draft a
new capability row. Same draft/commit flow — show the drafted row,
await confirmation.

If the user is acquiring a capability over time but doesn't have it
yet (e.g., *"I'm studying for the AWS cert"*), do **not** create a
capability — this is a project. Suggest capturing it as a project
instead.

## 3c. Evaluate decisions against principles

For each proposed *decision* (not projects, not other entity types —
decisions specifically), evaluate it against the user's ranked principles.

Call state_list_principles() to get the active principles in rank order.
If the user has no principles defined, skip this step entirely.

For each decision, reason about its relationship to each principle:

- **aligned** — the decision honors a principle in a notable way. Record
  only meaningful alignments, not every trivially-compatible one.
- **justified_override** — the decision departs from principle X but in
  service of a higher-ranked principle Y. Record with rationale and the
  override target. This is healthy; frame it as such.
- **unjustified_departure** — the decision departs from a principle with
  no higher principle being served. Record with rationale and severity
  (scaled by the departed principle's rank).

When a decision involves a departure from a high-ranked principle (top
third), surface it to the user before committing:

  "This decision departs from principle #2, [name]. Is this a deliberate
  override in service of a higher principle, or worth reconsidering?"

The user can respond:
  - "Override — I'm serving principle #1 here" → record as justified_override
  - "Hadn't thought about it / let me reconsider" → amend the decision
  - "The principle is wrong" → suggest running define_principles to revise it
  - "It's fine, just an exception" → record as unjustified_departure with
    their rationale

Record the evaluations via state_record_principle_evaluation only after
the decision itself is committed (evaluations reference the committed
decision_id).

Be proportionate. Don't interrogate every small decision against all
eleven principles. Surface friction only for genuine departures from
high-ranked principles. A decision that aligns with or is neutral to the
principles needs no commentary.

## 4. Attach sources

For every draft, call state_attach_source with the relevant excerpt
or document reference. Every entry should be traceable back to where
it came from. No orphaned data.

## 5. Show the user what you found

Render drafts as a short list:

  Decisions (2):
    1. Kill project X. Rationale: ... Revisit: never.
    2. Pause project Y until October. Rationale: ...
  Projects (1):
    1. New: meta-assistant MCP server. Kill criteria: ...

Keep it terse. The user is closing a session, not reading a report.

## 6. Wait for confirmation

The user will respond with one of:
- "All good" → call state_commit_draft on each.
- "Drop N, M" → reject those, commit the rest. Log rejection rationale
  if the user offers one.
- "Edit N: [change]" → state_amend_draft, re-confirm before committing.

Do not commit without explicit confirmation.

## 7. Close the loop

After commits land, output a one-line summary:

  Captured: 2 decisions, 1 project. Sources linked.

Nothing more.

## Notes on judgment

- Bias toward fewer, better entries.
- Don't capture venting as data.
- "Nothing to capture from this session" is a valid output and often the right one.
- If you find yourself wanting to capture something the user didn't say
  but you inferred — stop. Capture what was said.


## Close the operation

Call `state_end_operation(operation_id, "completed", notes="<one-line
summary>")` to mark the flow complete. The notes are a brief summary
of what actually happened — *"3 drafts committed"*, *"no patterns
crossed threshold"*, *"4 capabilities added"* — and they're what
appears next to this operation in the trace view. If the flow ended
without completing, pass `"abandoned"` as the status instead.
