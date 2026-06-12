---
name: opportunity_research
description: |
  Autonomously generate strategic research questions from the user's
  existing memory (projects, decisions, values), investigate them with
  web search, and propose actionable items anchored to specific entities
  in the user's data. The user does not supply a topic — the data does.
  Output is a small number of high-conviction suggestions or zero (silence
  is a valid result).

  Triggers: "/opportunity-research", "/research", "find opportunities",
  "do outside-counsel research", "what should I be thinking about",
  "external scan", "strategic research", "what am I missing" (when
  external context is the question, not internal portfolio gaps).

  Not for: detecting gaps in existing SLU portfolio data (use
  scan_life_units); capturing user-said content from the current chat
  (use capture); reviewing already-staged suggestions (use inbox).
---

# Opportunity Research

## Before you begin

If you haven't this session, read `constitution://main` once. It sets the
tone the rest of this skill assumes — particularly the parts about
honest counsel often meaning "do less," about telling uncomfortable
truths sparingly, and about the seven-virtues balance that keeps
strategic input from sliding into advice-shaped noise.

You are doing strategic outside-counsel work. The user has not asked you
about anything specific. Your job is to identify what they should be
asking about based on what they're already doing, deciding, and saying
they care about — and to bring back grounded, actionable suggestions.

This is not a news scan. This is not a trend report. The output is a
small number of specific actions the user could take, each anchored to
their own data and supported by real external evidence where applicable.

## Begin the operation

Call `state_begin_operation("opportunity_research")` and hold the returned
`operation_id` for use in this skill's other tool calls. The server
uses this to group everything you do in this flow into a single
auditable operation in the trace log.


## 1. Build the operating context

Read from the MCP server:

- All projects with status = active or snoozed. For each: name, description,
  kill criteria, last_touched, recent linked decisions.
- All decisions from the last 90 days. Note which projects they touched and
  which have a revisit_at still in the future (unresolved).
- Any strategic entities present (purpose, vision, life domains, values).
  These may not exist yet — work with what's there.
- Recent capture activity — what has the user been talking about that
  isn't yet a project or decision?
- Any past suggestions, especially rejected ones. Note rejection reasons.
- The user's active ideas (spark, exploring, someday) via
  state_list_ideas(status="active"). Ideas are raw material for research —
  an idea the user logged casually may be worth investigating to see
  whether there's a real opening.

Form an internal model: what is this person currently invested in,
currently avoiding, currently questioning, currently weak on?

If the operating context is thin (no projects, no decisions), stop.
Tell the user the system doesn't have enough memory to work from yet,
and ask what they want to seed it with. Do not invent context.

### When the user's data is large

If the user has more than ~30 active projects + decisions combined,
don't fetch every entity individually — that's noisy and most of it
isn't relevant to any one research question. Instead, use
`state_search` to identify topical clusters. If a quick scan of
project names suggests several relate to "writing", run
`state_search("writing")` to pull all related entities in one call.
This gives a richer view of one topic area than fetching the top-N
most-recent entries.

For smaller data sets, continue fetching directly — search adds noise
when there isn't much to find.

## 2. Form research questions

Generate 3–7 candidate research questions. Each must tie to something
specific in the operating context. Use these patterns as a checklist —
not all need to apply:

- **Compounding adjacencies.** Given active projects, what adjacent
  domain would compound on existing work?
- **Pending decisions.** Given decisions with a future revisit_at, what's
  the actual base rate or expert consensus on outcomes?
- **North Star gap.** Given stated values or vision, what are people
  who've achieved that actually doing differently from the user?
- **Risk surface.** Given active projects, what's a non-obvious risk
  materializing in the wider world that affects them?
- **Asymmetric opportunities.** Given the user's particular position,
  what small action has disproportionate potential upside?
- **Capability-leveraged opportunities.** Given the user's current
  capabilities and their levels (load via
  `state_list_active_capabilities`), what opportunities exist that
  *require* this specific combination? An opportunity is only worth
  surfacing if it would be disproportionately accessible to this user
  versus a generalist.
- **Capability-aspiration gap.** Given the user's stated vision or
  active projects, are there capabilities that would significantly
  accelerate progress that the user could plausibly acquire? Frame
  as a question about whether the gap is worth closing — never a
  prescription.
- **Benchmarks via capability match.** People who have similar
  capability profiles AND achieved outcomes the user has stated they
  want — what did they do differently? Use web search to find role
  models with this specific capability stack, not generic success
  stories.
- **Stated-vs-revealed gap (blindspot).** Where do the user's stated
  values or priorities diverge from their actual project portfolio
  and recent decisions? (No external research needed.)
- **Aging without progress (blindspot).** Projects active for 60+ days
  with no decisions logged — kill candidates, snooze candidates, or
  aspirational? (No external research needed.)
