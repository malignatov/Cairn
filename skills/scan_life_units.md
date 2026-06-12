---
name: scan_life_units
description: |
  Diagnostic pass over the user's Strategic Life Unit (SLU) portfolio — reads
  existing assessments and tagged activity to surface blindspots; never elicits
  new ratings. Writes observations to the diagnostic log on every run and
  promotes only the highest-conviction ones to suggestions.

  Triggers: "/scan", "/scan life units", "find weak spots in my SLU(s)",
  "what am I neglecting", "where am I drifting", "spot blindspots",
  "check my life portfolio", "what's off balance", "where are the gaps".

  Not for: collecting new SLU importance/satisfaction ratings (use
  assess_life_portfolio); broader external research on adjacent opportunities
  (use opportunity_research); reviewing already-staged suggestions (use inbox).
---

# Scan Life Units

## Before you begin

If you haven't this session, read `constitution://main` once. It sets the
tone the rest of this skill assumes — particularly the parts about
recommending less rather than more, and about tone for sensitive domains.

You are running a diagnostic pass over the user's life portfolio. You
don't surface everything — observations are data, suggestions are alarms.
A good run produces several observations and zero-to-few suggestions.

## Begin the operation

Call `state_begin_operation("scan_life_units")` and hold the returned
`operation_id` for use in this skill's other tool calls. The server
uses this to group everything you do in this flow into a single
auditable operation in the trace log.


## 0. Check what data you actually have

Before computing signals, count your inputs:

- `state_list_assessments()` — how many distinct `session_id`s?
- `state_list_perma_ratings()` — at least one PERMA-V session?
- `state_list_entity_slu_links()` — how many links total, and how recent
  is the oldest one?

Then handle each cold-start case explicitly:

- **0 assessment sessions** → stop. Tell the user the scanner needs at
  least one portfolio assessment to work from. Point them at
  `skill://assess_life_portfolio` and exit. Do not invent signals.
- **1 assessment session** → trajectory signals (`erosion`,
  `quadrant_shift`) are mathematically impossible with one data point.
  Skip them entirely. In your closing summary, state plainly: "Trajectory
  analysis requires two or more assessments; with one on record I can
  only detect point-in-time signals."
- **0 PERMA-V ratings** → the orientation cross-check against the user's
  stated values is unavailable. Continue with portfolio-only signals.
  Mention in the closing summary that running `define_great_life` would
  let the scanner weight findings by stated values.
- **Oldest entity_slu_link is < 30 days old** → the "last 90 days"
  window doesn't yet have meaning; your tag distribution is likely an
  artifact of a seed extraction or first capture, not 90 days of
  accumulated behavior. Compute share-of-activity signals but note this
  caveat to the user — the picture will sharpen in three months.

## Tone calibration

Apply this before computing any severity, not at draft time. Blindspots
touching **health, relationships, finances, or meaning** demand gentler
framing than career or hobbies:

- Frame findings about these SLUs as questions, not statements.
- Acknowledge that life seasons legitimately reshuffle attention.
- Down-weight severity by one notch in these domains (a `high` becomes
  a `medium` candidate for promotion; a `medium` becomes `low`).
- Do not promote more than one sensitive-domain blindspot per run. If
  two fire, pick the more urgent and hold the other for next time.

## 1. Load context

For each active life_unit (`state_list_active_slus`):
- Most recent slu_assessment (importance, satisfaction, hours, quadrant).
- Trajectory: prior assessments if any (skip if you flagged single-session above).
- Tagged entities via `state_list_entity_slu_links(life_unit_id=...,
  since=<90d ago>)`. Count: how many entities touched this SLU recently.
- Cross-SLU comparison: each SLU's share of total tagged activity.

Also load:
- Most recent `state_list_perma_ratings()` if any.
- `state_list_observations()` for context — don't repeat yourself within
  the same 30-day window.
- `state_query("suggestion", {"kind": "blindspot"})` — especially
  rejected ones, to check the rejection-history rule below.
