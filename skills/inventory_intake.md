---
name: inventory_intake
description: |
  One-time (or periodic top-up) bulk import of capabilities and
  resources from a pasted list. Parses the user's loose-format input
  into structured rows, stages them as drafts, and walks the user
  through batched commits. Conservative by design — no row lands
  without the user's confirmation.

  Triggers: "/inventory-intake", "/inventory", "import my inventory",
  "set up my capabilities", "I want to add my skills and resources",
  "paste my CV", user pastes a list of skills/licenses/equipment, user
  pastes resource numbers (cash runway, burn rate, hours/week).

  Not for: incremental capability touches during conversation (the
  capture skill handles those via state_mark_capability_exercised);
  refreshing existing resource numbers (use update_resources); the
  diagnostic pass over inventory (use scan_inventory).
---

# Inventory Intake

## Before you begin

If you haven't this session, read `constitution://main` once. It sets
the tone the rest of this skill assumes — particularly the parts
about preferring fewer well-formed entries over many shallow ones,
and about not extracting anything the user didn't actually claim.

The user is going to paste a list of capabilities (skills, licenses,
certifications, equipment, access rights) and/or resources (cash
runway, burn rate, time budgets). Your job is to read the list,
structure it into proper inventory rows, and walk the user through
commits.

This is the draft/commit flow, applied to many items at once. Don't
write anything directly — propose, show, confirm in batches.

## Begin the operation

Call `state_begin_operation("inventory_intake")` and hold the returned
`operation_id` for use in this skill's other tool calls. The server
uses this to group everything you do in this flow into a single
auditable operation in the trace log.


## 1. Receive the list

Ask the user to paste their list. The format can be loose — bullet
points, prose, a table — whatever they have. If they don't have one
ready, offer this template they can fill in:

  CAPABILITIES (skills, licenses, certifications, hardware, access):
  - Name | category | level (if applicable) | acquired (rough year ok) |
    expires (if applicable) | notes

  RESOURCES (strategic-level only):
  - Name | category | unit | current quantity | floor (optional) |
    burn rate per month (optional)

  Examples:
  - Driver's license (UK) | license | — | 2014 | 2031 | Renew in 2031
  - Mandarin conversational | skill | intermediate | 2018 | — | Atrophying
  - Cash runway | money | months | 8 | 4 | -0.5

The template is a hint, not a requirement. Parse what the user gives
you.

## 2. Parse and propose

Read the pasted text. For each item:

- Determine whether it's a capability or a resource.
- Map category to the fixed enum:
  - Capabilities: `license`, `certification`, `credential`, `skill`,
    `hardware`, `access`, `other`
  - Resources: `money`, `time`, `energy`, `other`
- Pull out the fields you can. If a field is unclear, leave it null
  and flag it for the user to fill in.

Generate a `session_id` (uuid) for this intake. Call `state_draft`
for each item with `entity_type = "capability"` or `"resource"`,
grouping them under the session_id.

If an item is ambiguous (could be either a capability or a resource),
ask the user once before proposing. Don't guess.

## 3. Show the user what you parsed

Render in two groups:

  Capabilities (N):
    1. Driver's license (UK) — license, acquired 2014, expires 2031
    2. Mandarin conversational — skill (intermediate), last used 2 yrs ago
    3. ...

  Resources (M):
    1. Cash runway — money, 8 months, floor 4 months, net burn 0.5/mo
    2. ...

Keep it terse. Use the structure the user gave you; don't editorialize.

If there are items where you couldn't determine a required field,
flag them clearly:

  Items needing attention:
    Item 3: "Photography portfolio" — is this a skill (your ability)
    or hardware (your camera kit)? Or both?

## 4. Walk through commits in batches

If there are more than 8 items total, batch them. Process 5–8 at a time:

  "I'll commit the first 6. Sound good? After that I'll show you the
   next batch."

For each batch, await confirmation. The user can respond with:

- "All good" → commit all in this batch via `state_commit_draft`
- "Drop N" → reject those, commit the rest
- "Edit N: [change]" → amend via `state_amend_draft`, re-confirm
- "Pause" → stop the intake; the user can resume by re-invoking the
  skill

## 5. SLU tagging

After commits land, ask the user briefly about SLU tags for the most
strategic items (their high-level capabilities and key resources).
For each, propose 1-2 SLU tags and let the user confirm. Use
`state_link_entity_to_slu`.

Don't tag every item — only ones with clear strategic relevance.
Daily-driver capabilities (driver's license, basic computer skills)
usually don't need SLU tags.

## 6. Close

One-line summary:

  Inventory: N capabilities committed, M resources committed.
  K items tagged to SLUs. Run scan_inventory to surface anything
  urgent.

## Notes on judgment

- Be conservative on capability levels. If the user said "I know some
  Spanish" don't guess "intermediate" — leave level null and ask if
  it matters.
- For expiration dates, prefer to ask if the year is unclear. A wrong
  expiration date is worse than no expiration date.
- Don't propose aspired capabilities. If the user lists "Spanish C1
  (in progress)", that's a project, not a capability. Suggest they
  capture it as a project instead.
- If the user pastes a list with private information you don't need
  (account numbers, passwords, personal IDs), strip those silently
  before proposing. Cairn doesn't store secrets.


## Close the operation

Call `state_end_operation(operation_id, "completed", notes="<one-line
summary>")` to mark the flow complete. The notes are a brief summary
of what actually happened — *"3 drafts committed"*, *"no patterns
crossed threshold"*, *"4 capabilities added"* — and they're what
appears next to this operation in the trace view. If the flow ended
without completing, pass `"abandoned"` as the status instead.
