"""MCP server: exposes the storage layer as tools and serves markdown resources.

The tool functions here are intentionally thin — they validate inputs the
SDK can't, then delegate to the Storage object. Skills, schemas, and the
constitution are loaded from disk on each read so editing markdown takes
effect without code changes.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.resources import FunctionResource
from pydantic import AnyUrl

from .storage import Storage

log = logging.getLogger("meta_assistant")

# --- paths and config (env-driven, with sensible defaults) --------------------
#
# Two run modes, distinguished by whether we're a PyInstaller bundle:
#
#   dev      — running from a source checkout. Content lives in the repo,
#              the DB in ./data. cwd-relative defaults, unchanged since v1.
#   frozen   — the standalone macOS binary an early adopter runs (no Python,
#              no Docker). Content is baked into the bundle and resolved via
#              sys._MEIPASS (read-only); the DB lives in Application Support —
#              the one writable, persistent spot a GUI-spawned process can
#              always reach (~/Documents et al. are privacy-restricted for
#              children spawned by Claude Desktop; see AGENTS.md footguns).
#
# Explicit META_* env vars override either mode — that's how tests inject
# tmp dirs and how a migrating user can point at an existing DB.

FROZEN = getattr(sys, "frozen", False)
_BUNDLE = Path(getattr(sys, "_MEIPASS", "."))  # extraction dir when frozen
_APP_SUPPORT = Path.home() / "Library" / "Application Support" / "Cairn"


def _content_default(name: str) -> str:
    """Default location of a bundled content dir/file (skills, constitution…)."""
    return str(_BUNDLE / name) if FROZEN else f"./{name}"


DB_PATH = os.environ.get(
    "META_DB_PATH",
    str(_APP_SUPPORT / "meta.db") if FROZEN else "./data/meta.db",
)
SKILLS_DIR = Path(os.environ.get("META_SKILLS_DIR", _content_default("skills")))
SCHEMAS_DIR = Path(os.environ.get("META_SCHEMAS_DIR", _content_default("schemas")))
GUIDES_DIR = Path(os.environ.get("META_GUIDES_DIR", _content_default("guides")))
CONSTITUTION_PATH = Path(
    os.environ.get("META_CONSTITUTION_PATH", _content_default("constitution.md"))
)
# Default to loopback so a plain `python -m meta_assistant` is NOT exposed to
# the network. The Docker image overrides META_HOST=0.0.0.0 (it must bind all
# interfaces to be reachable through a published port) and docker-compose
# publishes that port to 127.0.0.1 only. Bind a non-loopback host yourself only
# deliberately, and pair it with META_AUTH_TOKEN (see run()).
HOST = os.environ.get("META_HOST", "127.0.0.1")
PORT = int(os.environ.get("META_PORT", "8000"))


def build_server(
    db_path: str = DB_PATH,
    skills_dir: Path = SKILLS_DIR,
    schemas_dir: Path = SCHEMAS_DIR,
    guides_dir: Path = GUIDES_DIR,
    constitution_path: Path = CONSTITUTION_PATH,
    host: str = HOST,
    port: int = PORT,
) -> tuple[FastMCP, Storage]:
    """Construct a configured FastMCP instance and its backing Storage.

    Returned as a pair so tests can drive the same tools without spinning
    up the HTTP transport.
    """
    storage = Storage(db_path)
    mcp = FastMCP("meta-assistant", host=host, port=port)

    _register_tools(mcp, storage)
    _register_resources(
        mcp, skills_dir, schemas_dir, guides_dir, constitution_path
    )
    # v6.1: tool-wrapped access to the same content MCP exposes as
    # resources. Most production MCP clients (Desktop, Cowork, claude.ai)
    # don't surface resource-reading to the LLM — only tools — so without
    # these wrappers the skill markdown is effectively invisible to the
    # model. Resources stay registered for resource-aware clients (Claude
    # Code etc.); the tools below are the parallel path.
    _register_content_access_tools(
        mcp, skills_dir, schemas_dir, guides_dir, constitution_path
    )

    # v6: wrap every registered tool and every resource reader with the
    # logging dispatcher. Done AFTER registration so we catch the full
    # surface — including future additions that only have to remember
    # to register the normal way.
    _wrap_all_tools_for_logging(mcp, storage)
    _wrap_all_resources_for_logging(mcp, storage)

    return mcp, storage


# --- tools --------------------------------------------------------------------


def _register_tools(mcp: FastMCP, storage: Storage) -> None:
    @mcp.tool(
        name="state_query",
        description=(
            "List entities of a given type matching optional equality filters. "
            "entity_type is 'project' or 'decision'. filters is a flat dict of "
            "field=value pairs."
        ),
    )
    def state_query(
        entity_type: str, filters: Optional[dict] = None
    ) -> list[dict]:
        return storage.query(entity_type, filters)

    @mcp.tool(
        name="state_write",
        description=(
            "Create an entity directly, bypassing the draft flow. Use only "
            "when the user has already approved the write inside the current "
            "conversation. For uncertain captures, prefer state_draft."
        ),
    )
    def state_write(entity_type: str, data: dict) -> dict:
        return storage.write(entity_type, data)

    @mcp.tool(
        name="state_update",
        description=(
            "Partially update an existing entity by id. patch is a dict of "
            "field=value pairs to overwrite."
        ),
    )
    def state_update(entity_type: str, id: str, patch: dict) -> dict:
        return storage.update(entity_type, id, patch)

    @mcp.tool(
        name="state_draft",
        description=(
            "Stage a captured entity as a draft for the user to confirm. A "
            "draft is something the USER said, held in the drafts table; it "
            "does not affect canonical state until state_commit_draft is "
            "called. (Distinct from a suggestion, which is something the "
            "ASSISTANT surfaced proactively — for those use the inbox flow / "
            "state_list_pending_suggestions, not this.) Pass a session_id "
            "(any string) to group drafts from one capture."
        ),
    )
    def state_draft(
        entity_type: str,
        data: dict,
        session_id: Optional[str] = None,
    ) -> dict:
        return storage.create_draft(entity_type, data, session_id)

    @mcp.tool(
        name="state_list_drafts",
        description=(
            "List staged drafts — captured user content awaiting the user's "
            "confirmation — optionally filtered by session_id and/or status "
            "(pending, committed, rejected, amended). For the assistant's "
            "proactively-surfaced items awaiting triage, use "
            "state_list_pending_suggestions instead."
        ),
    )
    def state_list_drafts(
        session_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[dict]:
        return storage.list_drafts(session_id, status)

    @mcp.tool(
        name="state_commit_draft",
        description=(
            "Move a draft's payload into the canonical table. Returns the "
            "newly created entity. Errors if the draft is already committed "
            "or rejected."
        ),
    )
    def state_commit_draft(draft_id: str) -> dict:
        return storage.commit_draft(draft_id)

    @mcp.tool(
        name="state_reject_draft",
        description=(
            "Mark a draft as rejected. Optionally record the user's reason."
        ),
    )
    def state_reject_draft(
        draft_id: str, reason: Optional[str] = None
    ) -> dict:
        return storage.reject_draft(draft_id, reason)

    @mcp.tool(
        name="state_amend_draft",
        description=(
            "Apply a partial patch to a pending draft's data and re-mark it "
            "for review. Returns the updated draft."
        ),
    )
    def state_amend_draft(draft_id: str, patch: dict) -> dict:
        return storage.amend_draft(draft_id, patch)

    @mcp.tool(
        name="state_disposition_suggestion",
        description=(
            "Move a pending suggestion to accepted, rejected, or deferred. "
            "Suggestions are the ASSISTANT's own proactively-surfaced findings "
            "(distinct from drafts, which stage things the USER said). "
            "Always record a reason — a rejection without a reason is signal lost "
            "for future research runs. Errors if the suggestion is not pending."
        ),
    )
    def state_disposition_suggestion(
        id: str,
        status: str,
        reason: Optional[str] = None,
    ) -> dict:
        return storage.disposition_suggestion(id, status, reason)

    @mcp.tool(
        name="state_list_pending_suggestions",
        description=(
            "List pending suggestions — the ASSISTANT's proactively-surfaced "
            "findings awaiting triage (distinct from drafts, which stage "
            "captured USER content; see state_list_drafts). Newest first. "
            "Filter by session_id, kind "
            "(opportunity|risk|question|pattern|blindspot), and/or since "
            "(ISO timestamp lower bound on created_at)."
        ),
    )
    def state_list_pending_suggestions(
        session_id: Optional[str] = None,
        kind: Optional[str] = None,
        since: Optional[str] = None,
    ) -> list[dict]:
        return storage.list_pending_suggestions(session_id, kind, since)

    @mcp.tool(
        name="state_attach_source",
        description=(
            "Attach a source excerpt (chat snippet, pasted text, document "
            "reference) to an entity or draft. Returns the new source_id."
        ),
    )
    def state_attach_source(
        target_type: str,
        target_id: str,
        source_type: str,
        content: str,
    ) -> str:
        return storage.attach_source(target_type, target_id, source_type, content)

    # --- v7: principles ----------------------------------------------------
    # The user's RANKED operating rules — prescriptive (govern how the user
    # chooses), distinct from PERMA-V which is descriptive. state_write /
    # state_query / state_update handle principle create / read / edit via
    # the generic surface; the tools below cover the rank-aware and
    # evaluation operations that need dedicated logic.

    @mcp.tool(
        name="state_list_principles",
        description=(
            "List the user's operating principles in rank order (1 = highest "
            "priority). Defaults to active only; pass status='retired' or "
            "'all'. Principles are prescriptive rules the user evaluates "
            "decisions against — not to be confused with PERMA-V ratings "
            "(which describe how life is going)."
        ),
    )
    def state_list_principles(status: Optional[str] = "active") -> list[dict]:
        return storage.list_principles(status)

    @mcp.tool(
        name="state_rerank_principle",
        description=(
            "Move an active principle to a new rank (1 = highest), shifting "
            "the others to keep the hierarchy contiguous. Logs a 'reranked' "
            "revision. Re-ranking MUST go through this tool, never a plain "
            "state_update, because of the reordering logic. rationale is "
            "required — changing the hierarchy is a deliberate act."
        ),
    )
    def state_rerank_principle(
        principle_id: str, new_rank: int, rationale: str
    ) -> dict:
        return storage.rerank_principle(principle_id, new_rank, rationale)

    @mcp.tool(
        name="state_retire_principle",
        description=(
            "Retire a principle (sets status to retired), freeing its rank "
            "among the active set and leaving a gap the user can compact "
            "later. Logs a 'retired' revision. rationale required."
        ),
    )
    def state_retire_principle(principle_id: str, rationale: str) -> dict:
        return storage.retire_principle(principle_id, rationale)

    @mcp.tool(
        name="state_evaluate_decision_against_principles",
        description=(
            "Read helper for the capture flow: returns the active principles "
            "in rank order so the model can reason a committed decision "
            "against them. Does NOT write any evaluation — recording is the "
            "separate state_record_principle_evaluation step."
        ),
    )
    def state_evaluate_decision_against_principles(decision_id: str) -> dict:
        return storage.evaluate_decision_against_principles(decision_id)

    @mcp.tool(
        name="state_record_principle_evaluation",
        description=(
            "Record how a committed decision relates to a principle. "
            "relationship is one of: 'aligned' (honors it — record only "
            "notable alignments), 'justified_override' (departs from this "
            "principle to serve a HIGHER-ranked one — healthy, the hierarchy "
            "working; requires overrides_principle_id + rationale, and the "
            "target must outrank the departed principle), or "
            "'unjustified_departure' (departs with no higher principle "
            "invoked — requires rationale; severity auto-derives from the "
            "departed principle's rank if omitted). Only departures and "
            "overrides are always recorded."
        ),
    )
    def state_record_principle_evaluation(
        decision_id: str,
        principle_id: str,
        relationship: str,
        overrides_principle_id: Optional[str] = None,
        severity: Optional[str] = None,
        rationale: Optional[str] = None,
    ) -> dict:
        return storage.record_principle_evaluation(
            decision_id, principle_id, relationship,
            overrides_principle_id, severity, rationale,
        )

    @mcp.tool(
        name="state_list_principle_departures",
        description=(
            "Return decision↔principle evaluations, newest first, for the "
            "scanner. Filter by principle_id, by relationship (typically "
            "'unjustified_departure'), and/or since (ISO lower bound on "
            "evaluated_at). Justified overrides are healthy and should not be "
            "treated as problems."
        ),
    )
    def state_list_principle_departures(
        principle_id: Optional[str] = None,
        since: Optional[str] = None,
        relationship: Optional[str] = None,
    ) -> list[dict]:
        return storage.list_principle_departures(principle_id, since, relationship)

    # --- v8: ideas ---------------------------------------------------------
    # Retained sparks, pre-commitment. The point is maturation, not storage:
    # an idea must move (warm toward promotion, or be parked/released on
    # purpose) or it rots. state_write / state_query / state_update handle
    # create / read / status moves (spark↔exploring↔someday) via the generic
    # surface; the tools below add heat-aware reads and the terminal
    # transitions that carry side effects.

    @mcp.tool(
        name="state_list_ideas",
        description=(
            "List ideas (retained sparks), newest first, each with a derived "
            "`heat` (warm < 30d since last touch / neutral / cooling = 6mo+ "
            "for spark or exploring). status filters to a single status or "
            "the literal 'active' (spark + exploring + someday). since is an "
            "ISO lower bound on captured_at. Ideas are pre-commitment — not "
            "projects; promote them deliberately."
        ),
    )
    def state_list_ideas(
        status: Optional[str] = None, since: Optional[str] = None
    ) -> list[dict]:
        return storage.list_ideas(status, since)

    @mcp.tool(
        name="state_list_cooling_ideas",
        description=(
            "Return ideas in spark/exploring status untouched for at least "
            "dormant_months (default 6), coldest first. NEVER returns "
            "'someday' ideas (those were deliberately parked) or terminal "
            "ones. This is the scanner's patient anti-rot feed — surface a "
            "batch on a slow (quarterly) cadence and route the user to "
            "review_ideas, never nag."
        ),
    )
    def state_list_cooling_ideas(dormant_months: float = 6) -> list[dict]:
        return storage.list_cooling_ideas(dormant_months)

    @mcp.tool(
        name="state_touch_idea",
        description=(
            "Bump an idea's last_touched_at to now, keeping its derived heat "
            "honest. Call when an idea is revisited or referenced (e.g. the "
            "user chooses 'keep exploring' in review_ideas)."
        ),
    )
    def state_touch_idea(idea_id: str) -> dict:
        return storage.touch_idea(idea_id)

    @mcp.tool(
        name="state_promote_idea",
        description=(
            "Graduate an idea into a project: creates a project from the "
            "idea's title (→ name) and body (→ description), marks the idea "
            "'promoted' with a link to the new project, and touches it. "
            "Returns the new project — the user fleshes out kill criteria "
            "etc. afterward via the normal flows. Promotion is an explicit, "
            "deliberate threshold; the system never auto-promotes."
        ),
    )
    def state_promote_idea(idea_id: str) -> dict:
        return storage.promote_idea(idea_id)

    @mcp.tool(
        name="state_release_idea",
        description=(
            "Let an idea go: sets status to 'released' (terminal) with an "
            "optional reason, and touches it. Releasing dead ideas is healthy "
            "housekeeping, not a loss."
        ),
    )
    def state_release_idea(idea_id: str, reason: Optional[str] = None) -> dict:
        return storage.release_idea(idea_id, reason)

    # --- v3: Strategic Life Portfolio + PERMA-V ----------------------------

    @mcp.tool(
        name="state_list_active_slus",
        description=(
            "Return active Strategic Life Units (Strack's 16 SLUs, plus any "
            "user-added ones). Optionally filter by area: relationships, "
            "body_mind_spirit, community_society, job_learning_finances, "
            "interests_entertainment, personal_care."
        ),
    )
    def state_list_active_slus(area: Optional[str] = None) -> list[dict]:
        return storage.list_active_slus(area)

    @mcp.tool(
        name="state_start_assessment_session",
        description=(
            "Begin a new assessment session and return a session_id (uuid). "
            "type is one of: 'full' (a complete SLU portfolio re-rating, "
            "used by assess_life_portfolio), 'patch' (an SLU session that "
            "inherits unchanged ratings from a prior session, used by "
            "patch_portfolio — requires inherited_from_session_id), or "
            "'great_life' (a PERMA-V intake). The legacy v3 value "
            "'slu_portfolio' is accepted as an alias for 'full'."
        ),
    )
    def state_start_assessment_session(
        type: str,
        inherited_from_session_id: Optional[str] = None,
    ) -> dict:
        return storage.start_assessment_session(type, inherited_from_session_id)

    @mcp.tool(
        name="state_record_slu_rating",
        description=(
            "Record one SLU rating in a portfolio assessment session. "
            "importance and satisfaction are integers 0–10. hours_per_week "
            "is a non-negative number (rough estimate). The quadrant "
            "(upper_right / upper_left / lower_right / lower_left) is "
            "computed at write time with threshold 6+. session_type is "
            "'full' (default) or 'patch'; patch ratings require "
            "inherited_from_session_id. For session-less rating changes "
            "use state_record_quick_slu_update instead."
        ),
    )
    def state_record_slu_rating(
        session_id: str,
        life_unit_id: str,
        importance: int,
        satisfaction: int,
        hours_per_week: Optional[float] = None,
        notes: Optional[str] = None,
        session_type: str = "full",
        inherited_from_session_id: Optional[str] = None,
    ) -> dict:
        return storage.record_slu_rating(
            session_id, life_unit_id, importance, satisfaction,
            hours_per_week, notes, session_type, inherited_from_session_id,
        )

    @mcp.tool(
        name="state_record_quick_slu_update",
        description=(
            "Record a standalone SLU rating change with no session. "
            "Writes a row with session_id=NULL and "
            "session_type='quick_update'. Visible in per-SLU trajectory "
            "queries; excluded from portfolio snapshot comparisons "
            "(compute_portfolio_quadrants ignores quick_update rows when "
            "picking the most recent session, and the scanner's "
            "quadrant_shift signal does too). Used by the "
            "quick_update_slu skill."
        ),
    )
    def state_record_quick_slu_update(
        life_unit_id: str,
        importance: int,
        satisfaction: int,
        hours_per_week: Optional[float] = None,
        notes: Optional[str] = None,
    ) -> dict:
        return storage.record_quick_slu_update(
            life_unit_id, importance, satisfaction, hours_per_week, notes,
        )

    @mcp.tool(
        name="state_get_latest_full_session",
        description=(
            "Return the most recent SLU portfolio session "
            "(session_type='full' or 'patch') along with all its ratings. "
            "Returns null if no such session exists. Used by "
            "patch_portfolio to know what to inherit forward."
        ),
    )
    def state_get_latest_full_session() -> Optional[dict]:
        return storage.get_latest_full_session()

    @mcp.tool(
        name="state_record_perma_rating",
        description=(
            "Record one PERMA-V dimension rating in a great_life session. "
            "dimension is one of positive_emotions, engagement, "
            "relationships, meaning, achievement, vitality."
        ),
    )
    def state_record_perma_rating(
        session_id: str,
        dimension: str,
        importance: int,
        satisfaction: int,
        notes: Optional[str] = None,
    ) -> dict:
        return storage.record_perma_rating(
            session_id, dimension, importance, satisfaction, notes,
        )

    @mcp.tool(
        name="state_compute_portfolio_quadrants",
        description=(
            "Return the SLUs of the most recent (or specified) portfolio "
            "assessment grouped by quadrant. If session_id is omitted, "
            "uses the most recent session."
        ),
    )
    def state_compute_portfolio_quadrants(
        session_id: Optional[str] = None,
    ) -> dict:
        return storage.compute_portfolio_quadrants(session_id)

    @mcp.tool(
        name="state_link_entity_to_slu",
        description=(
            "Tag an entity (project, decision, commitment, suggestion) "
            "with a Strategic Life Unit. Many-to-many: call multiple times "
            "to link to multiple SLUs."
        ),
    )
    def state_link_entity_to_slu(
        entity_type: str,
        entity_id: str,
        life_unit_id: str,
    ) -> dict:
        return storage.link_entity_to_slu(entity_type, entity_id, life_unit_id)

    @mcp.tool(
        name="state_list_assessments",
        description=(
            "Return historical SLU assessments for trajectory analysis. "
            "Optionally filter by life_unit_id and/or by a 'since' ISO "
            "timestamp lower bound. Returns newest first."
        ),
    )
    def state_list_assessments(
        life_unit_id: Optional[str] = None,
        since: Optional[str] = None,
    ) -> list[dict]:
        return storage.list_assessments(life_unit_id, since)

    @mcp.tool(
        name="state_list_perma_ratings",
        description=(
            "Return historical PERMA-V ratings (the user's stated definition "
            "of a great life). Optional filters: session_id, dimension "
            "(positive_emotions|engagement|relationships|meaning|achievement|"
            "vitality), since (ISO timestamp lower bound). Returns newest "
            "first. Used by the scanner to weight blindspot detection by the "
            "user's stated values; if this returns empty, the user has not "
            "run define_great_life yet and the orientation signal is "
            "unavailable."
        ),
    )
    def state_list_perma_ratings(
        session_id: Optional[str] = None,
        dimension: Optional[str] = None,
        since: Optional[str] = None,
    ) -> list[dict]:
        return storage.list_perma_ratings(session_id, dimension, since)

    @mcp.tool(
        name="state_list_entity_slu_links",
        description=(
            "Return entity↔life_unit tags. Optional filters: entity_type "
            "(project|decision|commitment|stakeholder|interest|"
            "suggestion), entity_id, life_unit_id, since (ISO timestamp "
            "lower bound on tag created_at). Used by the scanner to compute "
            "per-SLU tagged-activity counts and share-of-activity, and by "
            "any skill that needs to see which entities anchor to a given "
            "life unit."
        ),
    )
    def state_list_entity_slu_links(
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
        life_unit_id: Optional[str] = None,
        since: Optional[str] = None,
    ) -> list[dict]:
        return storage.list_entity_slu_links(
            entity_type, entity_id, life_unit_id, since
        )

    @mcp.tool(
        name="state_write_observation",
        description=(
            "Append an observation to the diagnostic log. signal is one of "
            "silence, erosion, mismatch, avoidance, quadrant_shift, "
            "compound_debt; severity is low|medium|high. evidence is a "
            "structured object pointing to the specific data behind the "
            "observation. Used by scan_life_units."
        ),
    )
    def state_write_observation(
        life_unit_id: str,
        signal: str,
        severity: str,
        evidence: dict,
        notes: Optional[str] = None,
    ) -> dict:
        return storage.write_observation(
            life_unit_id, signal, severity, evidence, notes,
        )

    @mcp.tool(
        name="state_list_observations",
        description=(
            "Read past observations. Optional filters: life_unit_id, since "
            "(ISO timestamp), severity (low|medium|high). Newest first."
        ),
    )
    def state_list_observations(
        life_unit_id: Optional[str] = None,
        since: Optional[str] = None,
        severity: Optional[str] = None,
    ) -> list[dict]:
        return storage.list_observations(life_unit_id, since, severity)

    @mcp.tool(
        name="state_deactivate_slu",
        description=(
            "Mark a life unit as not-applicable for this user (e.g. the "
            "user has no significant other right now). It stops appearing "
            "in list_active_slus and is skipped by portfolio assessments. "
            "Reversible via state_activate_slu."
        ),
    )
    def state_deactivate_slu(
        life_unit_id: str,
        reason: Optional[str] = None,
    ) -> dict:
        return storage.deactivate_slu(life_unit_id, reason)

    @mcp.tool(
        name="state_activate_slu",
        description=(
            "Re-activate a previously deactivated life unit so it returns "
            "to portfolio assessments."
        ),
    )
    def state_activate_slu(life_unit_id: str) -> dict:
        return storage.activate_slu(life_unit_id)

    # --- v3.1: commitments + stakeholders + interests -----------------------
    # state_write / state_query / state_update / state_draft /
    # state_commit_draft already accept the new entity_types
    # ('commitment', 'stakeholder', 'interest') — no extra registration
    # needed. The tools below are the new transition / convenience tools.

    @mcp.tool(
        name="state_complete_commitment",
        description=(
            "Mark an open commitment as done. Optionally record an outcome "
            "in one sentence. Errors if the commitment isn't open."
        ),
    )
    def state_complete_commitment(
        id: str,
        outcome: Optional[str] = None,
    ) -> dict:
        return storage.complete_commitment(id, outcome)

    @mcp.tool(
        name="state_drop_commitment",
        description=(
            "Mark an open commitment as dropped with a reason. Use when the "
            "commitment is no longer something the user intends to do."
        ),
    )
    def state_drop_commitment(
        id: str,
        reason: Optional[str] = None,
    ) -> dict:
        return storage.drop_commitment(id, reason)

    @mcp.tool(
        name="state_update_contact",
        description=(
            "Record a contact event with a stakeholder. Updates "
            "last_contact_at (defaults to now if omitted) and appends a "
            "timestamped line to the stakeholder's notes if a note is "
            "provided. Prior notes are preserved."
        ),
    )
    def state_update_contact(
        stakeholder_id: str,
        when: Optional[str] = None,
        note: Optional[str] = None,
    ) -> dict:
        return storage.update_contact(stakeholder_id, when, note)

    @mcp.tool(
        name="state_link_stakeholder_to_project",
        description=(
            "Tag a stakeholder as involved in a project. Optionally label "
            "the role they play in that project."
        ),
    )
    def state_link_stakeholder_to_project(
        stakeholder_id: str,
        project_id: str,
        role_in_project: Optional[str] = None,
    ) -> dict:
        return storage.link_stakeholder_to_project(
            stakeholder_id, project_id, role_in_project
        )

    @mcp.tool(
        name="state_list_overdue_contacts",
        description=(
            "Stakeholders not contacted in more than threshold_days days. "
            "Stakeholders with no recorded contact (last_contact_at IS NULL) "
            "are included and surfaced first."
        ),
    )
    def state_list_overdue_contacts(threshold_days: int) -> list[dict]:
        return storage.list_overdue_contacts(threshold_days)

    # --- v4: inventory (capabilities + resources) --------------------------
    # state_write / state_query / state_update / state_draft /
    # state_commit_draft already accept 'capability' and 'resource' as
    # entity_type values. The tools below are the inventory-specific
    # helpers and the diagnostic queries scan_inventory needs.

    @mcp.tool(
        name="state_list_active_capabilities",
        description=(
            "Return capabilities with status='active', optionally filtered "
            "by category (license|certification|credential|skill|hardware|"
            "access|other). Used by capture and opportunity_research when "
            "they need to know what the user currently has."
        ),
    )
    def state_list_active_capabilities(
        category: Optional[str] = None,
    ) -> list[dict]:
        return storage.list_active_capabilities(category)

    @mcp.tool(
        name="state_list_expiring_capabilities",
        description=(
            "Capabilities whose expires_at falls within `within_days` days "
            "from now. Sorted soonest-first. Used by scan_inventory to "
            "surface upcoming license/certification renewals."
        ),
    )
    def state_list_expiring_capabilities(within_days: int) -> list[dict]:
        return storage.list_expiring_capabilities(within_days)

    @mcp.tool(
        name="state_list_atrophying_capabilities",
        description=(
            "Active capabilities of categories that atrophy when unused "
            "(skill, certification, access) whose last_exercised_at is "
            "older than `months_silent` (default 12). Used by "
            "scan_inventory to flag skills that may have lapsed."
        ),
    )
    def state_list_atrophying_capabilities(
        months_silent: int = 12,
    ) -> list[dict]:
        return storage.list_atrophying_capabilities(months_silent)

    @mcp.tool(
        name="state_mark_capability_exercised",
        description=(
            "Bump a capability's last_exercised_at timestamp. `when` "
            "defaults to now (ISO timestamp). `source_ref` is informational "
            "only — use state_attach_source if you want lineage. Called by "
            "the capture skill when the user describes work that "
            "meaningfully exercises a skill."
        ),
    )
    def state_mark_capability_exercised(
        capability_id: str,
        when: Optional[str] = None,
        source_ref: Optional[str] = None,
    ) -> dict:
        return storage.mark_capability_exercised(capability_id, when, source_ref)

    @mcp.tool(
        name="state_list_resources_below_floor",
        description=(
            "Resources whose current_quantity has dropped below their "
            "declared floor. Most-depleted first."
        ),
    )
    def state_list_resources_below_floor() -> list[dict]:
        return storage.list_resources_below_floor()

    @mcp.tool(
        name="state_compute_resource_runway",
        description=(
            "For each resource, returns time-to-empty in months based on "
            "current_quantity / max(burn_rate - replenish_rate, 0). "
            "runway_months is null for resources without burn_rate, and "
            "for resources where replenish ≥ burn (no net depletion)."
        ),
    )
    def state_compute_resource_runway() -> list[dict]:
        return storage.compute_resource_runway()

    @mcp.tool(
        name="state_add_capability_requirement",
        description=(
            "Tag a project as needing a specific capability OR resource. "
            "Pass exactly one of capability_id / resource_id. "
            "required_level applies only to capability requirements "
            "(novice|intermediate|advanced|expert). required_amount applies "
            "only to resource requirements."
        ),
    )
    def state_add_capability_requirement(
        project_id: str,
        capability_id: Optional[str] = None,
        resource_id: Optional[str] = None,
        required_level: Optional[str] = None,
        required_amount: Optional[float] = None,
        notes: Optional[str] = None,
    ) -> dict:
        return storage.add_capability_requirement(
            project_id, capability_id, resource_id,
            required_level, required_amount, notes,
        )

    @mcp.tool(
        name="state_remove_capability_requirement",
        description="Delete a capability_requirement row by id.",
    )
    def state_remove_capability_requirement(id: str) -> dict:
        return storage.remove_capability_requirement(id)

    @mcp.tool(
        name="state_list_capability_requirements",
        description=(
            "List capability_requirements, optionally filtered by "
            "project_id. Each row includes a computed is_satisfied "
            "boolean reflecting current inventory state."
        ),
    )
    def state_list_capability_requirements(
        project_id: Optional[str] = None,
    ) -> list[dict]:
        return storage.list_capability_requirements(project_id)

    @mcp.tool(
        name="state_list_capability_gaps",
        description=(
            "Unsatisfied capability_requirements. With no project_id, "
            "returns gaps across all *active* projects (gaps on "
            "killed/dormant projects aren't actionable). Used by "
            "scan_inventory and opportunity_research."
        ),
    )
    def state_list_capability_gaps(
        project_id: Optional[str] = None,
    ) -> list[dict]:
        return storage.list_capability_gaps(project_id)

    @mcp.tool(
        name="state_delete",
        description=(
            "Permanently delete an entity. Currently allowed ONLY for "
            "entity_type='resource' — every other type has a status enum "
            "and should be retired via its proper transition instead. "
            "Used by update_resources when the user retires a resource "
            "they no longer track."
        ),
    )
    def state_delete(entity_type: str, id: str) -> dict:
        return storage.delete(entity_type, id)

    # --- v5: full-text search ----------------------------------------------

    @mcp.tool(
        name="state_search",
        description=(
            "Lexical full-text search across every indexed entity "
            "(projects, decisions, commitments, stakeholders, interests, "
            "capabilities, resources, suggestions, slu_observations, "
            "sources, life_units, slu_assessments, great_life_dimensions). "
            "Returns ranked results (BM25, higher score = more relevant) "
            "with `snippet` strings containing <mark>matched terms</mark>. "
            "Diacritic- and case-insensitive. By default queries are "
            "treated as phrase matches — \"Surf Bar Valencia\" searches "
            "for the whole phrase rather than individual words. To use "
            "FTS5 operators (AND, OR, NOT, prefix*) include them in the "
            "query and the tool will pass the query through unwrapped. "
            "Filters: entity_types (list of indexed types), since (ISO "
            "timestamp lower bound), limit (default 20, max 100). Queries "
            "shorter than 2 characters return an empty list."
        ),
    )
    def state_search(
        query: str,
        entity_types: Optional[list[str]] = None,
        since: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict]:
        return storage.search(query, entity_types, since, limit)

    # --- v6: operation lifecycle + trace querying --------------------------

    @mcp.tool(
        name="state_begin_operation",
        description=(
            "Open a named operation in the current chat. Returns an "
            "operation_id that the calling skill holds for the duration "
            "of the flow; every tool call made between begin and end is "
            "automatically tagged with this operation_id in the tool_calls "
            "log. If another operation is already in_progress in the same "
            "chat, it is auto-marked 'abandoned' (with a note explaining "
            "why) before the new one starts — this handles forgotten "
            "end_operation calls and mid-flow user pivots. Every skill "
            "(capture, scan_life_units, find, etc.) should call this as "
            "its first step."
        ),
    )
    def state_begin_operation(
        skill_name: str,
        notes: Optional[str] = None,
    ) -> dict:
        chat_id = _resolve_chat_id(storage)
        op_id = storage.begin_operation(chat_id, skill_name, notes)
        return {"operation_id": op_id, "chat_id": chat_id, "skill_name": skill_name}

    @mcp.tool(
        name="state_end_operation",
        description=(
            "Close an operation. status defaults to 'completed'; pass "
            "'abandoned' if the flow was aborted mid-way. notes is a "
            "brief one-line summary written by the skill ('3 drafts "
            "committed', 'no patterns crossed threshold', etc.) — it's "
            "what shows up in the trace view's operation list. Errors "
            "if the operation isn't in_progress (you can't end something "
            "that's already terminal)."
        ),
    )
    def state_end_operation(
        operation_id: str,
        status: str = "completed",
        notes: Optional[str] = None,
    ) -> dict:
        return storage.end_operation(operation_id, status, notes)

    @mcp.tool(
        name="state_list_recent_chats",
        description=(
            "Return recent MCP chats (one per connection), newest first. "
            "Used by the trace skill's 'last chat' view."
        ),
    )
    def state_list_recent_chats(
        limit: int = 20,
        since: Optional[str] = None,
    ) -> list[dict]:
        return storage.list_recent_chats(limit, since)

    @mcp.tool(
        name="state_list_recent_operations",
        description=(
            "Return recent operations, newest first. Optional filters: "
            "skill_name (e.g. 'capture'), status (in_progress|completed|"
            "abandoned), since (ISO timestamp), limit (default 20)."
        ),
    )
    def state_list_recent_operations(
        skill_name: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 20,
        since: Optional[str] = None,
    ) -> list[dict]:
        return storage.list_recent_operations(skill_name, status, limit, since)

    @mcp.tool(
        name="state_get_operation_trace",
        description=(
            "Return one operation plus all of its tool_calls and "
            "resource_reads in chronological order. The primary drill-"
            "down for the trace skill — shows exactly what happened "
            "inside a single skill invocation."
        ),
    )
    def state_get_operation_trace(operation_id: str) -> Optional[dict]:
        return storage.get_operation_trace(operation_id)

    @mcp.tool(
        name="state_get_chat_trace",
        description=(
            "Return one chat plus all its operations, tool_calls (both "
            "operation-bound and ad-hoc), and resource_reads. Use for a "
            "session-wide audit view."
        ),
    )
    def state_get_chat_trace(chat_id: str) -> Optional[dict]:
        return storage.get_chat_trace(chat_id)

    @mcp.tool(
        name="state_list_abandoned_operations",
        description=(
            "Operations that ended badly: either explicitly marked "
            "'abandoned' (auto-closed because a new operation started) "
            "or stuck in 'in_progress' past a threshold (default 1 "
            "hour). Pattern surface — repeated abandonment of the same "
            "skill is itself signal worth surfacing to the user."
        ),
    )
    def state_list_abandoned_operations(
        since: Optional[str] = None,
        stuck_for_hours: int = 1,
    ) -> list[dict]:
        return storage.list_abandoned_operations(since, stuck_for_hours)

    @mcp.tool(
        name="state_list_errors",
        description=(
            "Tool calls that returned errors. Useful for debugging when "
            "a skill behaves oddly — exact arguments, exact error "
            "message, exact time."
        ),
    )
    def state_list_errors(
        since: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        return storage.list_errors(since, limit)

    @mcp.tool(
        name="state_purge_logs",
        description=(
            "Permanently delete log rows older than before_date (ISO "
            "timestamp). Returns per-table counts of rows removed. "
            "Manual purge only — Cairn has no automatic retention. The "
            "trace skill points users here when their DB grows large."
        ),
    )
    def state_purge_logs(before_date: str) -> dict:
        return storage.purge_logs(before_date)


# --- v6.1: tool-wrapped content access ---------------------------------------

# Stem names must be filesystem-safe; this also blocks `..` traversal
# attempts and absolute paths in user-supplied arguments.
_SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def _read_markdown_file(directory: Path, name: str, scheme: str) -> str:
    """Validated read of <directory>/<name>.md, with sensible errors.

    Used by every state_read_<X> wrapper. Validation happens before any
    filesystem access: the name must be alphanumeric/underscore/dash,
    nothing else — that prevents traversal and absolute-path attacks
    even though the rest of the path is controlled by us.
    """
    if not isinstance(name, str) or not _SAFE_NAME_RE.match(name):
        raise ValueError(
            f"{scheme} name must be alphanumeric (underscores and dashes ok); "
            f"got {name!r}"
        )
    path = directory / f"{name}.md"
    if not path.exists():
        raise ValueError(f"no {scheme} named {name!r}")
    return path.read_text(encoding="utf-8")


def _list_markdown_catalog(
    directory: Path, scheme: str, fallback_description: Callable[[str], str],
) -> list[dict]:
    """List every *.md in a directory as {name, description, uri}.

    Description is parsed from frontmatter when present (the trigger-rich
    descriptions the LLM should see for discovery); falls back to the
    per-scheme stub otherwise. Same contract as the resource registration
    in _register_directory, so what the LLM sees via tools matches what
    a resource-aware client would see via resources/list.
    """
    if not directory.is_dir():
        return []
    out: list[dict] = []
    for md in sorted(directory.glob("*.md")):
        name = md.stem
        description = _parse_skill_description(md) or fallback_description(name)
        out.append({
            "name": name,
            "description": description,
            "uri": f"{scheme}://{name}",
        })
    return out


def _register_content_access_tools(
    mcp: FastMCP,
    skills_dir: Path,
    schemas_dir: Path,
    guides_dir: Path,
    constitution_path: Path,
) -> None:
    """Register the v6.1 tool-wrapped content surface.

    MCP exposes skills, schemas, guides, and the constitution as resources
    (constitution://, skill://, schema://, guide://). Resource-aware
    clients fetch them directly. Tool-only clients (Claude Desktop,
    Cowork, claude.ai) can't reach those resources at all — so the LLM
    in those surfaces was operating without the playbook. These tools
    are the parallel access path: same content, exposed via the tool
    grammar every client supports.
    """

    @mcp.tool(
        name="state_read_constitution",
        description=(
            "Return the operating-philosophy markdown that sits at "
            "constitution://main. Mirrors what `resources/read` would "
            "return for clients that support MCP resources, but accessible "
            "through the tool surface so tool-only clients can reach it "
            "too. Skills instruct the model to call this once per session."
        ),
    )
    def state_read_constitution() -> str:
        if not constitution_path.exists():
            raise ValueError(f"constitution not found at {constitution_path}")
        return constitution_path.read_text(encoding="utf-8")

    @mcp.tool(
        name="state_list_skills",
        description=(
            "List every available skill with its name, frontmatter "
            "description (the WHAT / Triggers / Not-for the model uses "
            "to pick the right skill), and skill:// URI. Read this once "
            "early in a session to know which skills exist before "
            "matching the user's intent."
        ),
    )
    def state_list_skills() -> list[dict]:
        return _list_markdown_catalog(
            skills_dir, "skill", lambda n: f"Skill procedure: {n}",
        )

    @mcp.tool(
        name="state_read_skill",
        description=(
            "Return the full markdown body of a named skill (e.g. "
            "'capture', 'scan_life_units', 'patch_portfolio'). This is "
            "the procedure the model executes — including the 'Before "
            "you begin' constitution cue, the step-by-step instructions, "
            "and the tone guidance. Call this right after identifying "
            "which skill matches the user's request."
        ),
    )
    def state_read_skill(name: str) -> str:
        return _read_markdown_file(skills_dir, name, "skill")

    @mcp.tool(
        name="state_list_schemas",
        description=(
            "List every available schema doc with name and description "
            "and schema:// URI. Schemas describe the field-level shape "
            "of each entity type (projects, decisions, capabilities, "
            "etc.) — load one when you need to know what fields an "
            "entity has and what their constraints are."
        ),
    )
    def state_list_schemas() -> list[dict]:
        return _list_markdown_catalog(
            schemas_dir, "schema", lambda n: f"Schema for the {n} entity type.",
        )

    @mcp.tool(
        name="state_read_schema",
        description=(
            "Return the full markdown body of a named schema doc (e.g. "
            "'projects', 'commitments', 'drafts'). Use when you "
            "need the canonical field list and validation rules for an "
            "entity type."
        ),
    )
    def state_read_schema(name: str) -> str:
        return _read_markdown_file(schemas_dir, name, "schema")

    @mcp.tool(
        name="state_list_guides",
        description=(
            "List every guide doc with name, description, and guide:// "
            "URI. Guides are session-level orientation documents — the "
            "system map, future FAQ/glossary etc. Load these for "
            "navigating the system, not for executing procedures (use "
            "skills for that)."
        ),
    )
    def state_list_guides() -> list[dict]:
        return _list_markdown_catalog(
            guides_dir, "guide", lambda n: f"Guide: {n}",
        )

    @mcp.tool(
        name="state_read_guide",
        description=(
            "Return the full markdown body of a named guide (e.g. "
            "'overview'). The overview guide is the recommended first "
            "read in any new session — it maps user intents to the "
            "right skill."
        ),
    )
    def state_read_guide(name: str) -> str:
        return _read_markdown_file(guides_dir, name, "guide")


# --- v6: dispatch-layer logging wrapper --------------------------------------

# Tools that ARE the logging surface. We don't log calls TO them — that
# would recurse forever (purge_logs would delete the row that recorded
# purge_logs being called).
_UNLOGGED_TOOLS: set[str] = {
    "state_purge_logs",
}


def _get_session_chats(storage: Storage) -> dict[str, str]:
    """Per-Storage cache mapping MCP session id → chat_id. Lives on the
    Storage instance (not module-level) so each DB has its own mapping
    and tests don't leak state into each other."""
    cache = getattr(storage, "_session_chats", None)
    if cache is None:
        cache = {}
        storage._session_chats = cache  # type: ignore[attr-defined]
    return cache


def _serialize_for_log(obj: Any) -> str:
    """JSON-serialize an argument or response for the log. Tool responses
    routinely include nested dicts, datetimes, custom row objects, etc.
    A serialization failure here must not break the tool — fall back to
    a placeholder that says what went wrong.
    """
    def _default(o: Any) -> Any:
        if isinstance(o, datetime):
            return o.isoformat()
        if hasattr(o, "model_dump"):  # pydantic v2
            try:
                return o.model_dump()
            except Exception:
                pass
        if hasattr(o, "dict"):  # pydantic v1, also misc objects
            try:
                return o.dict()
            except Exception:
                pass
        return str(o)

    try:
        return json.dumps(obj, default=_default, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "__serialization_error__": str(e),
            "type": type(obj).__name__,
        })