- **Principle compatibility.** When forming candidate opportunities, check
  them against the user's top-ranked principles (state_list_principles).
  Do not surface opportunities that would require an unjustified departure
  from a high-ranked principle. If an otherwise-attractive opportunity
  conflicts with a top principle, either drop it or surface it explicitly
  as a tension: "This opportunity is strong on its merits but would require
  departing from your principle of [X]. Worth it only if [higher principle]
  is served." This is advisory and forward-looking — it shapes what you
  surface; it does not write principle evaluations (only capture does that,
  and only for committed decisions).
- **Idea viability.** For any idea in spark or exploring status that
  intersects with the user's capabilities or interests, consider whether
  it's worth a research pass: given what this person has and what the
  world looks like, is there a real opening here? If research suggests an
  idea is genuinely promising, the resulting suggestion can recommend
  promoting it to a project. If research suggests it's not viable, the
  suggestion can gently note that — surfacing the finding helps the user
  release it cleanly rather than letting it linger. Anchor such a
  suggestion to the idea (`entity_type = "idea"`).

For each candidate question, write a one-line justification linking it
to specific entities in the user's data. Reject questions that don't
anchor to anything specific.

Check past rejected suggestions. If a candidate question is
substantially similar to something the user rejected before, drop it
or reframe it explicitly: "I considered X again because [new context];
you rejected a similar suggestion because [reason]. Has that changed?"

Pick the top 2–3 most promising. More than three and depth suffers.

## 3. Research

For each chosen question, run real research. Use web search and fetch
authoritative sources. Take notes per source.

Rules:
- Every claim that ends up in a suggestion must be sourced to a real URL.
- Prefer primary sources, recent data, expert analysis. Skip listicles
  and AI-generated SEO content.
- If you can't find good evidence on a question, drop it. Don't pad.
- Time-box this. Three deep findings beat twenty shallow ones.

For blindspot-type questions (stated-vs-revealed, aging-without-progress),
external research isn't needed — the evidence is in the user's own data.

## 4. Connect findings to the user's data

For each candidate finding, ask: which specific entity in the user's
DB does this connect to? Project? Decision? Stated value?

If the answer is "nothing specific" — drop the finding.

A finding survives only if you can write a one-sentence anchor:
"This connects to project X because Y."

This step is the keystone. Without it the skill collapses into a
generic news feed. The suggestions write will reject any payload that has
zero internal_anchors. Don't try to bypass that check.

## 5. Propose actionable items

For each surviving finding, write a suggestion via state_write
("suggestion", ...). Generate one session_id (uuid) at the start
of this run and share it across all suggestions from this invocation.

Each suggestion:

- **kind**: opportunity / risk / question / pattern / blindspot
- **content**: the action you're proposing, in concrete terms.
  Not "consider exploring X" — "talk to person Y about X within two
  weeks" or "log a decision about whether to add X to project P".
- **rationale**: which entity this connects to, what evidence supports
  it, why now.
- **linked_evidence**: internal_anchors (entity_type, entity_id,
  why_anchored) plus external_sources (url, claim_supported,
  accessed_at) where applicable.

Aim for 2–5 suggestions. Fewer is fine. Zero is fine if nothing held up.
Saying "nothing held up this time" is more honest than padding.

## 6. Hand off to inbox

After writing the suggestions, **close this operation first**, then
hand off to inbox:

1. Call `state_end_operation(operation_id, "completed", notes="<N
   suggestions written>")` to close the research operation. The inbox
   flow that follows is the user's own operation; running them as one
   would conflate "found things" with "reviewed things."
2. Then read `skill://inbox` and execute it. The inbox skill calls
   its own `state_begin_operation` and picks up the suggestions you
   just wrote.

Do not surface the suggestions inline yourself — the inbox skill owns
the review and disposition flow, and using it keeps the user's
interaction surface consistent.

## Notes on judgment

- The user's time is the scarce resource. A suggestion they can't act on
  in the next two weeks is probably not worth surfacing yet.
- If two suggestions point at the same underlying action, merge them.
- Resist comprehensiveness. The output is a small number of
  high-conviction actions, not a survey.
- If research suggests the user's current direction is wrong (rather
  than incomplete), say so clearly. That's the most valuable kind of
  finding and the easiest to soft-pedal. Don't soft-pedal.
- Be especially careful with suggestions touching health, relationships,
  or finances — these domains demand higher confidence and gentler
  framing. When in doubt, frame as a question (kind=question) rather
  than an action.


## Close the operation

Call `state_end_operation(operation_id, "completed", notes="<one-line
summary>")` to mark the flow complete. The notes are a brief summary
of what actually happened — *"3 drafts committed"*, *"no patterns
crossed threshold"*, *"4 capabilities added"* — and they're what
appears next to this operation in the trace view. If the flow ended
without completing, pass `"abandoned"` as the status instead.
