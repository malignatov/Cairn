---
name: define_great_life
description: |
  Foundational PERMA-V intake — walks the user through Seligman's six
  dimensions (Positive emotions, Engagement, Relationships, Meaning,
  Achievement, Vitality) capturing importance (0–10) and satisfaction
  (0–10). Output is the user's structured definition of a good life that
  orients the rest of the system. Takes 10–15 minutes; user must be present.

  Triggers: "/define great life", "/values intake", "set up my values",
  "tell the system what matters to me", "do the values intake",
  "PERMA-V check-in", "yearly values review", first-time setup of the
  assistant.

  Not for: per-life-area importance/satisfaction ratings across the 16
  SLUs (use assess_life_portfolio); diagnostic scanning on existing data
  (use scan_life_units).
---

# Define a Great Life

## Before you begin

If you haven't this session, read `constitution://main` once. It sets the
tone the rest of this skill assumes — particularly the parts about
treating this as reflective work, not transactional, and about loyalty
to who the user said they wanted to be while staying open to who
they're becoming.

This is the first foundational step in the user's strategic process. They
are telling you what they want their life to be aimed at, not what it
currently is. The output is a structured statement of their definition.

Open warmly. This is reflective work, not transactional. Some users will
find these questions easy; others will sit with them. Both are fine.

## Begin the operation

Call `state_begin_operation("define_great_life")` and hold the returned
`operation_id` for use in this skill's other tool calls. The server
uses this to group everything you do in this flow into a single
auditable operation in the trace log.


## 1. Set up the session

Call state_start_assessment_session("great_life") and hold the returned
session_id throughout this skill.

### Optional: prior signals as context

If the caller has provided **prior signals** — things the user has
previously written or said that may relate to PERMA-V (journal
excerpts, recurring themes in past chats, fragments from a seed
extraction) — read them before you begin. Use them as gentle
memory-jogs during the conversation, not as ratings.

A good use:

> "You've written in the past about wanting more deep work in your
> mornings — how does that map onto how you'd score Engagement today?"

A bad use:

> "Based on what you've written, I'd give your Engagement an 8 — does
> that sound right?"

The user provides every rating themselves. Prior signals only widen
the aperture so the user remembers things they care about that they
might otherwise forget to mention. If no prior signals are provided,
ignore this section.

## 2. Frame the dimensions

Briefly introduce PERMA-V — six aspects of a flourishing life from
positive psychology. Don't lecture. One or two sentences per dimension
is enough:

- **Positive emotions** — pleasure, joy, contentment in daily life
- **Engagement** — flow, absorption, being deeply involved in something
- **Relationships** — close, meaningful connections with others
- **Meaning** — connection to something larger; a sense of purpose
- **Achievement** — mastery, progress, visible accomplishment
- **Vitality** — health, energy, physical wellbeing

## 3. Walk through each dimension

For each of the six dimensions, ask three things:

1. On a scale of 0 to 10, how important is this to your idea of a great
   life? (0 = doesn't really matter to me; 10 = central to the life I want)
2. On a scale of 0 to 10, how satisfied are you with this dimension
   right now? (0 = not at all; 10 = exactly as I'd want)
3. (Optional) One sentence — what does this look like in practice for you,
   or what would you change?

After each answer, call state_record_perma_rating(session_id, dimension,
importance, satisfaction, notes?).

Don't rush. If the user is thinking, let them think. If they're uncertain
between two numbers, suggest they go with the lower one — most people
inflate scores under social pressure.

## 4. Summarize

Once all six are recorded, show the user a clean summary:

  Your definition of a great life:
    Importance ranking (most → least): ...
    Biggest gaps (importance - satisfaction):
      1. [Dimension]: importance N, satisfaction M, gap = N-M
      2. ...

The biggest gaps are the priority signals for the rest of the system.
Surface them gently:

  These three dimensions are where what matters most to you and how it's
  going are furthest apart. They're worth special attention as you go
  through the rest of the strategic process.

Do not propose actions here. This skill captures definition; action
happens in later skills (assess_life_portfolio, scan_life_units, capture).

## 5. Close

One line:

  Definition recorded. Re-run this once a year or after major life events
  — values evolve, and the system should know when yours have.


## Close the operation

Call `state_end_operation(operation_id, "completed", notes="<one-line
summary>")` to mark the flow complete. The notes are a brief summary
of what actually happened — *"3 drafts committed"*, *"no patterns
crossed threshold"*, *"4 capabilities added"* — and they're what
appears next to this operation in the trace view. If the flow ended
without completing, pass `"abandoned"` as the status instead.
