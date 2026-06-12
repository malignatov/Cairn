---
name: overview
description: |
  Map of Cairn for the agent: every skill, every tool group, the data
  model, the recommended session-start flow, and an intent→skill table
  for routing user requests. Load once at session start to know what's
  available without reading all six skills.

  Triggers: "/help", "/overview", "/map", "what can you do", "what skills
  are available", "what tools do you have", first message in a new
  session, MCP client just connected and needs orientation.

  Not for: per-procedure instructions (use the matching skill://); the
  assistant's operating philosophy (use constitution://main); per-entity
  field details (use schema://<entity_type>).
---

# Cairn — Agent Overview

A quick-reference map of the system. Read this once at session start
and you'll know what's available, when to use it, and where the
boundaries between the parts lie.

## What Cairn is

A thin staging layer over SQLite that stores the user's strategic state
— projects, decisions, commitments, stakeholders, interests, life-area
ratings, observations — and serves procedural skills as markdown
resources. The LLM does all the reasoning; Cairn holds the data and
the playbook.

## Recommended session start

1. **Read `constitution://main` once per session** if you haven't
   already. It sets the tone the rest of the system depends on
   (recommending less rather than more, tone for sensitive domains,
   loyalty to who the user is becoming).
2. **Identify the user's intent.** Use the intent→skill table below.
3. **Read the single matching skill.** Don't pre-load multiple skills
   "just in case"; context is precious.
4. **Execute the skill's procedure.** Each skill tells you which tools
   to call in what order.

If the user's intent doesn't match any skill clearly, ask a clarifying
question rather than guessing.

### Two access paths — pick whichever your client supports

Cairn exposes every skill, schema, guide, and the constitution two
ways, in parallel. Use whichever your MCP client surfaces:

| Path | When | How |
|---|---|---|
| **MCP resources** | Your client surfaces `resources/list` and `resources/read` (Claude Code, custom MCP clients). | Read via the URI directly: `constitution://main`, `skill://capture`, `schema://projects`, `guide://overview`. |
| **Tool wrappers** | Your client only surfaces tools (Claude Desktop, Cowork, claude.ai chat). Resources are invisible to the model in these surfaces. | Call `state_read_constitution()`, `state_list_skills()` + `state_read_skill("capture")`, `state_list_schemas()` + `state_read_schema("projects")`, `state_list_guides()` + `state_read_guide("overview")`. |

The two paths return identical content. If you can't tell which mode
your client is in, just call the tool — it works everywhere. If you
*are* in a resource-aware client, the URI form is slightly cheaper
because it skips the JSON-tool round-trip.

## Intent → skill

| User says... | Skill to invoke |
|---|---|
| "/capture", "save this", "record what we discussed", pastes a transcript or notes | `skill://capture` |
| "/inbox", "what's in my inbox", "show me pending suggestions", "let me triage" | `skill://inbox` |
| "/research", "find opportunities", "what should I be thinking about", "outside-counsel work" | `skill://opportunity_research` |
| "/scan", "find weak spots", "what am I neglecting", "where am I drifting", "spot blindspots" | `skill://scan_life_units` |
| "/assess", "rate my life", "quarterly review", "Strack assessment", "score my life areas" | `skill://assess_life_portfolio` |
| "/define great life", "set up my values", "PERMA-V", "tell the system what matters" | `skill://define_great_life` |
| "/define-principles", "/principles", "set up my principles", "the rules I decide by", "rank/revise my principles" | `skill://define_principles` |
| "/idea", "capture this idea", "jot this down", "what if…", "save this spark" | `skill://capture_idea` |
| "/review-ideas", "go through my ideas", "tend my idea garden", "what's gone cold" | `skill://review_ideas` |
| "/digest", "what's new in my interests", "catch me up", "what's happening in [topic]" | `skill://digest` |

For full trigger phrasing and disambiguation see each skill's
description in `resources/list`.

A second easily-confused pair: `opportunity_research` is convergent and
practical ("what should I do?" → actionable proposals); `digest` is
divergent and exploratory ("what's new in the worlds I care about?" →
awareness, not action). And note `digest` is *not* a `scan_*`: the
scanners are diagnostic (hunt for problems); the digest feeds curiosity.

Note the two easily-confused intake skills: `define_great_life` captures
PERMA-V — *descriptive*, how life is going; `define_principles` captures
ranked operating rules — *prescriptive*, how the user chooses. Different
layers.

## Resources at a glance

**Philosophy (one):** `constitution://main`

**Skills (ten procedures the LLM runs):**
`skill://capture`, `skill://inbox`, `skill://opportunity_research`,
`skill://scan_life_units`, `skill://assess_life_portfolio`,
`skill://define_great_life`, `skill://define_principles`,
`skill://capture_idea`, `skill://review_ideas`, `skill://digest`.

**Schemas (thirteen entity field references):**
`schema://projects`, `schema://decisions`, `schema://drafts`,
`schema://commitments`, `schema://stakeholders`, `schema://interests`,
`schema://suggestions`, `schema://principles`,
`schema://decision_principle_evaluations`, `schema://ideas`,
`schema://life_units`, `schema://slu_assessments`,
`schema://great_life_dimensions`.

**Guides (this directory):** `guide://overview` (this file). Drop new
markdown into `guides/` to extend.

## Data model in plain prose

There are three sets of entities:

**Captured entities** — things the user has said or done that they want
tracked. These come in via the capture skill, go through the draft
flow (`drafts` table), and land in their canonical table:
- `projects` — things being worked on or killed
- `decisions` — choices made with stated rationale
- `commitments` — promises (to self or others)
- `stakeholders` — people the user has explicitly chosen to track
- `interests` — recurring topics that aren't projects

**Proactive suggestions** — things the assistant generated:
- `suggestions` — opportunity / risk / question / pattern / blindspot,
  produced by `opportunity_research` and `scan_life_units`, reviewed via
  `inbox`. Every suggestion must anchor to specific internal data; the
  storage layer rejects unanchored writes.

**Strategic substrate** — the structural lens for everything else:
- `life_units` — Strack's 16 SLUs, pre-seeded on first boot
- `slu_assessments` — per-SLU importance/satisfaction/hours snapshots
- `great_life_dimensions` — PERMA-V values intake (descriptive)
- `entity_slu_links` — many-to-many tags from captured entities to SLUs
- `slu_observations` — the scanner's diagnostic log

**Principles** — the user's ranked operating rules (prescriptive: how the
user chooses, vs PERMA-V's descriptive measure). No pre-seeding; soft
overrides; never judged or inferred by the system:
- `principles` — ranked rules (rank 1 = highest), via `define_principles`
- `principle_revisions` — append-only log of created/rewritten/reranked/retired
- `decision_principle_evaluations` — per-decision relationship to a principle:
  `aligned` / `justified_override` (healthy) / `unjustified_departure`
  (the only case the scanner watches, and only when it repeats)

**Ideas** — retained sparks, pre-commitment. The point is maturation (move
or die), not storage. Captured via `capture_idea`, matured via
`review_ideas`:
- `ideas` — spark → exploring → someday → promoted/released. Heat (warm/
  neutral/cooling) is *derived* from `last_touched_at`, never stored; only
  spark/exploring can cool. `someday` is a deliberate park, never nagged.
  The scanner's `cooling_idea` pattern is the patient anti-rot mechanism.

**Cross-cutting:**
- `sources` + `source_links` — chat/document excerpts, attached to any
  entity for provenance
- `stakeholder_project_links` — many-to-many tying people to projects

## Tool groups

You don't need to memorize these; the matching skill calls the right
ones in the right order. This is just the inventory.

**Polymorphic CRUD** over `project`, `decision`, `commitment`,
`stakeholder`, `interest`, `suggestion`, `capability`, `resource`,
`principle`:
`state_query`, `state_write`, `state_update`.

**Draft flow** (capture's safety net):
`state_draft`, `state_list_drafts`, `state_commit_draft`,
`state_reject_draft`, `state_amend_draft`.

**Suggestions / inbox flow:**
`state_list_pending_suggestions`, `state_disposition_suggestion`.

**Principles (v7)** — ranked-rule operations needing dedicated logic:
`state_list_principles`, `state_rerank_principle`, `state_retire_principle`,
`state_evaluate_decision_against_principles`,
`state_record_principle_evaluation`, `state_list_principle_departures`.

**Ideas (v8)** — heat-aware reads and terminal transitions:
`state_list_ideas`, `state_list_cooling_ideas`, `state_touch_idea`,
`state_promote_idea`, `state_release_idea`.

**Source attachment:**
`state_attach_source`.

**Strategic Life Units:**
`state_list_active_slus`, `state_activate_slu`, `state_deactivate_slu`,
`state_link_entity_to_slu`, `state_list_entity_slu_links`.

**Assessments:**
`state_start_assessment_session`, `state_record_slu_rating`,
`state_record_perma_rating`, `state_list_assessments`,
`state_list_perma_ratings`, `state_compute_portfolio_quadrants`.

**Scanner diagnostics:**
`state_write_observation`, `state_list_observations`.

**Commitment transitions:**
`state_complete_commitment`, `state_drop_commitment`.

**Stakeholder transitions:**
`state_link_stakeholder_to_project`, `state_update_contact`,
`state_list_overdue_contacts`.

## What NOT to do

- **Don't write `suggestions` via `state_write` ad-hoc.** They're
  the assistant's voice; they go through `opportunity_research` or
  `scan_life_units`, and the user reviews via `inbox`. Bypassing this
  buries the user's veto.
- **Don't pre-populate server-managed fields** like `last_contact_at`,
  `source_count`, `quadrant`, `created_at`. Storage computes or
  enforces these.
- **Don't extract entities the user didn't actually say.** The capture
  skill is explicit: "capture what was said," not what you inferred.
- **Don't auto-add stakeholders.** Stakeholders are opt-in per person.
  Propose them one at a time; ask before adding anyone mentioned only
  in passing.
- **Don't load every skill at session start.** Read this overview,
  identify the right one, load only it.
- **Don't run multiple sensitive-domain blindspot suggestions in one
  scan.** The scanner's tone calibration enforces this; respect it.

## Operational caveats worth knowing

- **Constitution is opt-in.** Each skill reminds you to load
  `constitution://main`, but nothing enforces it. Reading it once per
  session is genuinely important — the system's tone discipline lives
  there.
- **Cold-start state.** Until the user runs `assess_life_portfolio` at
  least twice and `define_great_life` once, several scanner signals
  are mathematically unavailable (trajectory) or unweighted (no
  PERMA-V orientation). The scanner handles this explicitly; don't
  fabricate signals to fill the gap.
- **Anchor convention for suggestions.** Use these canonical
  `entity_type` values in `linked_evidence.internal_anchors`:
  `life_unit`, `slu_observation`, `project`, `decision`,
  `commitment`, `stakeholder`, `interest`. The storage layer doesn't
  enforce this set, but staying within it keeps anchors queryable
  later.

## Where to look next

- For tone and philosophy: `constitution://main`
- For a procedure: `skill://<name>` (see intent→skill above)
- For a field-level data spec: `schema://<entity_type>`
