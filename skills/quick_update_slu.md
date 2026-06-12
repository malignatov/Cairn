---
name: quick_update_slu
description: |
  Record a single SLU rating change as a standalone update, without
  creating a new portfolio session. Useful for noting in-the-moment
  changes between formal assessments. Visible in per-SLU trajectory
  views; excluded from portfolio snapshot comparisons and from the
  scanner's quadrant_shift signal.

  Triggers: "/quick-update", "/quick-update-slu", "bump <SLU>", "my
  <SLU> satisfaction is up", "update just <SLU>", "rate just one
  life unit", in-the-moment rating change.

  Not for: a multi-SLU update (use patch_portfolio); a full
  reassessment (use assess_life_portfolio); reviewing the portfolio
  (use compute_portfolio_quadrants directly or invoke scan_life_units).
---

# Quick Update SLU

## Before you begin

If you haven't this session, read `constitution://main` once.
Particularly relevant: the parts about staying small when the user is
asking for something small. This skill is the inverse of ceremony.

The user wants to record a rating change for one Strategic Life Unit
without claiming they re-evaluated the whole portfolio. Your job is
fast and minimal.

## Begin the operation

Call `state_begin_operation("quick_update_slu")` and hold the returned
`operation_id` for use in this skill's other tool calls. The server
uses this to group everything you do in this flow into a single
auditable operation in the trace log.


## 1. Identify the SLU

If the user said which SLU in their request, use it. Otherwise ask
which one. Don't show the full list unless asked — they probably know.

## 2. Get the values

Ask for importance (0–10), satisfaction (0–10), and optionally
hours/week and a one-line note. Default to skipping the optional
fields.

If they're updating only one of importance/satisfaction, ask if the
other has also changed. If not, fetch the most recent rating for this
SLU via
`state_list_assessments(life_unit_id=<id>)` and use that value to fill
the unchanged dimension. (The list returns newest first; take the
first row.)

## 3. Record

Call `state_record_quick_slu_update(life_unit_id, importance,
satisfaction, hours_per_week?, notes?)`. This writes a single rating
row with `session_id = null` and `session_type = "quick_update"`.

## 4. Close

One short line confirming the change:

  Physical health: satisfaction 6 → 7. Recorded.

If this update causes the SLU's quadrant to shift, mention it:

  Physical health: satisfaction 6 → 7. Quadrant moved from
  needing-attention to going-well.

Don't go further. This is a small, casual interaction. No portfolio
view, no summary stats, no "what moved" prose.

## Notes on judgment

- Don't propose other SLUs to update. The user came for one thing.
- If the user says something that sounds like multiple SLU changes
  (*"my health is up but my work is down"*), suggest `patch_portfolio`
  instead. Quick updates are strictly single-SLU.
- A `quick_update` is not a portfolio snapshot. The scanner sees the
  new rating but the trajectory view treats it as a point in time,
  not as a new session. The scanner's `quadrant_shift` signal does
  not fire for quick-updates by design.


## Close the operation

Call `state_end_operation(operation_id, "completed", notes="<one-line
summary>")` to mark the flow complete. The notes are a brief summary
of what actually happened — *"3 drafts committed"*, *"no patterns
crossed threshold"*, *"4 capabilities added"* — and they're what
appears next to this operation in the trace view. If the flow ended
without completing, pass `"abandoned"` as the status instead.
