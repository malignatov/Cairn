---
name: add_inventory_items
description: |
  Add one or a few capabilities or resources to Cairn's inventory in
  steady state. Use this instead of inventory_intake when there are
  just a couple of items to add — a new certification, a piece of
  equipment, a new tracked resource. Same draft/commit ethic,
  smaller frame.

  Triggers: "/add-inventory", "/add-capability", "/add-resource", "I
  got a new certification", "add this license", "I bought a <thing>
  I want to track", "add a resource".

  Not for: bulk import from a pasted list (use inventory_intake);
  refreshing existing resource quantities (use update_resources);
  diagnostic scanning (use scan_inventory); recording that the user
  exercised a capability (the capture skill does that via
  state_mark_capability_exercised).
---

# Add Inventory Items

## Before you begin

If you haven't this session, read `constitution://main` once. The
relevant part: stay conservative, propose what the user actually
claimed, no inference about adjacent items.

The user wants to add one or a few items to their inventory. Don't
re-run the full intake framing — this is a quick addition.

## Begin the operation

Call `state_begin_operation("add_inventory_items")` and hold the returned
`operation_id` for use in this skill's other tool calls. The server
uses this to group everything you do in this flow into a single
auditable operation in the trace log.


## 1. Take the items

The user describes one or more items. Parse what they give you. If
the description is sparse, ask for the minimum needed:

- For a capability: name, category (license / certification /
  credential / skill / hardware / access / other), level (only for
  skill / credential / other; never hardware/license/certification),
  expiration if applicable.
- For a resource: name, category (money / time / energy / other),
  unit, current quantity.

If they list 5+ items, suggest running `inventory_intake` instead —
that skill is built for batches.

## 2. Check for duplicates

For each item, run `state_search(name, entity_types=["capability"])`
or `state_search(name, entity_types=["resource"])`. If a high-score
match comes back in the same entity_type, surface it:

  *"You already have a capability called 'Driver's license (UK)'. Is
  this an update to that one, or a different license?"*

If it's an update, route to `state_update` on the existing entity
rather than proposing a new one. If genuinely different, proceed.

## 3. Propose

For each item, call `state_draft` with
`entity_type = "capability"` or `"resource"` and the parsed fields.
Don't group under a session_id — these are individual additions, not
a batch import.

## 4. Show and confirm

Render the proposed items concisely. Confirm in one go (not batched,
since there are few items). User says yes / no / edit per item.

## 5. Commit and close

Commit confirmed items via `state_commit_draft`. Offer SLU tagging
only for capabilities/resources with clear strategic relevance —
don't prompt for daily-driver items.

One-line summary:

  Added: 2 capabilities, 1 resource.

## Notes on judgment

- For new capabilities being acquired (not yet held), suggest
  capturing as a project instead — aspirations are projects, not
  capabilities. *"Studying for AWS SAA"* → project. *"Passed AWS
  SAA last week"* → capability.
- For resources, ensure `as_of` is set to now (the storage layer
  defaults to this; just don't override with a stale date).
  Stale-on-arrival data would defeat the purpose.
- If a capability has a near-term expiration, mention it: *"This
  expires in 4 months — the scanner will pick that up automatically."*
- Don't tag every item to an SLU. Reserve tags for items with clear
  strategic relevance. A driver's license usually doesn't need a tag;
  a darkroom does.


## Close the operation

Call `state_end_operation(operation_id, "completed", notes="<one-line
summary>")` to mark the flow complete. The notes are a brief summary
of what actually happened — *"3 drafts committed"*, *"no patterns
crossed threshold"*, *"4 capabilities added"* — and they're what
appears next to this operation in the trace view. If the flow ended
without completing, pass `"abandoned"` as the status instead.