- `state_list_principle_departures(relationship="unjustified_departure",
  since=<90d ago>)` — for the principle-drift pass in step 2a. Group the
  results by `principle_id`. (If the user has no principles, this returns
  nothing and step 2a is a no-op.)

## 2. Compute signals per SLU

For each SLU, check these patterns and write observations via
`state_write_observation`. **All severities below are pre-tone-adjustment**
— apply the tone calibration above before deciding promotions.

- **silence**: high-importance SLU with zero tagged activity in last 90 days.
    - imp ≥ 8 AND sat < 4 AND zero tags → severity = **high**
    - imp ≥ 6 AND sat < 6 AND zero tags → severity = **medium**
    - imp ≥ 6 AND sat ≥ 6 AND zero tags → severity = **low**, do not promote
    - The satisfaction gate matters. A high-importance SLU with zero
      activity AND high satisfaction usually means the user is happy
      with that area and simply hasn't talked about it to the system.
      That's a capture artifact, not a blindspot. Recording it as `low`
      keeps the audit trail without producing noise in the inbox.

- **erosion**: satisfaction has dropped 2+ points between the last two
  assessments. Severity = medium; high if also importance ≥ 8.
  (Skip entirely if you have fewer than 2 assessment sessions.)

- **mismatch**: importance ≥ 7 AND this SLU's share of total tagged
  activity is in the bottom third (relative to other active SLUs).
  Severity = medium; high if importance ≥ 9.

- **avoidance**: SLU has 2+ unresolved decisions (revisit_at in the past,
  no outcome logged). Severity = medium.

- **quadrant_shift**: SLU moved from upper-right to upper-left between
  the two most recent **full-or-patch** sessions. Severity = high.
  Skip entirely if there are fewer than 2 such sessions. **Ignore
  `quick_update` rows for this signal** — a casual in-the-moment
  rating change shouldn't trigger portfolio-level alarms. Quick
  updates are visible to the per-SLU trajectory but not to this
  signal.

- **compound_debt**: SLU has 3+ unresolved decisions OR 3+ suggestions
  rejected without progress. Severity = medium.

Write every observation that fires, including `low` ones, with `evidence`
pointing to the specific data (entity IDs, assessment IDs, numbers).

## 2a. Check principle drift (v7)

This pass is principle-scoped, not SLU-scoped. Using the departures you
loaded in step 1 (grouped by `principle_id`):

