---
name: define_principles
description: |
  Capture the user's ranked operating principles — the rules they evaluate
  their decisions against. Walks through each principle, its description,
  and its rank. No pre-seeding; the user brings their own. Can be re-run
  to add, rewrite, re-rank, or retire principles.

  Triggers: "/define-principles", "/principles", "set up my principles",
  "define my operating principles", "what do I want my decisions checked
  against", "rank my principles", "revise my principles", "re-rank my
  principles", "retire a principle", "the rules I live by".

  Not for: rating how life is going across wellbeing dimensions (use
  define_great_life / assess_life_portfolio — PERMA-V is descriptive,
  principles are prescriptive; different layers); capturing decisions or
  projects from a conversation (use capture); proactive research the user
  didn't ask for (use opportunity_research). This skill captures and ranks
  the prescriptive rules themselves; it never judges whether a principle
  is "good" — that is not the assistant's call.
---

# Define Principles

The user is articulating the principles they want their decisions checked
against. These are ranked — higher rank means higher priority when
principles conflict. This is reflective, deliberate work. Take it slowly.

## Before you begin

If you haven't this session, read `constitution://main` once. Principles are
the most morally loaded data in Cairn — the user's explicit statements about
how they want to live. The constitution sets the tone this work demands:
capture faithfully, never editorialize, and stay loyal to the version of the
user who articulated these principles while open to who they're becoming.

## 0. Begin the operation

Call state_begin_operation("define_principles") and hold the operation_id.

## 1. Check for existing principles

Call state_list_principles(). If principles already exist, this is a
revision session, not a first intake. Show the current ranked list and
ask what the user wants to do: add new ones, rewrite existing ones,
re-rank, or retire some.

If no principles exist, this is a first intake. Proceed to gather them.

## 2. Gather principles

Ask the user to share their principles. They can paste a list or go one
at a time. For each principle, capture:

- **name** — short, memorable.
- **description** — one or two sentences, in the user's own words. Don't
  paraphrase or "improve" their wording. Capture it as they say it.
- **provenance** (optional) — if the user notes where it comes from
  (a tradition, a person, personal reflection), record it.

Don't rush to rank yet. Get all the principles down first.

## 3. Establish the ranking

Once the principles are captured, work through the ranking. This is the
most important and most difficult part — ranking forces real choices.

Present the principles and ask the user to order them, highest priority
first. If they struggle (most people do), help by asking pairwise
questions: "When [principle A] and [principle B] conflict, which wins?"

The ranking must be a total order — every principle has a distinct rank.
If the user insists two are truly equal, gently push: the whole value of
ranking is resolving conflicts, and equal ranks can't resolve. Ask which
would win "if you absolutely had to choose." Record that.

## 4. Propose and commit

For each principle, propose it via the draft/commit flow with its rank.
Show the full ranked list before committing:

  Your principles, ranked:
    1. [name] — [description]
    2. [name] — [description]
    ...

On confirmation, write each via state_write("principle", ...) with its
rank (or commit the staged drafts). A "created" revision is logged
automatically for each, so the history starts clean.

When re-ranking existing principles, use state_rerank_principle(principle_id,
new_rank, rationale) — it shifts the others and logs the change. To retire
one, use state_retire_principle(principle_id, rationale). Plain edits to a
principle's wording go through state_update with a `rationale` in the patch.

## 5. Optional SLU tagging

For principles that clearly relate to specific life areas, propose SLU
tags. Most principles are cross-cutting (they apply everywhere), so don't
force tags. Tag only where there's a clear, narrow connection.

## 6. Close the operation

Call state_end_operation(operation_id, "completed",
  notes="<N principles defined/revised>").

## Notes on judgment

- Capture the user's wording faithfully. These are their principles, not
  yours. If their phrasing is idiosyncratic ("rectitude toward oneself
  and others"), keep it exactly.
- The ranking is the hard part and the valuable part. Don't let the user
  skip it. An unranked principle list is just a values statement; the
  ranking is what makes it a decision system.
- Re-ranking later is fine and expected. People's priorities shift. The
  revision log preserves the history, so changing the ranking isn't
  destructive — it's documented growth.
- Don't editorialize about the principles themselves. Whether a principle
  is "good" is not your call. Your job is to capture and rank, not judge.