def _get_request_session_id() -> Optional[str]:
    """Best-effort: pull a stable per-connection identifier from
    FastMCP's request context. Returns None outside a request (e.g.
    unit tests calling tools directly) — the caller falls back to a
    sentinel chat.

    What we need is something that's the *same* across every tool call
    within one MCP connection but *different* between connections.
    The SDK exposes ``session`` on the request context but doesn't
    consistently expose a string session_id across versions. The
    fallback ``id(session)`` is a process-stable identity — same
    session object → same id — which is exactly the property we want.
    The request_id is **not** suitable: it's unique per call and would
    spawn a new chat for every tool invocation.
    """
    try:
        try:
            from mcp.shared.context import request_ctx  # type: ignore
        except ImportError:
            from mcp.server.lowlevel.server import request_ctx  # type: ignore
        ctx = request_ctx.get()
    except (LookupError, ImportError, AttributeError):
        return None
    if ctx is None:
        return None
    # Prefer a real session-id string if the SDK exposes one.
    for chain in (
        ("session", "session_id"),
        ("session", "_session_id"),
    ):
        cur: Any = ctx
        try:
            for attr in chain:
                cur = getattr(cur, attr)
            if cur is not None:
                return str(cur)
        except AttributeError:
            continue
    # Fallback: object-identity of the session object. Stable within a
    # connection, which is all we need for chat correlation.
    sess = getattr(ctx, "session", None)
    if sess is not None:
        return f"sess-{id(sess)}"
    return None