- **repeated_unjustified_departure**: a principle has 2+ unjustified
  departures (relationship = unjustified_departure) recorded in the last
  90 days. Severity scales with the principle's rank: top-third principle
  → high; middle → medium; bottom → low.

  When this fires, the suggestion should be genuinely open about which
  thing is wrong — the behavior or the principle:

    "You've departed from principle #2, [name], three times in two months,
    each time without invoking a higher principle. Either your decisions
    are drifting from a value you hold central, or this principle isn't
    really ranked #2 for you anymore. Worth examining which."

  Do NOT surface single departures. Life happens. Patterns matter.
  Do NOT surface justified overrides as problems — those are the
  hierarchy working correctly.

  Write the promotion as a `suggestion` of `kind = blindspot`, anchored
  via `linked_evidence.internal_anchors` to the principle
  (`entity_type = "principle"`, the principle's id) and to each departing
  decision (`entity_type = "decision"`). `external_sources` is empty for
  this kind. Apply the same sensitive-domain tone calibration above if the
  principle touches health, relationships, finances, or meaning.

## 2b. Check cooling ideas (v8)

The ideas layer is where the system is **most patient**. Its job is to
make sure ideas don't rot invisibly — not to pressure them toward
resolution. A good idea sometimes needs a year of quiet before its moment.

- **cooling_idea**: an idea in `spark` or `exploring` status, untouched
  for 6+ months. Use `state_list_cooling_ideas()`. Severity: **low** —
  ideas cooling is normal, not alarming. Never flag `someday` ideas; those
  were deliberately parked (the query already excludes them).

  Promote to a suggestion only on a SLOW cadence — surface cooling ideas
  no more than quarterly, and only after the 6-month dormancy threshold.
  A single cooling idea is not worth a suggestion; surface them in a batch
  when several have gone cold, framed as an invitation to review:

    "A handful of ideas have gone quiet — 'bio fuse design studio' and two
    others haven't been touched in over six months. Worth a quick review
    to promote, park, or release them? No rush."

  The suggestion (`kind = pattern`, anchored to the cooling ideas via
  `entity_type = "idea"`) should route the user toward `review_ideas`, not
  disposition the ideas itself. If only one idea is cooling, hold it for a
  future run rather than nagging about a single spark.

## 3. Decide what to surface

Observations are silent by default. Promote to a `suggestion` only if:

- Post-tone-calibration severity is `high`, OR
- The same SLU has 3+ medium observations in the last 90 days, OR
- Post-tone-calibration severity is `medium` AND the SLU is in the
  user's top three by importance.

`low` severity observations are never promoted; they live in the
diagnostic log for the next run to reason about.

For each promotion, write a `suggestion` of `kind = blindspot`. The
content must:
- Name the SLU specifically.
- State the pattern in one sentence, without judgment.
- Cite the specific evidence (numbers, IDs, dates).
- Frame as a question or invitation, not a conclusion.

Example good content:

  Significant other is your highest-importance SLU (9) but in the last
  90 days only 3% of your tagged activity touches it. The "plan trip"
  decision from August is still unresolved. Worth a check-in — is this
  a chosen season of focus elsewhere, or has the calendar quietly
  redirected?

Example bad content (do not write this):

  You are neglecting your partner.

Anchor every suggestion to internal data via
`linked_evidence.internal_anchors`. Use these conventional entity_types
for anchors so they're queryable later:
- `life_unit` (the SLU under examination — always include)
- `slu_observation` (the observation that triggered this — always include)
- `project`, `decision`, `commitment` (any specific entity cited)

`external_sources` can be empty for `blindspot` kinds.

## 4. Check rejection history

Before writing any blindspot suggestion, check past rejected suggestions for
the same SLU. If a substantially similar suggestion was rejected with a
reason that still applies (e.g. "I've consciously deprioritized this
during caregiving"), suppress the new suggestion and write an observation
instead, noting that the system considered surfacing it but didn't.

## 5. Hand off to inbox

**Stop here.** Do not list, summarize, or describe the suggestions you
wrote — that's the inbox skill's job and mixing them produces a worse
review surface for the user.

**Close this operation first**, then hand off:

1. Call `state_end_operation(operation_id, "completed", notes="<N
   observations, M suggestions promoted>")` to close the scan
   operation cleanly. The inbox flow that follows is the user's own
   operation; mixing it with the scan's would confuse the trace.
2. Then read `skill://inbox` and execute it. The inbox skill calls
   its own `state_begin_operation` and the inbox picks up everything
   you just wrote.

## 6. Close

After the inbox handoff completes, output one line:

  Scan complete. [N] observations recorded. [M] suggestions in the inbox.
  [Note any cold-start caveats from step 0 here.]

## Notes on judgment

- Silence is a valid output. If nothing crossed promotion thresholds,
  the right answer is "no suggestions this run."
- Don't reach. If you find yourself constructing a story to justify a
  suggestion, the suggestion isn't ready.
- The scanner's value compounds over time. Early runs (single
  assessment, no PERMA-V, recent tags only) produce thinner findings on
  purpose; the trajectory data needed for strong suggestions builds
  across quarters.
- If the cold-start caveats from step 0 mean only one or two signals
  can fire, say so plainly in the closing summary. A scanner that
  pretends to be working with full data is worse than one that is
  honest about its limits.


## Close the operation

Call `state_end_operation(operation_id, "completed", notes="<one-line
summary>")` to mark the flow complete. The notes are a brief summary
of what actually happened — *"3 drafts committed"*, *"no patterns
crossed threshold"*, *"4 capabilities added"* — and they're what
appears next to this operation in the trace view. If the flow ended
without completing, pass `"abandoned"` as the status instead.
