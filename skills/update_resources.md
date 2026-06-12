---
name: update_resources
description: |
  Quick monthly refresh of resource quantities. Walks every resource,
  asks the user for the current value, updates `current_quantity` and
  `as_of` in lockstep. Takes 3–5 minutes. No analysis happens here —
  that's scan_inventory's job.

  Triggers: "/update-resources", "/refresh resources", "refresh my
  numbers", "update my runway", "my resource numbers are stale",
  monthly check-in, when the scanner reports stale_resource_update.

  Not for: adding new resources (use inventory_intake); scanning for
  patterns (use scan_inventory); transaction tracking (Cairn doesn't
  do that — use a budgeting app).
---

# Update Resources

## Before you begin

If you haven't this session, read `constitution://main` once.
Particularly relevant: the parts about not moralizing money and
about preferring honest data over polished narrative.

You are doing a periodic refresh of resource quantities. Quick,
focused, no analysis — that belongs in `scan_inventory`.

## Begin the operation

Call `state_begin_operation("update_resources")` and hold the returned
`operation_id` for use in this skill's other tool calls. The server
uses this to group everything you do in this flow into a single
auditable operation in the trace log.


## 1. Load resources

Call `state_query("resource")` to get all current resources. If
there are zero, tell the user the inventory has no resources yet
and route them to `inventory_intake`.

## 2. Walk through each

For each resource, show the current state and ask the new value:

  Cash runway — currently 8 months as of 2 months ago. Current value?

The user gives a number. Call:

```
state_update("resource", id, {
  current_quantity: <new value>,
  as_of: <now, ISO timestamp>
})
```

Both fields move together — the storage layer enforces that.

If the user wants to skip a resource (no change since last update),
still update `as_of` to now without changing `current_quantity` —
this keeps the resource fresh in the scanner's eyes. The whole
"current as of <date>" contract depends on the user actually
confirming the number, not just its change.

If the user wants to retire a resource (no longer tracking), confirm
and call `state_delete("resource", id)`. Resources are the only
entity type Cairn allows `state_delete` on; everything else has a
status transition instead.

If the user wants to add a new resource mid-flow, defer to
`inventory_intake` — don't add resources here. This skill is
refresh-only.

## 3. Optional: update burn/replenish rates

After current quantities are refreshed, ask whether `burn_rate` or
`replenish_rate` has changed for any resource. Most months the
answer is no. If yes, update via `state_update`.

## 4. Close

One-line summary:

  Resources updated: N refreshed, M skipped, K retired.
  Next scheduled refresh: 30 days from now.

Note: if the user has triggers enabled (deferred to v5), set a
30-day trigger to invoke this skill again.

## Notes on judgment

- Don't ask follow-up questions about *why* a number changed. That's
  the user's business. Take the number, update the row, move on.
- If the user is uncertain about a number ("maybe 7 months? hard to
  say"), record their best guess and move on. The point is keeping
  data fresh, not perfecting it.
- Money is the tone-trap. Treat the numbers as numbers; don't react
  to size changes ("runway looks tight" / "great runway") — that's
  exactly the kind of involvement the user isn't asking for in this
  flow.


## Close the operation

Call `state_end_operation(operation_id, "completed", notes="<one-line
summary>")` to mark the flow complete. The notes are a brief summary
of what actually happened — *"3 drafts committed"*, *"no patterns
crossed threshold"*, *"4 capabilities added"* — and they're what
appears next to this operation in the trace view. If the flow ended
without completing, pass `"abandoned"` as the status instead.
