---
name: review_ideas
description: |
  Walk through ideas — especially cooling ones — and mature each: keep
  exploring, park as someday, promote to a project, or release. This is
  the anti-rot mechanism: ideas move or die.

  Triggers: "/review-ideas", "review my ideas", "go through my ideas",
  "tend my idea garden", "what ideas have gone cold", "mature my ideas",
  "clean up my idea list", "promote/park/release ideas".

  Not for: capturing a brand-new idea (use capture_idea); evaluating
  whether an idea is viable via research (use opportunity_research);
  triaging the assistant's proactive suggestions (use inbox). Review is
  about disposition, not analysis.
---

# Review Ideas

The user is doing the maturation pass. The goal is that no idea sits
forgotten — each gets a deliberate disposition. Be patient and
non-pushy: parking an idea as "someday" is a perfectly good outcome.

## Before you begin

If you haven't this session, read `constitution://main` once. This is
garden-tending, not queue-processing: some things you nurture, some you
let go, none of it is failure. Hold that tone throughout.

## 0. Begin the operation

Call state_begin_operation("review_ideas") and hold the operation_id.

## 1. Decide the scope

Ask what the user wants to review, or infer from how they invoked it:
- Cooling ideas only (the default for a maintenance pass) →
  state_list_cooling_ideas()
- All active ideas (spark + exploring + someday) →
  state_list_ideas(status="active")
- A specific idea by name

If reviewing cooling ideas and there are none, say so plainly: "No ideas
have gone cold. Nothing to review." Then stop.

## 2. Walk through each

For each idea, show it with its age and heat:

  "Bio fuse design studio" — captured 8 months ago, untouched since.
  "Interior fusing into the exterior, boutique studio."

Then offer the dispositions:
  - Keep exploring (it's still alive; just touch it)
  - Park as someday (deliberately set aside, no nagging)
  - Promote to a project (it's ready to become real work)
  - Release (let it go)

Take the user's choice:
  - Keep exploring → state_update status to "exploring" (this also
    touches the idea)
  - Park as someday → state_update status to "someday" (also touches)
  - Promote → state_promote_idea(idea_id). Then mention the new project
    exists and they can flesh it out (kill criteria, etc.) via capture or
    directly. Optionally help them set those now.
  - Release → state_release_idea(idea_id, reason?). Ask for a brief reason
    if the user offers one; don't force it.

(state_update on an idea already bumps last_touched_at, so a separate
state_touch_idea call isn't needed for status moves; use state_touch_idea
when the user revisits an idea without changing its status.)

## 3. Don't rush, don't pad

Process ideas one or a few at a time. Don't present 30 at once. If there
are many cooling ideas, batch them and let the user work through at their
pace.

Resist the urge to advocate. If the user wants to release an idea, release
it — don't argue for keeping it. If they want to keep something you'd have
released, that's their call.

## 4. Close the operation

Call state_end_operation(operation_id, "completed",
  notes="reviewed N ideas: X promoted, Y parked, Z released").

## Notes on judgment

- Releasing ideas is healthy, not a loss. A review session that releases
  several dead ideas did its job. Don't treat the idea count going down
  as a failure.
- Promotion is a real threshold. When an idea is promoted, it becomes a
  project with all the weight that implies (kill criteria, attention,
  the scanner watching it). Make sure the user actually means to commit,
  not just that the idea is interesting.
- Parking as someday is the pressure valve. It lets the user keep an idea
  alive without the scanner nagging. Offer it freely.
- Don't evaluate viability here either. If the user wants to know whether
  an idea is worth pursuing, that's opportunity_research, not review.
  Review is about disposition, not analysis.
