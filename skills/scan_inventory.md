---
name: scan_inventory
description: |
  Diagnostic pass over the user's capabilities and resources. Detects
  expiring capabilities, atrophying skills, resources below floor,
  runway depletion, and capability-aspiration gaps on active projects.
  Writes observations on every run; promotes only the highest-conviction
  ones to suggestions.

  Triggers: "/scan-inventory", "scan my inventory", "check my
  capabilities", "find expiring licenses", "what's atrophying", "am I
  running out of runway", "check my runway", "where are my capability
  gaps".

  Not for: behavioral / portfolio drift on Strategic Life Units (use
  scan_life_units); intake of new inventory (use inventory_intake);
  refreshing resource numbers (use update_resources); external
  research (use opportunity_research).
---

# Scan Inventory

## Before you begin

If you haven't this session, read `constitution://main` once.
Particularly relevant here: the parts about surfacing facts without
moralizing, and about money/health-area framing.

You are running a structural diagnostic over the user's inventory.
Same ethic as `scan_life_units`: observations are data, suggestions
are alarms. A good run produces several observations and few
suggestions.

## Begin the operation

Call `state_begin_operation("scan_inventory")` and hold the returned
`operation_id` for use in this skill's other tool calls. The server
uses this to group everything you do in this flow into a single
auditable operation in the trace log.


## 0. Check data preconditions

Before computing signals, gauge what you have:

- `state_query("capability")` — how many active capabilities?
- `state_query("resource")` — how many resources? When was each last
  updated (`as_of`)?
- `state_list_capability_requirements` — any requirements declared?

Handle each cold-start case:

- **0 capabilities, 0 resources** → stop. Tell the user the inventory
  is empty; point them at `skill://inventory_intake` and exit.
- **All resources stale (as_of > 60 days)** → don't try to compute
  runway. Tell the user: "Resource data is too old to scan reliably.
  Run `update_resources` first." Still run capability checks.
- **No capability_requirements declared** → capability_gap signals
  won't fire. That's fine; note it in the closing summary so the user
  knows the scanner is working with partial data.

## 1. Load context

- All active capabilities, with `expires_at`, `last_exercised_at`,
  `status`.
- All resources, with `current_quantity`, `floor`, `burn_rate`,
  `as_of`.
- All active or snoozed projects with their capability_requirements.
- Past `slu_observations` (don't repeat yourself).
- Past suggestions of `kind = blindspot` involving inventory,
  especially rejected ones with reasons.

## 2. Compute signals

For each capability, check:

- **expiring_capability**: `expires_at` within
  `(renewal_lead_time_days OR 90 days)`. Severity = high if expiration
  is within 30 days OR if any active project has this capability as a
  requirement; medium otherwise.
- **atrophying_capability**: `last_exercised_at` older than 12 months
  AND `status='active'` AND category in `(skill, certification,
  access)`. Don't flag licenses/hardware for atrophy — they don't
  atrophy in the same way. Severity = medium; high if the capability
  is listed as a requirement on any active project. Use
  `state_list_atrophying_capabilities` to get the candidates.
- **stale_resource_update**: `as_of` older than 60 days. Severity =
  low; medium if `burn_rate` is set (can't compute runway without
  fresh data).

For each resource:

- **below_floor**: `current_quantity < floor`. Severity = high. Use
  `state_list_resources_below_floor`.
- **runway_short**: for resources with `burn_rate`, runway =
  `current_quantity / max(burn_rate - replenish_rate, 0)`. Severity =
  high if runway < 6 months; medium if runway < 12 months. Use
  `state_compute_resource_runway`.

For each active project with capability_requirements:

- **capability_gap**: any requirement not satisfied (capability
  missing, capability present at level below required, or resource
  below required amount). Use `state_list_capability_gaps`. Severity
  = high if the project is rated important; medium otherwise.

Write every triggered observation via `state_write_observation`.
Observations are silent by default.

## 3. Promote to suggestions

Promote an observation to a suggestion of `kind = blindspot` if:

- Severity = high, OR
- The same item has 3+ medium observations in the last 90 days, OR
- A capability_gap exists on a project the user has named as a priority

For each suggestion, the content must:

- Name the specific capability or resource.
- State the pattern in one sentence, without panic.
- Include the relevant numbers (months to expiration, months of
  runway, months since last use).
- Frame as a question or invitation when sensitive (money), as a fact
  when neutral (an expiring license is just a fact).

Example good content for expiring:

  Your commercial drone license expires in 47 days. Your "Coast
  videography" project has it listed as a required capability. Renewal
  usually takes 2–3 weeks — worth starting now.

Example good content for runway:

  Cash runway is at 4.2 months, below your stated floor of 4. At
  current net burn, you'll cross the floor next month. Worth a
  deliberate look before then.

Example good content for atrophy:

  Mandarin (intermediate) hasn't appeared in tagged activity for 18
  months. Either invest in maintaining it, or consider marking it
  lapsed so the inventory reflects current reality.

Anchor every suggestion to internal data via
`linked_evidence.internal_anchors` — the `capability_id` or
`resource_id`, plus any related `project_id`s.

## 4. Special caution: money

Money-related suggestions need especially careful framing. They touch
real anxiety, and Cairn isn't a financial advisor.

- Always frame as fact + invitation, never as judgment.
- Don't propose specific actions ("you should cut spending on X").
  The user knows their situation; the scanner's job is to surface
  the number, not to coach.
- Don't surface money suggestions if the user has rejected similar
  ones recently with reasons like "I know, I'm working on it." Trust
  their awareness.

## 5. Hand off to inbox

**Stop here.** Do not list, summarize, or describe the suggestions
you wrote — that's the inbox skill's job and mixing them produces a
worse review surface for the user.

**Close this operation first**, then hand off:

1. Call `state_end_operation(operation_id, "completed", notes="<N
   observations, M suggestions promoted>")` to close the inventory
   scan cleanly. The inbox flow that follows is its own operation;
   mixing it with this scan's would confuse the trace.
2. Then read `skill://inbox` and execute it. The inbox skill calls
   its own `state_begin_operation` and picks up the suggestions you
   just wrote.

## 6. Close

After the inbox handoff completes, output one line:

  Inventory scan complete. N observations recorded. M suggestions in
  the inbox.
  [Note any cold-start caveats from step 0 here.]

## Notes on judgment

- Stale resource data is a real problem — you can't scan what you
  don't have. If most resources are stale, the right output is "your
  resource data is too old to scan reliably; run update_resources
  first" rather than fabricating analysis.
- Expiring is almost always urgent enough to surface. Atrophy is
  rarely urgent enough on its own — promote it only when it
  intersects with active projects or repeated medium observations.
- Don't surface "you're using all your capabilities well" as a
  positive affirmation. The scanner notices problems; absence of
  problems is the default state.


## Close the operation

Call `state_end_operation(operation_id, "completed", notes="<one-line
summary>")` to mark the flow complete. The notes are a brief summary
of what actually happened — *"3 drafts committed"*, *"no patterns
crossed threshold"*, *"4 capabilities added"* — and they're what
appears next to this operation in the trace view. If the flow ended
without completing, pass `"abandoned"` as the status instead.
