---
name: patch_portfolio
description: |
  Update specific SLU ratings while inheriting the rest from the most
  recent full or patch assessment. Use when one or several life units
  have shifted but the user hasn't re-evaluated the whole portfolio.
  Creates a new session marked `patch`, with explicit lineage to the
  session whose unchanged ratings were carried forward.

  Triggers: "/patch", "/patch-portfolio", "update a few SLUs", "I want
  to update my ratings for X and Y", "my satisfaction with Z changed",
  "patch the portfolio", "partial reassessment".

  Not for: a single-SLU change with no portfolio claim (use
  quick_update_slu); a full reassessment of every SLU (use
  assess_life_portfolio); diagnostic scanning over existing data (use
  scan_life_units).
---

# Patch Portfolio

## Before you begin

If you haven't this session, read `constitution://main` once.
Particularly relevant: the parts about honesty over convenience — a
patch is honest because it explicitly says *what was inherited and
what was rewritten*. Don't let the user collapse that distinction.

The user wants to update some SLU ratings without redoing the full
assessment. Your job is to walk them through the changes, carry
forward everything else from the most recent full or patch session,
and record the result as a patch session.

## Begin the operation

Call `state_begin_operation("patch_portfolio")` and hold the returned
`operation_id` for use in this skill's other tool calls. The server
uses this to group everything you do in this flow into a single
auditable operation in the trace log.


## 1. Load the baseline

Call `state_get_latest_full_session()`. This returns the most recent
full-or-patch session and its ratings.

If no prior session exists, this skill doesn't apply — there's
nothing to inherit from. Tell the user gently:

  *"Cairn doesn't have a prior portfolio assessment to patch from. Run
  `assess_life_portfolio` for the full intake first."*

Stop here in that case.

## 2. Ask which SLUs to update

Show the user the SLUs and their current values (from the baseline) in
a compact form, organized by SLA:

  Relationships:
    Significant other — importance 9, satisfaction 6, 12h/week
    Family — importance 8, satisfaction 7, 8h/week
    Friendship — importance 7, satisfaction 7, 5h/week
  …

Then ask:

  Which units do you want to update? You can list several.

The user names one or more. If their input is ambiguous, ask once.

## 3. Start the patch session

Call `state_start_assessment_session("patch",
inherited_from_session_id=<baseline session id>)` and hold the
returned `session_id`.

## 4. Walk through each updated SLU

For each SLU the user named:

1. Show the current values from the baseline.
2. Ask for new importance (or *"same"*), satisfaction (or *"same"*),
   hours/week (or *"same"*), notes (optional, can be skipped).
3. For any field the user says *"same"* on, carry the baseline value
   forward.
4. Call `state_record_slu_rating(session_id, life_unit_id, importance,
   satisfaction, hours_per_week, notes, session_type="patch",
   inherited_from_session_id=<baseline session id>)`.

If the user wants to mark an SLU as "no longer applicable" (something
that was active in the baseline), call `state_deactivate_slu` and
don't record a rating for it in this patch.

If the user wants to *reactivate* an SLU that was inactive in the
baseline, they'll need to give all three values from scratch — there's
no baseline to inherit from for an SLU that wasn't rated last time.

## 5. Carry forward the rest

For every SLU not named by the user (and still active), copy its
baseline rating into the new session via the same
`state_record_slu_rating(..., session_type="patch",
inherited_from_session_id=<baseline>)` call — same `importance`,
`satisfaction`, `hours_per_week`, and `notes` values as the baseline.

This is what makes it a real session rather than a partial one: every
active SLU has a rating in the new session, even if most were
inherited. The storage layer's session_type and
inherited_from_session_id fields preserve the lineage so trajectory
analysis stays honest.

## 6. Show the updated portfolio

Call `state_compute_portfolio_quadrants(session_id)` and present the
result the same way `assess_life_portfolio` does. Especially highlight
any quadrant shifts caused by the updates:

  Patch recorded. Changes from the prior assessment:

  Physical health: needing attention → going well (satisfaction 4 → 7)
  Job and career: going well → going well, but satisfaction dropped 9 → 7

  Updated portfolio:
    Upper-right (going well): …
    Upper-left (needing attention): …
    …

## 7. Close

One line:

  Patch saved. The scanner will pick up these changes on its next run.

## Notes on judgment

- A patch is honest only if it claims to update only what changed. If
  the user starts asking to "update" 12 of 16 SLUs, suggest they run
  a full assessment instead — the patch lineage stops being meaningful
  when most of the data is replaced.
- Don't auto-detect "drift" during a patch. The user is telling you
  what changed; trust them. The scanner can suggest other SLUs to
  look at on its own cadence.
- Inheriting forward includes notes. If the user wants to clear a
  note, ask them to overwrite it explicitly with an empty string —
  don't strip it by default.


## Close the operation

Call `state_end_operation(operation_id, "completed", notes="<one-line
summary>")` to mark the flow complete. The notes are a brief summary
of what actually happened — *"3 drafts committed"*, *"no patterns
crossed threshold"*, *"4 capabilities added"* — and they're what
appears next to this operation in the trace view. If the flow ended
without completing, pass `"abandoned"` as the status instead.
