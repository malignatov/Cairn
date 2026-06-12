---
name: inbox
description: |
  Surface pending suggestions (proactive findings the assistant
  generated) and walk the user through accepting, rejecting, or
  deferring each. Rejection reasons are recorded and become signal for
  future research runs — a rejection without a reason is data lost.

  Triggers: "/inbox", "what's in my inbox", "show me pending suggestions",
  "what did you find", "let me review what came up", "triage the inbox",
  "what suggestions are waiting". Invoked automatically as the hand-off step
  at the end of opportunity_research and scan_life_units.

  Not for: capturing fresh user-said content (use capture); generating new
  suggestions (use opportunity_research or scan_life_units); reading
  canonical entities like projects or decisions (use state_query).
---

# Inbox

## Before you begin

If you haven't this session, read `constitution://main` once. It sets the
tone the rest of this skill assumes — particularly the parts about
spending credibility sparingly and about deferring being a legitimate
choice, not failure.

Show the user what's accumulated in their suggestion inbox and walk
them through dispositioning each one.

## Begin the operation

Call `state_begin_operation("inbox")` and hold the returned
`operation_id` for use in this skill's other tool calls. The server
uses this to group everything you do in this flow into a single
auditable operation in the trace log.


## 1. Load pending suggestions

Call state_list_pending_suggestions(). Sort by created_at descending — newest
first. If there are zero pending suggestions, say so plainly: "Inbox is
empty." Then stop.

## 2. Render concisely

Render suggestions grouped by kind:

  Opportunities (2):
    1. [one-line action]
       Anchors: project X, decision Y
       Why now: [one-line rationale]
       Evidence: [source url], [source url]

  Blindspots (1):
    2. [one-line action]
       Anchors: stated value Z vs. project portfolio
       Why now: [one-line rationale]

Keep each entry short. The user is reviewing, not reading a report.
If there are more than five pending, show the top five and tell the
user how many more remain.

## 3. Walk through disposition

For each visible suggestion, the user will respond with one of:

- "Accept N" → call state_disposition_suggestion(id, "accepted", reason?).
  Optionally ask if they want to turn this into a project, decision,
  or trigger. If yes, run the relevant flow (state_draft or
  state_write). If no, the suggestion is simply marked accepted —
  it's now in their awareness; they'll act on it as they choose.

- "Reject N: [reason]" → call state_disposition_suggestion(id, "rejected",
  reason). Always capture the reason. The system learns from
  rejections; a rejection without a reason is data lost.

- "Defer N" → call state_disposition_suggestion(id, "deferred"). Deferred
  suggestions stay in the inbox for future review.

- "Skip" / "Done" → end the flow with whatever's been processed.

## 4. Close

One-line summary:

  Inbox: 2 accepted, 1 rejected, 1 deferred. 3 remain pending.

Done.

## Notes on judgment

- Don't push the user to act on everything. Deferring is a legitimate
  choice; the suggestion isn't going anywhere.
- If a rejection reason reveals a pattern ("these compounding-adjacency
  suggestions are always too speculative for me"), name the pattern.
  Future research runs will read this and adjust.
- If the user accepts a suggestion and you create a downstream artifact
  (project, decision, trigger), link the new artifact's source back to
  the originating suggestion so the lineage is visible.


## Close the operation

Call `state_end_operation(operation_id, "completed", notes="<one-line
summary>")` to mark the flow complete. The notes are a brief summary
of what actually happened — *"3 drafts committed"*, *"no patterns
crossed threshold"*, *"4 capabilities added"* — and they're what
appears next to this operation in the trace view. If the flow ended
without completing, pass `"abandoned"` as the status instead.