def _resolve_chat_id(storage: Storage) -> str:
    """Map the current MCP session (or 'no-session' sentinel) to a chat_id,
    creating a chats row on first sighting. Cache is per-Storage so
    multiple Storage instances (tests, parallel servers) don't share
    chat_ids that wouldn't exist in each other's databases.
    """
    session_id = _get_request_session_id()
    key = session_id if session_id else "__no_session__"
    cache = _get_session_chats(storage)
    cached = cache.get(key)
    if cached:
        return cached
    client_info = session_id[:64] if session_id else "no-session"
    try:
        chat_id = storage.begin_chat(client_info=client_info)
    except Exception as e:
        # Don't crash the tool call just because we can't open a chat —
        # log and fall back to a synthetic id (subsequent log writes
        # will probably fail too, but the tool itself still works).
        sys.stderr.write(f"[cairn log] begin_chat failed: {e}\n")
        sys.stderr.flush()
        return "chat-error"
    cache[key] = chat_id
    return chat_id


def _logged_tool(
    fn: Callable[..., Any],
    tool_name: str,
    storage: Storage,
) -> Callable[..., Any]:
    """Wrap a tool function with capture-everything logging. Uses
    functools.wraps so FastMCP's signature introspection still sees the
    inner function's typed parameters."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        chat_id = _resolve_chat_id(storage)
        operation_id: Optional[str] = None
        try:
            operation_id = storage.get_active_operation(chat_id)
        except Exception:
            # Treat as 'no active operation' if the lookup fails.
            operation_id = None

        # Arguments: prefer kwargs (how FastMCP dispatches); if there are
        # positionals (e.g. unit tests calling directly), capture them too.
        args_for_log: Any
        if args and not kwargs:
            args_for_log = list(args)
        elif args:
            args_for_log = {"_positional": list(args), **kwargs}
        else:
            args_for_log = kwargs
        arguments_json = _serialize_for_log(args_for_log)
        arguments_size = len(arguments_json.encode("utf-8"))

        started_at = datetime.now(timezone.utc).isoformat()
        started_perf = time.perf_counter()

        status = "ok"
        response_json: Optional[str] = None
        response_size: Optional[int] = None
        error_message: Optional[str] = None
        result: Any = None
        exc_to_reraise: Optional[BaseException] = None
        try:
            result = fn(*args, **kwargs)
            response_json = _serialize_for_log(result)
            response_size = len(response_json.encode("utf-8"))
        except Exception as e:
            status = "error"
            error_message = f"{type(e).__name__}: {e}"
            exc_to_reraise = e

        completed_at = datetime.now(timezone.utc).isoformat()
        duration_ms = int((time.perf_counter() - started_perf) * 1000)
        payload_size_warning = (
            arguments_size > 100_000
            or (response_size is not None and response_size > 100_000)
        )

        # Logging must never break tool execution.
        try:
            storage.log_tool_call(
                chat_id=chat_id,
                operation_id=operation_id,
                tool_name=tool_name,
                status=status,
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=duration_ms,
                arguments_json=arguments_json,
                response_json=response_json,
                arguments_size_bytes=arguments_size,
                response_size_bytes=response_size,
                error_message=error_message,
                payload_size_warning=payload_size_warning,
            )
        except Exception as log_e:
            sys.stderr.write(
                f"[cairn log] tool_call write failed for {tool_name}: {log_e}\n"
            )
            sys.stderr.flush()

        if exc_to_reraise is not None:
            raise exc_to_reraise
        return result

    return wrapper


def _logged_resource_read(
    reader: Callable[..., str],
    resource_uri: str,
    storage: Storage,
) -> Callable[..., str]:
    """Wrap a resource reader to log every fetch."""

    @functools.wraps(reader)
    def wrapper(*args: Any, **kwargs: Any) -> str:
        chat_id = _resolve_chat_id(storage)
        operation_id: Optional[str] = None
        try:
            operation_id = storage.get_active_operation(chat_id)
        except Exception:
            operation_id = None

        started_at = datetime.now(timezone.utc).isoformat()
        started_perf = time.perf_counter()
        content: Optional[str] = None
        content_size: Optional[int] = None
        try:
            content = reader(*args, **kwargs)
            content_size = len(content.encode("utf-8"))
            return content
        finally:
            duration_ms = int((time.perf_counter() - started_perf) * 1000)
            try:
                storage.log_resource_read(
                    chat_id=chat_id,
                    operation_id=operation_id,
                    resource_uri=resource_uri,
                    content_size_bytes=content_size,
                    started_at=started_at,
                    duration_ms=duration_ms,
                )
            except Exception as log_e:
                sys.stderr.write(
                    f"[cairn log] resource_read write failed for "
                    f"{resource_uri}: {log_e}\n"
                )
                sys.stderr.flush()

    return wrapper


def _wrap_all_tools_for_logging(mcp: FastMCP, storage: Storage) -> None:
    """Replace each registered tool's fn with a logged version. Run
    after registration so the wrap sweep catches every tool — including
    ones added in future versions, which need only register normally."""
    for name, tool in list(mcp._tool_manager._tools.items()):
        if name in _UNLOGGED_TOOLS:
            continue
        tool.fn = _logged_tool(tool.fn, name, storage)


def _wrap_all_resources_for_logging(mcp: FastMCP, storage: Storage) -> None:
    """Replace each registered resource's reader fn with a logged version."""
    for resource in list(mcp._resource_manager._resources.values()):
        if not isinstance(resource, FunctionResource):
            continue
        uri = str(resource.uri)
        # FunctionResource stores the reader fn at .fn
        resource.fn = _logged_resource_read(resource.fn, uri, storage)


