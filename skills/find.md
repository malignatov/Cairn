---
name: find
description: |
  Pull together everything Cairn knows about a topic, person, or place.
  Runs a lexical search across all indexed entities, groups results by
  type, and offers to synthesize how the user's thinking on the topic
  has evolved over time. Read-only — never proposes changes or actions.

  Triggers: "/find <query>", "/search <query>", "what do I have on X",
  "show me everything about Y", "pull up Z", "what's Cairn got on …",
  user wants to read across entities on a topic.

  Not for: free-form Q&A about the data (this returns matches, not
  answers); proposing new entities (use capture); proactive surfacing
  (use opportunity_research or scan_life_units); per-entity-type lists
  (use state_query directly).
---

# Find

## Before you begin

If you haven't this session, read `constitution://main` once. The
parts about tone matter here — this skill is conversational, and how
results are presented determines whether the surface feels like a
librarian or a database dump.

The user wants to see what Cairn has on some topic. This skill does
the search and presents results in a way that's useful to read.

## Begin the operation

Call `state_begin_operation("find")` and hold the returned
`operation_id` for use in this skill's other tool calls. The server
uses this to group everything you do in this flow into a single
auditable operation in the trace log.


## 1. Take the query

The user invoked `/find` followed by a query (a word, phrase, name,
place, project name). Take the query as-is. If they didn't supply
one, ask.

## 2. Run the search

Call `state_search(query)` with no `entity_types` filter. Take the
top 20–30 results.

## 3. Group and present

Group results by `entity_type`. Order types by likely user interest:
projects, decisions, commitments first; captures, sources next;
everything else last.

Render concisely:

  Found 14 things about "Valencia":

  Projects (2):
    · Move to Valencia (active, 6 months in)
    · Surf Bar partnership (snoozed)

  Decisions (5):
    · Choose Valencia over Lisbon (Sept 2025) — "climate and cost"
    · Rent before buying (Oct 2025) — "want a year on the ground first"
    · ... (3 more)

  Captures (4):
    · From PDF "valencia-trip-notes.pdf" (Aug 2025)
    · ... (3 more)

  Stakeholders (2):
    · Maria — Valencia-based partner, last contact 3 months ago
    · ... (1 more)

  Observations (1):
    · slu_observation: silence on family SLU since Valencia decision

Keep titles compressed. Show dates. Don't show full content — the user
can ask to drill into anything.

## 4. Offer synthesis

After the list, ask:

  Want me to pull these together into a thread of how your thinking on
  Valencia has evolved? Or drill into any of these specifically?

If yes, fetch full entity bodies for the most relevant items via
`state_query` and write a synthesized narrative — chronological,
showing how decisions built on each other and where the user's
thinking shifted.

This synthesis is read-only. Don't propose changes or actions.

## 5. Optional: surface gaps

If the synthesis reveals something the system should know but doesn't
— e.g., a project mentioned in captures but never created as a project
entity — offer to capture it now. But don't do this proactively; only
if the user asks "what's missing."

## Notes on judgment

- Search returns lexical matches, not semantic ones. A search for
  "ocean" won't find "Mediterranean" unless that exact word appears.
  Mention this if the user seems to expect otherwise.
- If results are sparse, suggest related queries the user could try.
- Be honest when there's little to show: *"Cairn doesn't have much
  about this yet"* is more useful than padding.
- Phrase queries are the default — *"Surf Bar Valencia"* searches for
  the whole phrase. To use Boolean operators, type them explicitly:
  *"Valencia OR Lisbon"*, *"sailing AND winter"*, or *"valenc*"* for
  prefix matches.


## Close the operation

Call `state_end_operation(operation_id, "completed", notes="<one-line
summary>")` to mark the flow complete. The notes are a brief summary
of what actually happened — *"3 drafts committed"*, *"no patterns
crossed threshold"*, *"4 capabilities added"* — and they're what
appears next to this operation in the trace view. If the flow ended
without completing, pass `"abandoned"` as the status instead.
