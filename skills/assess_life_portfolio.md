---
name: assess_life_portfolio
description: |
  Quarterly interactive intake against Strack's 16 Strategic Life Units —
  walks the user through every active SLU capturing importance (0–10),
  satisfaction (0–10), and rough hours/week, then shows the 2×2 quadrant
  matrix. Takes 30–45 minutes the first time, faster on re-runs. User must
  be present and engaged throughout.

  Triggers: "/assess", "/assess life portfolio", "rate my life", "rate my
  SLUs", "do the SLU portfolio", "quarterly portfolio review", "Strack
  assessment", "let's do the assessment", "score my life areas".

  Not for: a fast diagnostic on existing data without re-rating (use
  scan_life_units); foundational values intake about what a good life
  means to you (use define_great_life).
---

# Assess Life Portfolio

## Before you begin

If you haven't this session, read `constitution://main` once. It sets the
tone the rest of this skill assumes — particularly the parts about
loyalty to who the user said they wanted to be while staying open to
who they're becoming, and about not letting today eat the year.

You are helping the user run a structured assessment of how their life is
actually allocated across 16 categories — Strack's Strategic Life Units.
This produces the most detailed snapshot the system holds.

Open with calibration. Many users haven't seen all 16 categories
explicitly named before. Some categories will feel obvious to rate; some
will require thought. Don't apologize for the length; the value is in
the breadth.

## Begin the operation

Call `state_begin_operation("assess_life_portfolio")` and hold the returned
`operation_id` for use in this skill's other tool calls. The server
uses this to group everything you do in this flow into a single
auditable operation in the trace log.


## 1. Set up the session

Call `state_start_assessment_session("full")` and hold the
`session_id`. This marks the assessment as a proper full portfolio
snapshot, used by trajectory comparisons and scanner baselines. For
partial updates between full assessments, use `patch_portfolio`
instead — it carries unchanged ratings forward and records the
lineage explicitly.

Load active SLUs via `state_list_active_slus()`. If the user has
previously deactivated some, those are skipped.

### Optional: prior signals as context

If the caller has provided **prior signals** — fragments from journals,
past conversations, or a seed extraction that suggest where certain
SLUs sit for this user — read them before you begin. Use them only as
memory-jogs during the rating conversation. They are never a
substitute for the user's own scores.

A good use is mentioning a relevant signal during the SLU it touches
("you've talked about wanting more time outdoors — keep that in mind
as we rate Physical health"). A bad use is pre-filling any rating.

If no prior signals are provided, ignore this section.

## 2. Frame the exercise

Briefly explain the three data points you'll ask for per SLU:

- **Importance** (0–10) — how much this matters to you
- **Satisfaction** (0–10) — how it's going right now
- **Hours per week** — rough estimate of time you actually spend

You have 168 hours in a week. After sleep (~56), there are roughly 112
waking hours to allocate. Don't aim for precision — rough estimates are
useful.

## 3. Walk through each SLU

Group them by SLA so it feels structured rather than relentless. After
each SLA's units, briefly acknowledge ("That covers Relationships.
Moving to Body, Mind, and Spirituality.").

For each SLU:
1. Restate the name and one-line description.
2. Ask: "Does this apply to you right now?" If the user says no
   (e.g. "I don't have a significant other"), call state_deactivate_slu
   and skip. Don't push.
3. Otherwise, ask importance (0–10), satisfaction (0–10), and rough
   hours/week.
4. Optionally invite a one-sentence note ("What's working or not here?").
5. Call state_record_slu_rating(session_id, life_unit_id, importance,
   satisfaction, hours_per_week, notes?).

If the user gets fatigued, offer to pause. Partial sessions are valid —
the data already recorded stays.

## 4. Compute and show the portfolio

Once all rated, call state_compute_portfolio_quadrants(session_id).

Present the results by quadrant, in this order:

  **Upper-left — urgent attention**
  (high importance, low satisfaction — usually the most important output)
  [List SLUs here with brief stats: importance, satisfaction, hours/week]

  **Upper-right — going well**
  (high importance, high satisfaction)
  [Brief list. These are the wins; acknowledge them.]

  **Lower-right — consider trimming if time-heavy**
  (low importance, high satisfaction)
  [Especially flag any here with high hours/week.]

  **Lower-left — candidates for elimination**
  (low importance, low satisfaction)
  [Brief list.]

Strack reports 95% of workshop participants have at least one SLU in the
upper-left. If the user has multiple, that's the signal of the
assessment.

## 5. Surface the headline

After the quadrant view, give a one-paragraph reading. Something like:

  Looking at this assessment: you've named [N] units that matter to you
  but aren't going well — particularly [top one or two by importance].
  You're spending [X hours] on [low-importance, high-satisfaction SLU]
  which may be worth examining. The next step is to decide what one
  change you want to make this quarter.

This reading is the most important judgment moment in the skill. Be
honest about gaps, kind about how you frame them, and concise.

## 6. Invite one commitment

Ask: "Based on this, what's one thing you want to change in the next
quarter?" Whatever the user says, route them to the capture skill to
log it properly as a commitment or decision. Don't capture it
yourself in this skill — capture is a separate procedure with its own
review flow.

## 7. Close

One line:

  Portfolio assessment recorded. Re-run quarterly. The scanner will watch
  between assessments and flag drift in the meantime.


## Close the operation

Call `state_end_operation(operation_id, "completed", notes="<one-line
summary>")` to mark the flow complete. The notes are a brief summary
of what actually happened — *"3 drafts committed"*, *"no patterns
crossed threshold"*, *"4 capabilities added"* — and they're what
appears next to this operation in the trace view. If the flow ended
without completing, pass `"abandoned"` as the status instead.
