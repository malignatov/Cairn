---
name: trace
description: |
  Browse Cairn's logs of what the LLM has done — recent chats, recent
  operations, abandoned operations, errors, or a single operation's
  full timeline. Read-only. Use when the user wants to audit or debug
  what just happened, not when they want analysis or summaries.

  Triggers: "/trace", "/audit", "/log", "what did you just do", "show
  me the log", "show me errors", "what did capture do", "trace last
  chat", "what's stuck", "any abandoned operations".

  Not for: free-form Q&A about the data (use find); proposing changes
  (use the relevant capture / patch / quick_update skill); aggregations
  or metrics (not built — logs are raw timelines, not dashboards).
---

# Trace

## Before you begin

If you haven't this session, read `constitution://main` once.
Particularly relevant: logs hold the user's data verbatim. Don't dump
raw payloads casually — summarize unless the user asks for raw.

## 0. Begin the operation

Call `state_begin_operation("trace")` and hold the returned
`operation_id` for use in this skill's other tool calls.

## 1. Parse the request

The user invokes `/trace` with a modifier. Map the request to one of:

- *(no modifier)* → most recent operation, full timeline
- *"last chat" / "last conversation"* → most recent chat with all operations
- *"abandoned"* → operations stuck `in_progress` or auto-closed
- *"errors"* → recent error tool calls
- *"<skill_name>"* (e.g. *"capture"*, *"scan"*) → recent operations of that skill
- *"<uuid-looking-id>"* → specific operation or chat by id
- *"large"* → tool calls flagged with `payload_size_warning = true`

If the request is ambiguous, ask once.

## 2. Run the query

Use the appropriate tool:

- `state_list_recent_operations` / `state_list_recent_chats` for browse
  views.
- `state_get_operation_trace(operation_id)` for a single-operation
  drill-down (returns the operation row plus its tool_calls and
  resource_reads in order).
- `state_get_chat_trace(chat_id)` for a session-wide view.
- `state_list_abandoned_operations(stuck_for_hours?)` for the
  abandoned/stuck view.
- `state_list_errors(since?)` for the error view.

## 3. Render readably

For a single operation, render as a timeline:

  Operation: capture (completed)
  Started: 2025-11-15 09:14:22
  Duration: 3m 47s
  Notes: 3 drafts committed

  Timeline:
    09:14:22  read skill://capture (1247 bytes)
    09:14:24  state_list_active_slus()
              → 16 SLUs returned
    09:14:28  state_search("Valencia move")
              → 2 matches (project, decision)
    09:15:12  state_draft(entity_type=decision, …)
              → draft id: draft-abc
    …

Keep the rendering compressed. Don't dump full args/response JSON
inline unless the user asks to see a specific call's detail. Show an
argument summary (entity_type, key fields) and a response summary
(count, brief description).

For a chat view, list operations first, then loose tool calls:

  Chat from 2025-11-15 09:00 — ongoing

  Operations (3):
    · capture (completed, 3m 47s) — 3 drafts committed
    · scan_life_units (completed, 1m 12s) — 4 observations, 1 suggestion
    · find (in progress) — started 2 minutes ago

  Loose tool calls (5):
    · state_query(project) — 09:42
    · …

For an error view:

  4 errors in the last 7 days:
    2025-11-13  state_commit_draft(draft-xyz) — draft not found
    2025-11-14  state_write(decision, …) — validation: rationale required
    …

## 4. Offer drill-down

After a list view, offer:

  *Want to drill into a specific operation? Tell me the id or paste a
  detail.*

If the user asks to see a specific tool call's full args/response,
fetch the row from the operation trace and render the JSON
pretty-printed in a code block. Don't summarize when the user has
explicitly asked for the raw data.

## 5. Close the operation

Call `state_end_operation(operation_id, "completed",
notes="<what was queried>")`.

## Notes on judgment

- The model's deliberation between tool calls is not in the logs. If
  the user asks *"why did capture decide to propose 3 things instead
  of 4?"*, the honest answer is *"the logs show what was done, not
  what was thought."* Don't fabricate reasoning.
- Logs contain the user's data. Treat them with the same discretion
  as the entities themselves — if the user asks for trace output in
  a way that would dump sensitive content (e.g. asking for trace
  output to paste somewhere), summarize and check before pasting raw
  payloads.
- Logs grow over time. If the user notices their DB getting large,
  point at `state_purge_logs` as the manual cleanup option. Don't
  propose automatic retention — that's a deliberate user choice.
- Don't moralize about what's in the logs. The user is auditing their
  own system; you're showing them what's there.
