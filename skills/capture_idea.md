---
name: capture_idea
description: |
  Quickly capture a single idea — a spark, a thought, a what-if — without
  evaluating it. Fast and minimal. Use when the user floats something they
  want to keep but aren't committing to.

  Triggers: "/idea", "/capture-idea", "capture this idea", "jot this down",
  "save this spark", "keep this thought", "what if there were…", "I have an
  idea", "park this thought", "note this down for later".

  Not for: committing to build something (that's a project — use capture);
  evaluating whether an idea is viable or worth pursuing (use
  opportunity_research); reviewing or maturing ideas already captured (use
  review_ideas). Capturing is the whole job here — no evaluation.
---

# Capture Idea

The user has a spark they want to keep. Your job is to catch it cleanly
and get out of the way. This is a fast interaction — no evaluation, no
scoping, no "have you thought about whether this is viable."

## Before you begin

If you haven't this session, read `constitution://main` once — in
particular the restraint it asks for. Capturing an idea is the lightest
touch in Cairn: catch it in the user's words and stop. Don't analyze.

## 0. Begin the operation

Call state_begin_operation("capture_idea") and hold the operation_id.

## 1. Catch the idea

Take what the user gives you. An idea usually has a kernel (the title)
and some texture (the body). Pull both out:

- title: a short, memorable handle for the idea.
- body: the fuller thought, in the user's words. Keep their phrasing.

If they gave you only a fragment, that's fine — capture the fragment.
Don't pad it or interpret it into something more developed than they said.

## 2. Stage it

Stage the idea via state_draft with entity_type = "idea", status =
"spark". Optionally suggest 1-2 SLU tags if the connection is obvious,
but don't force it — many ideas don't map cleanly to a life area.

Show the user what you caught:

  Caught: "Bio fuse design studio" — interior fusing into the exterior,
  boutique studio.
  Tagged: hobbies_and_interests (optional, can remove)

  Keep it?

## 3. Commit

On confirmation, commit via state_commit_draft. That's it.

## 4. Close the operation

Call state_end_operation(operation_id, "completed", notes="captured idea: <title>").

## Notes on judgment

- Do NOT evaluate the idea. Don't ask if it's viable, don't suggest next
  steps, don't research it. Capturing is the whole job. Evaluation happens
  later, deliberately, in review_ideas or opportunity_research.
- Do NOT promote it to a project. An idea is pre-commitment. If the user
  used clear commitment language ("I'm going to build X"), check: "Want
  this as an idea to sit with, or a project you're committing to?" Default
  to idea unless they're clearly committing.
- Keep their wording. "Bio fuse" stays "bio fuse." Don't standardize a
  spark into corporate-speak.
- Fast. This should feel like jotting on a napkin, not filling a form.