# --- resources ----------------------------------------------------------------


_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


def _parse_skill_description(path: Path) -> Optional[str]:
    """Pull the ``description:`` field out of a markdown file's YAML frontmatter.

    Skills use a conventional front-matter block at the top:

        ---
        name: <name>
        description: |
          <multi-line text, 2-space indented>
        ---

    We surface this description as the MCP resource's description so it
    shows up in ``resources/list`` — that's what client LLMs see when
    deciding which skill applies to a request. Without this, every skill
    just gets ``"Skill procedure: <name>"`` which is useless for discovery.

    Returns the description (newline-wrapping flattened to spaces within
    paragraphs; paragraph breaks preserved as blank lines) or ``None`` if
    no frontmatter / no description field is parseable.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None
    lines = m.group(1).splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("description:"):
            inline = line[len("description:"):].strip()
            if inline == "|":
                # multi-line YAML literal block; collect following indented lines
                i += 1
                content: list[str] = []
                while i < len(lines):
                    nxt = lines[i]
                    if nxt.startswith("  "):
                        content.append(nxt[2:])
                    elif not nxt.strip():
                        content.append("")
                    else:
                        break
                    i += 1
                # Flatten line-wraps within each paragraph; keep paragraph breaks.
                paragraphs: list[str] = []
                current: list[str] = []
                for c in content:
                    if c.strip():
                        current.append(c.strip())
                    elif current:
                        paragraphs.append(" ".join(current))
                        current = []
                if current:
                    paragraphs.append(" ".join(current))
                return "\n\n".join(paragraphs) or None
            elif inline:
                return inline
            else:
                return None
        i += 1
    return None


def _add_file_resource(
    mcp: FastMCP,
    uri: str,
    resource_name: str,
    path: Path,
    description: str,
) -> None:
    """Register a markdown file as an MCP resource that re-reads on each call."""

    def reader(_path: Path = path) -> str:
        if not _path.exists():
            raise FileNotFoundError(f"{_path} no longer exists")
        return _path.read_text(encoding="utf-8")

    mcp.add_resource(
        FunctionResource(
            uri=AnyUrl(uri),
            name=resource_name,
            description=description,
            mime_type="text/markdown",
            fn=reader,
        )
    )


def _register_directory(
    mcp: FastMCP,
    dir_path: Path,
    scheme: str,
    fallback_description: "callable[[str], str]",
) -> None:
    """Scan a directory of markdown files and register each as an MCP resource.

    Used for skills/, schemas/, and guides/. Pulls each file's
    description from YAML frontmatter when present (so the description
    that shows up in resources/list is actionable and discoverable);
    falls back to a per-scheme stub otherwise. Files are re-read from
    disk on every resource fetch, so editing markdown takes effect
    without restarting.
    """
    if not dir_path.is_dir():
        log.warning("%s directory not found at %s", scheme, dir_path)
        return
    for md_file in sorted(dir_path.glob("*.md")):
        name = md_file.stem
        description = (
            _parse_skill_description(md_file)
            or fallback_description(name)
        )
        _add_file_resource(
            mcp,
            f"{scheme}://{name}",
            f"{scheme}-{name}",
            md_file,
            description,
        )


def _register_resources(
    mcp: FastMCP,
    skills_dir: Path,
    schemas_dir: Path,
    guides_dir: Path,
    constitution_path: Path,
) -> None:
    if constitution_path.exists():
        _add_file_resource(
            mcp,
            "constitution://main",
            "constitution",
            constitution_path,
            "Operating philosophy for the assistant. Load this when you need "
            "context on how to behave.",
        )
    else:
        log.warning("constitution file not found at %s", constitution_path)

    # Three parallel scanned directories. guides/ holds session-level
    # orientation documents (the overview, future FAQ/glossary etc.) —
    # things that aren't procedures (skills) or data shapes (schemas)
    # but help the LLM navigate the system.
    _register_directory(
        mcp, skills_dir, "skill",
        lambda n: f"Skill procedure: {n}",
    )
    _register_directory(
        mcp, schemas_dir, "schema",
        lambda n: f"Schema for the {n} entity type.",
    )
    _register_directory(
        mcp, guides_dir, "guide",
        lambda n: f"Guide: {n}",
    )


# --- module-level instance for `python -m meta_assistant` ---------------------


def run(transport: str | None = None) -> None:
    # Logging must go to stderr: under the stdio transport, stdout carries
    # the JSON-RPC wire and anything else printed there corrupts the stream.
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if transport is None:
        # The frozen binary is spawned by Claude Desktop over stdio (no port,
        # no bridge). A source checkout keeps the HTTP server it always had.
        transport = os.environ.get("META_TRANSPORT") or (
            "stdio" if FROZEN else "streamable-http"
        )
    mcp, _storage = build_server()
    if transport == "stdio":
        log.info("starting meta-assistant (stdio); db=%s", DB_PATH)
        mcp.run(transport=transport)
        # The client closed stdin: the session is over and every response has
        # already been written and read. Exit immediately, before CPython's
        # finalizer runs — a frozen (PyInstaller) build otherwise prints a
        # harmless but alarming "I/O operation on closed file" traceback when
        # it tries to flush the std streams the anyio transport already closed.
        # os._exit is safe here precisely because there is nothing left to
        # flush; any unsent tail would already have been lost inside mcp.run
        # when stdin hit EOF, independent of how we exit. (Source builds don't
        # surface the error; the early exit is harmless there too.)
        try:
            sys.stdout.flush()
        except (ValueError, OSError):
            pass
        os._exit(0)
    else:
        token = os.environ.get("META_AUTH_TOKEN") or None
        loopback = HOST in ("127.0.0.1", "localhost", "::1")
        if not loopback and not token:
            log.warning(
                "SECURITY: binding %s:%s on a NON-loopback address with NO auth "
                "token. Every tool — full read/write/delete of your personal "
                "data — is then reachable by anything on that network. Set "
                "META_AUTH_TOKEN to require a bearer token, or bind META_HOST to "
                "127.0.0.1.", HOST, PORT,
            )
        if token:
            # Route through the bearer-gated ASGI app so the token actually
            # protects the HTTP transport (FastMCP's own run() has no auth).
            from . import remote
            log.info(
                "starting meta-assistant on %s:%s [token required]; db=%s",
                HOST, PORT, DB_PATH,
            )
            remote.serve(host=HOST, port=PORT, token=token)
        else:
            log.info("starting meta-assistant on %s:%s; db=%s", HOST, PORT, DB_PATH)
            mcp.run(transport=transport)
