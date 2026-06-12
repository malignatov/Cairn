"""SQLite-backed storage for projects, decisions, drafts, and sources.

The MCP tools are thin wrappers over this module; all of the validation,
state transitions, and DB I/O live here.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("meta_assistant.storage")

# entity_type as used by the tool surface (singular form)
ENTITY_PROJECT = "project"
ENTITY_DECISION = "decision"
ENTITY_DRAFT = "draft"
ENTITY_SUGGESTION = "suggestion"
ENTITY_COMMITMENT = "commitment"
ENTITY_STAKEHOLDER = "stakeholder"
ENTITY_INTEREST = "interest"
ENTITY_CAPABILITY = "capability"
ENTITY_RESOURCE = "resource"
ENTITY_PRINCIPLE = "principle"
ENTITY_IDEA = "idea"

# what state_write / state_query accept
VALID_ENTITY_TYPES = {
    ENTITY_PROJECT,
    ENTITY_DECISION,
    ENTITY_SUGGESTION,
    ENTITY_COMMITMENT,
    ENTITY_STAKEHOLDER,
    ENTITY_INTEREST,
    ENTITY_CAPABILITY,
    ENTITY_RESOURCE,
    ENTITY_PRINCIPLE,
    ENTITY_IDEA,
}
# what state_update accepts (suggestions transition via disposition_suggestion;
# commitments via complete_commitment, but state_update is still legal for
# field touch-ups like editing a typo)
VALID_UPDATE_TYPES = {
    ENTITY_PROJECT,
    ENTITY_DECISION,
    ENTITY_COMMITMENT,
    ENTITY_STAKEHOLDER,
    ENTITY_INTEREST,
    ENTITY_CAPABILITY,
    ENTITY_RESOURCE,
    ENTITY_PRINCIPLE,
    ENTITY_IDEA,
}
# what state_draft / commit_draft / reject_draft / amend_draft accept. v4 adds
# capability and resource because inventory_intake uses draft/commit for bulk import.
VALID_DRAFT_ENTITY_TYPES = {
    ENTITY_PROJECT,
    ENTITY_DECISION,
    ENTITY_COMMITMENT,
    ENTITY_STAKEHOLDER,
    ENTITY_INTEREST,
    ENTITY_CAPABILITY,
    ENTITY_RESOURCE,
    ENTITY_PRINCIPLE,
    ENTITY_IDEA,
}
VALID_LINK_TARGETS = {
    ENTITY_PROJECT,
    ENTITY_DECISION,
    ENTITY_DRAFT,
    ENTITY_COMMITMENT,
    ENTITY_STAKEHOLDER,
    ENTITY_INTEREST,
    ENTITY_CAPABILITY,
    ENTITY_RESOURCE,
    ENTITY_PRINCIPLE,
    ENTITY_IDEA,
}
VALID_PROJECT_STATUS = {"active", "snoozed", "dormant", "killed"}
VALID_DRAFT_STATUS = {"pending", "committed", "rejected", "amended"}

# ---- v8: ideas -------------------------------------------------------------
# An idea is a retained spark, pre-commitment. The lifecycle is a maturation
# ladder: spark → exploring → someday (deliberately parked) → promoted
# (graduated into a project, terminal) / released (let go, terminal). "Heat"
# is DERIVED at read time from last_touched_at, never stored.
VALID_IDEA_STATUS = {"spark", "exploring", "someday", "promoted", "released"}
# statuses the cooling-idea anti-rot scanner watches (someday is deliberate,
# the two terminal states are done — none of those can "cool")
COOLABLE_IDEA_STATUS = {"spark", "exploring"}

# ---- v7: principles --------------------------------------------------------
VALID_PRINCIPLE_STATUS = {"active", "retired"}
VALID_PRINCIPLE_CHANGE_TYPES = {"created", "rewritten", "reranked", "retired"}
# decision↔principle relationship, the three-case model:
#   aligned             — the decision honors the principle (logged quietly)
#   justified_override  — departs from X to serve a HIGHER-ranked Y (healthy)
#   unjustified_departure — departs with no higher principle invoked (the
#                           only case the scanner cares about, and only when
#                           it repeats)
VALID_EVAL_RELATIONSHIPS = {
    "aligned", "justified_override", "unjustified_departure",
}
VALID_EVAL_SEVERITY = {"low", "medium", "high"}
VALID_SOURCE_TYPES = {"chat", "pdf", "paste", "file"}

# ---- v3.1: commitments, stakeholders, interests ----------------------------

VALID_COMMITMENT_STATUS = {"open", "done", "dropped"}
VALID_INTEREST_STATUS = {"active", "dormant", "retired"}
VALID_STAKEHOLDER_STRENGTH = {"close", "regular", "weak"}

# ---- v4: capabilities & resources ------------------------------------------

VALID_CAPABILITY_CATEGORIES = {
    "license", "certification", "credential",
    "skill", "hardware", "access", "other",
}
# Only these categories can carry a `level` value. Hardware doesn't atrophy
# into novice/expert grades; a license is binary (you have it or you don't).
LEVELED_CAPABILITY_CATEGORIES = {"skill", "credential", "other"}
VALID_CAPABILITY_LEVELS = {"novice", "intermediate", "advanced", "expert"}
# Levels are ordered so "do I meet a required_level?" reduces to integer
# comparison. Keep this in sync with VALID_CAPABILITY_LEVELS.
CAPABILITY_LEVEL_ORDER = {
    "novice": 0, "intermediate": 1, "advanced": 2, "expert": 3,
}
VALID_CAPABILITY_STATUS = {"active", "lapsed", "retired"}
# Only these categories meaningfully atrophy when not exercised.
# Licenses don't atrophy (they have hard expirations); hardware doesn't
# atrophy unless physically broken (not something the scanner can know).
ATROPHYING_CAPABILITY_CATEGORIES = {"skill", "certification", "access"}

VALID_RESOURCE_CATEGORIES = {"money", "time", "energy", "other"}

VALID_SUGGESTION_KINDS = {"opportunity", "risk", "question", "pattern", "blindspot"}
# kinds that must cite at least one external source
SUGGESTION_KINDS_REQUIRING_EXTERNAL = {"opportunity", "risk"}
VALID_SUGGESTION_STATUS = {"pending", "accepted", "rejected", "deferred"}
VALID_SUGGESTION_DISPOSITIONS = {"accepted", "rejected", "deferred"}

# ---- v3: Strack Strategic Life Portfolio + PERMA-V --------------------------

VALID_SLA = {
    "relationships",
    "body_mind_spirit",
    "community_society",
    "job_learning_finances",
    "interests_entertainment",
    "personal_care",
}

VALID_PERMA_DIMENSIONS = {
    "positive_emotions",
    "engagement",
    "relationships",
    "meaning",
    "achievement",
    "vitality",
}

# Assessment session types accepted by ``start_assessment_session``.
#  - ``full``       : a complete SLU portfolio re-rating (assess_life_portfolio).
#  - ``patch``      : an SLU portfolio session that inherits unchanged ratings
#                     from an earlier full/patch session (patch_portfolio).
#  - ``great_life`` : a PERMA-V intake (define_great_life). Unchanged from v3.
#
# ``slu_portfolio`` is kept as a legacy alias of ``full`` so v3-era callers
# (and persisted data) keep working without surprise.
VALID_ASSESSMENT_TYPES = {"full", "patch", "great_life", "slu_portfolio"}

# What ends up in ``slu_assessments.session_type`` on a stored rating:
#  - ``full``         : part of a full session.
#  - ``patch``        : part of a patch session (the row may be inherited or new).
#  - ``quick_update`` : a one-off rating change with no session at all
#                       (session_id IS NULL). Visible in per-SLU trajectories
#                       but excluded from portfolio snapshot comparisons.
VALID_SLU_SESSION_TYPES = {"full", "patch", "quick_update"}

# session_types that count as a "snapshot" for portfolio comparison purposes.
# scan_life_units' quadrant_shift signal and compute_portfolio_quadrants both
# restrict themselves to these.
SNAPSHOT_SESSION_TYPES = {"full", "patch"}
VALID_QUADRANTS = {"upper_right", "upper_left", "lower_right", "lower_left"}
VALID_OBS_SIGNALS = {
    "silence",
    "erosion",
    "mismatch",
    "avoidance",
    "quadrant_shift",
    "compound_debt",
}
VALID_OBS_SEVERITY = {"low", "medium", "high"}
# entity types that can be SLU-tagged (polymorphic; no FK)
VALID_SLU_LINK_ENTITY_TYPES = {
    ENTITY_PROJECT,
    ENTITY_DECISION,
    ENTITY_SUGGESTION,
    ENTITY_COMMITMENT,
    ENTITY_STAKEHOLDER,
    ENTITY_INTEREST,
    ENTITY_CAPABILITY,
    ENTITY_RESOURCE,
    ENTITY_PRINCIPLE,
    ENTITY_IDEA,
}

# Threshold: treat 6+ as "high" for both importance and satisfaction.
QUADRANT_HIGH_THRESHOLD = 6

# Strack's 16 SLUs, seeded on first boot. (name, area, description)
SEED_LIFE_UNITS: list[tuple[str, str, str]] = [
    # SLA 1 — Relationships
    (
        "Significant other",
        "relationships",
        "Your romantic partner, if you have one; the central intimate "
        "relationship in your life.",
    ),
    (
        "Family",
        "relationships",
        "Family of origin, children, extended family. The bonds defined by "
        "blood or commitment.",
    ),
    (
        "Friendship",
        "relationships",
        "The chosen kin. Close friends and the broader social circle.",
    ),
    # SLA 2 — Body, Mind, and Spirituality
    (
        "Physical health",
        "body_mind_spirit",
        "Movement, fitness, body care, medical attention.",
    ),
    (
        "Mental health",
        "body_mind_spirit",
        "Emotional wellbeing, psychological care, inner life.",
    ),
    (
        "Spirituality",
        "body_mind_spirit",
        "Religious practice, contemplative practice, the search for "
        "connection to something larger.",
    ),
    # SLA 3 — Community and Society
    (
        "Community involvement",
        "community_society",
        "Local participation: neighborhood, congregation, school, club, "
        "mutual aid.",
    ),
    (
        "Societal engagement",
        "community_society",
        "Broader civic action: politics, advocacy, voluntary work at scale.",
    ),
    # SLA 4 — Job, Learning, and Finances
    (
        "Job and career",
        "job_learning_finances",
        "Paid work and professional identity.",
    ),
    (
        "Education and learning",
        "job_learning_finances",
        "Deliberate skill development, study, intellectual growth.",
    ),
    (
        "Finances",
        "job_learning_finances",
        "Earning, saving, investing, planning for the future, managing money.",
    ),
    # SLA 5 — Interests and Entertainment
    (
        "Hobbies and interests",
        "interests_entertainment",
        "Activities pursued for their own sake — creative, recreational, "
        "exploratory.",
    ),
    (
        "Online entertainment",
        "interests_entertainment",
        "Time spent on screens for leisure: social media, streaming, browsing.",
    ),
    (
        "Offline entertainment",
        "interests_entertainment",
        "Leisure away from screens: reading, games, gatherings, outings.",
    ),
    # SLA 6 — Personal Care
    (
        "Physiological needs",
        "personal_care",
        "Sleep, eating, recovery, the basic biological maintenance of being "
        "alive.",
    ),
    (
        "Activities of daily living",
        "personal_care",
        "Errands, chores, commuting, paperwork — the operational overhead of "
        "an adult life.",
    ),
]

# singular entity_type -> plural table name
_TABLE = {
    ENTITY_PROJECT: "projects",
    ENTITY_DECISION: "decisions",
    ENTITY_SUGGESTION: "suggestions",
    ENTITY_COMMITMENT: "commitments",
    ENTITY_STAKEHOLDER: "stakeholders",
    ENTITY_INTEREST: "interests",
    ENTITY_CAPABILITY: "capabilities",
    ENTITY_RESOURCE: "resources",
    ENTITY_PRINCIPLE: "principles",
    ENTITY_IDEA: "ideas",
}

# ---- readability: resolve opaque FK UUIDs to human labels at read time -----
# Stored cross-references are UUIDs (correct as canonical keys), but a human
# reading a row shouldn't have to decode them. Every entity read resolves its
# foreign keys to a `<field>_name` label ALONGSIDE the id — never replacing
# it (the LLM still needs the id for tool calls), and never stored (resolved
# fresh each read, so a rename never leaves a stale label behind).

# the human-readable "display" column for each table
_DISPLAY_FIELD = {
    "projects": "name",
    "decisions": "what",
    "principles": "name",
    "commitments": "what",
    "stakeholders": "name",
    "interests": "topic",
    "capabilities": "name",
    "resources": "name",
    "ideas": "title",
    "life_units": "name",
    "suggestions": "content",
    "slu_observations": "signal",
}
_LABEL_MAXLEN = 80  # long free-text labels (what / content / title) get clipped

# entity_type → table for label resolution, beyond the writable _TABLE set
# (link rows and suggestion anchors reference these non-writable types too)
_ENTITY_TYPE_TABLE_EXTRA = {
    "life_unit": "life_units",
    "slu_observation": "slu_observations",
    "great_life_dimension": "great_life_dimensions",
    "source": "sources",
    "draft": "drafts",
}

# per-entity FK columns that query() resolves to a label
_QUERY_FK_LABELS = {
    ENTITY_DECISION:   [("project_id", "projects", "project_name")],
    ENTITY_COMMITMENT: [("related_project_id", "projects", "related_project_name")],
    ENTITY_IDEA:       [("promoted_to_project_id", "projects", "promoted_to_project_name")],
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    last_touched TEXT NOT NULL,
    snooze_until TEXT,
    kill_criteria TEXT,
    revive_criteria TEXT
);

CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    what TEXT NOT NULL,
    rationale TEXT NOT NULL,
    alternatives_considered TEXT,
    decided_at TEXT NOT NULL,
    revisit_at TEXT,
    outcome TEXT,
    FOREIGN KEY (project_id) REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS drafts (
    id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    data TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    committed_at TEXT,
    rejection_reason TEXT,
    session_id TEXT,
    committed_entity_id TEXT
);

CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_links (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    FOREIGN KEY (source_id) REFERENCES sources(id)
);

CREATE TABLE IF NOT EXISTS suggestions (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    rationale TEXT NOT NULL,
    linked_evidence TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    disposition_reason TEXT,
    dispositioned_at TEXT,
    session_id TEXT
);

-- v3 ----------------------------------------------------------------

CREATE TABLE IF NOT EXISTS life_units (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    area TEXT NOT NULL,
    description TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    custom INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS slu_assessments (
    id TEXT PRIMARY KEY,
    life_unit_id TEXT NOT NULL,
    assessed_at TEXT NOT NULL,
    importance INTEGER NOT NULL,
    satisfaction INTEGER NOT NULL,
    hours_per_week REAL,
    quadrant TEXT NOT NULL,
    notes TEXT,
    session_id TEXT,
    -- v5.1: session_type discriminates full / patch / quick_update.
    -- session_id IS NULL when session_type = 'quick_update'.
    session_type TEXT NOT NULL DEFAULT 'full',
    -- v5.1: for patch sessions, points to the session whose unchanged
    -- ratings were carried forward into this one.
    inherited_from_session_id TEXT,
    FOREIGN KEY (life_unit_id) REFERENCES life_units(id)
);

CREATE TABLE IF NOT EXISTS great_life_dimensions (
    id TEXT PRIMARY KEY,
    dimension TEXT NOT NULL,
    assessed_at TEXT NOT NULL,
    importance INTEGER NOT NULL,
    satisfaction INTEGER NOT NULL,
    notes TEXT,
    session_id TEXT
);

CREATE TABLE IF NOT EXISTS entity_slu_links (
    id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    life_unit_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (life_unit_id) REFERENCES life_units(id)
);

CREATE TABLE IF NOT EXISTS slu_observations (
    id TEXT PRIMARY KEY,
    life_unit_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    signal TEXT NOT NULL,
    severity TEXT NOT NULL,
    evidence TEXT NOT NULL,
    notes TEXT,
    FOREIGN KEY (life_unit_id) REFERENCES life_units(id)
);

-- v3.1 --------------------------------------------------------------

CREATE TABLE IF NOT EXISTS commitments (
    id TEXT PRIMARY KEY,
    what TEXT NOT NULL,
    to_whom TEXT NOT NULL,
    due_at TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    related_project_id TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    outcome TEXT,
    dropped_reason TEXT,
    FOREIGN KEY (related_project_id) REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS stakeholders (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT,
    relationship_strength TEXT,
    last_contact_at TEXT,
    owed_action TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stakeholder_project_links (
    id TEXT PRIMARY KEY,
    stakeholder_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    role_in_project TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (stakeholder_id) REFERENCES stakeholders(id),
    FOREIGN KEY (project_id) REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS interests (
    id TEXT PRIMARY KEY,
    topic TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    first_observed_at TEXT NOT NULL,
    last_observed_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- v4 ----------------------------------------------------------------

CREATE TABLE IF NOT EXISTS capabilities (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    description TEXT,
    level TEXT,
    acquired_at TEXT,
    expires_at TEXT,
    renewal_required INTEGER NOT NULL DEFAULT 0,
    renewal_lead_time_days INTEGER DEFAULT 90,
    last_exercised_at TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    unlocks TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resources (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    unit TEXT NOT NULL,
    current_quantity REAL NOT NULL,
    as_of TEXT NOT NULL,
    floor REAL,
    burn_rate REAL,
    replenish_rate REAL,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS capability_requirements (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    capability_id TEXT,
    resource_id TEXT,
    required_level TEXT,
    required_amount REAL,
    notes TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id),
    FOREIGN KEY (capability_id) REFERENCES capabilities(id),
    FOREIGN KEY (resource_id) REFERENCES resources(id)
);

-- v6: end-to-end logging -------------------------------------------

CREATE TABLE IF NOT EXISTS chats (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    client_info TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chats_started_at_idx ON chats(started_at);

CREATE TABLE IF NOT EXISTS operations (
    id TEXT PRIMARY KEY,
    chat_id TEXT NOT NULL,
    skill_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'in_progress',
    started_at TEXT NOT NULL,
    completed_at TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (chat_id) REFERENCES chats(id)
);
CREATE INDEX IF NOT EXISTS operations_chat_idx     ON operations(chat_id);
CREATE INDEX IF NOT EXISTS operations_skill_idx    ON operations(skill_name);
CREATE INDEX IF NOT EXISTS operations_status_idx   ON operations(status);
CREATE INDEX IF NOT EXISTS operations_started_idx  ON operations(started_at);

CREATE TABLE IF NOT EXISTS tool_calls (
    id TEXT PRIMARY KEY,
    chat_id TEXT NOT NULL,
    operation_id TEXT,
    tool_name TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    duration_ms INTEGER,
    arguments_json TEXT,
    response_json TEXT,
    arguments_size_bytes INTEGER,
    response_size_bytes INTEGER,
    error_message TEXT,
    payload_size_warning INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (chat_id) REFERENCES chats(id),
    FOREIGN KEY (operation_id) REFERENCES operations(id)
);
CREATE INDEX IF NOT EXISTS tool_calls_chat_idx    ON tool_calls(chat_id);
CREATE INDEX IF NOT EXISTS tool_calls_op_idx      ON tool_calls(operation_id);
CREATE INDEX IF NOT EXISTS tool_calls_name_idx    ON tool_calls(tool_name);
CREATE INDEX IF NOT EXISTS tool_calls_status_idx  ON tool_calls(status);
CREATE INDEX IF NOT EXISTS tool_calls_started_idx ON tool_calls(started_at);

CREATE TABLE IF NOT EXISTS resource_reads (
    id TEXT PRIMARY KEY,
    chat_id TEXT NOT NULL,
    operation_id TEXT,
    resource_uri TEXT NOT NULL,
    content_size_bytes INTEGER,
    started_at TEXT NOT NULL,
    duration_ms INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY (chat_id) REFERENCES chats(id),
    FOREIGN KEY (operation_id) REFERENCES operations(id)
);
CREATE INDEX IF NOT EXISTS resource_reads_chat_idx    ON resource_reads(chat_id);
CREATE INDEX IF NOT EXISTS resource_reads_op_idx      ON resource_reads(operation_id);
CREATE INDEX IF NOT EXISTS resource_reads_uri_idx     ON resource_reads(resource_uri);
CREATE INDEX IF NOT EXISTS resource_reads_started_idx ON resource_reads(started_at);

-- v7: principles ------------------------------------------------------------
-- The user's ranked operating rules — prescriptive (govern how the user
-- chooses), as opposed to PERMA-V which is descriptive (measures how life
-- is going). rank is unique among ACTIVE principles; uniqueness is enforced
-- in application logic (not a DB constraint) because retired rows keep their
-- historical rank value and would otherwise collide.
CREATE TABLE IF NOT EXISTS principles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    rank INTEGER NOT NULL,
    provenance TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    last_revised_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS principles_status_rank_idx ON principles(status, rank);

-- Append-only revision log. Every change to a principle is recorded with
-- reasoning and the before/after state preserved — amending a principle is
-- ceremonial, like amending a constitution.
CREATE TABLE IF NOT EXISTS principle_revisions (
    id TEXT PRIMARY KEY,
    principle_id TEXT NOT NULL,
    revised_at TEXT NOT NULL,
    change_type TEXT NOT NULL,
    prior_state TEXT,
    new_state TEXT,
    rationale TEXT NOT NULL,
    FOREIGN KEY (principle_id) REFERENCES principles(id)
);
CREATE INDEX IF NOT EXISTS principle_revisions_pid_idx ON principle_revisions(principle_id);

-- Bridge: how a committed decision relates to a principle. Three cases:
-- aligned / justified_override / unjustified_departure. Only departures and
-- overrides are always recorded; alignment only where notable.
CREATE TABLE IF NOT EXISTS decision_principle_evaluations (
    id TEXT PRIMARY KEY,
    decision_id TEXT NOT NULL,
    principle_id TEXT NOT NULL,
    relationship TEXT NOT NULL,
    overrides_principle_id TEXT,
    severity TEXT,
    rationale TEXT,
    evaluated_at TEXT NOT NULL,
    FOREIGN KEY (decision_id) REFERENCES decisions(id),
    FOREIGN KEY (principle_id) REFERENCES principles(id),
    FOREIGN KEY (overrides_principle_id) REFERENCES principles(id)
);
CREATE INDEX IF NOT EXISTS dpe_decision_idx  ON decision_principle_evaluations(decision_id);
CREATE INDEX IF NOT EXISTS dpe_principle_idx ON decision_principle_evaluations(principle_id);
CREATE INDEX IF NOT EXISTS dpe_relationship_idx ON decision_principle_evaluations(relationship);

-- v8: ideas -----------------------------------------------------------------
-- A retained spark, pre-commitment. The point of this layer is maturation,
-- not storage: an idea must move (warm toward a project, or be parked/
-- released on purpose) or it rots. "Heat" is DERIVED from last_touched_at at
-- read time — never stored — and only spark/exploring ideas can "cool".
CREATE TABLE IF NOT EXISTS ideas (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    body TEXT,
    status TEXT NOT NULL DEFAULT 'spark',
    captured_at TEXT NOT NULL,
    last_touched_at TEXT NOT NULL,
    promoted_to_project_id TEXT,
    released_reason TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (promoted_to_project_id) REFERENCES projects(id)
);
CREATE INDEX IF NOT EXISTS ideas_status_idx       ON ideas(status);
CREATE INDEX IF NOT EXISTS ideas_last_touched_idx ON ideas(last_touched_at);
"""


# ---- v5: full-text search index ---------------------------------------------

# (entity_type, table, title_expr, body_expr, created_at_expr)
# Expressions use ``NEW.`` prefixes so they drop straight into trigger
# bodies. For backfill, NEW. is rewritten to the table alias.
SEARCHABLE_ENTITIES: list[tuple[str, str, str, str, str]] = [
    ("project", "projects",
     "NEW.name",
     "coalesce(NEW.description,'') || ' ' || coalesce(NEW.kill_criteria,'') || ' ' || coalesce(NEW.revive_criteria,'')",
     "NEW.created_at"),
    ("decision", "decisions",
     "NEW.what",
     "coalesce(NEW.rationale,'') || ' ' || coalesce(NEW.alternatives_considered,'') || ' ' || coalesce(NEW.outcome,'')",
     "NEW.decided_at"),
    ("commitment", "commitments",
     "NEW.what",
     "coalesce(NEW.dropped_reason,'')",
     "NEW.created_at"),
    ("stakeholder", "stakeholders",
     "NEW.name",
     "coalesce(NEW.role,'') || ' ' || coalesce(NEW.notes,'')",
     "NEW.created_at"),
    ("interest", "interests",
     "NEW.topic",
     "coalesce(NEW.description,'')",
     "NEW.created_at"),
    ("capability", "capabilities",
     "NEW.name",
     "coalesce(NEW.description,'') || ' ' || coalesce(NEW.unlocks,'') || ' ' || coalesce(NEW.notes,'')",
     "NEW.created_at"),
    ("resource", "resources",
     "NEW.name",
     "coalesce(NEW.notes,'')",
     "NEW.created_at"),
    ("slu_observation", "slu_observations",
     "NEW.signal",
     "coalesce(NEW.notes,'')",
     "NEW.observed_at"),
    ("suggestion", "suggestions",
     "substr(NEW.content, 1, 80)",
     "coalesce(NEW.rationale,'')",
     "NEW.created_at"),
    ("source", "sources",
     "NEW.source_type",
     "coalesce(NEW.content,'')",
     "NEW.created_at"),
    ("life_unit", "life_units",
     "NEW.name",
     "coalesce(NEW.description,'')",
     "NEW.created_at"),
    ("great_life_dimension", "great_life_dimensions",
     "NEW.dimension",
     "coalesce(NEW.notes,'')",
     "NEW.assessed_at"),
    ("slu_assessment", "slu_assessments",
     "(SELECT name FROM life_units WHERE id = NEW.life_unit_id) || ' ' || NEW.assessed_at",
     "coalesce(NEW.notes,'')",
     "NEW.assessed_at"),
    ("principle", "principles",
     "NEW.name",
     "coalesce(NEW.description,'') || ' ' || coalesce(NEW.provenance,'')",
     "NEW.created_at"),
    ("idea", "ideas",
     "NEW.title",
     "coalesce(NEW.body,'')",
     "NEW.captured_at"),
]

SEARCHABLE_ENTITY_TYPES = {e[0] for e in SEARCHABLE_ENTITIES}

# Heuristic: does the user's query contain FTS5 operators we should
# pass through unwrapped? Otherwise we treat the whole thing as a
# phrase to avoid surprise behavior from punctuation or stray keywords.
_FTS5_ADVANCED_RE = re.compile(r" (AND|OR|NOT) |\*$")


def _build_search_schema() -> str:
    """Construct the FTS5 virtual table + sync triggers for every
    searchable entity. Returned as a SQL string suitable for executescript.
    """
    parts: list[str] = [
        "\n-- v5: full-text search index ----------------------------------\n",
        "CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(\n"
        "  entity_type UNINDEXED,\n"
        "  entity_id UNINDEXED,\n"
        "  title,\n"
        "  body,\n"
        "  created_at UNINDEXED,\n"
        "  tokenize = 'unicode61 remove_diacritics 2'\n"
        ");\n",
    ]
    for entity_type, table, title, body, created in SEARCHABLE_ENTITIES:
        parts.append(f"""
CREATE TRIGGER IF NOT EXISTS {table}_search_insert AFTER INSERT ON {table}
BEGIN
    INSERT INTO search_index(entity_type, entity_id, title, body, created_at)
    VALUES ('{entity_type}', NEW.id, {title}, {body}, {created});
END;

CREATE TRIGGER IF NOT EXISTS {table}_search_update AFTER UPDATE ON {table}
BEGIN
    DELETE FROM search_index WHERE entity_type = '{entity_type}' AND entity_id = OLD.id;
    INSERT INTO search_index(entity_type, entity_id, title, body, created_at)
    VALUES ('{entity_type}', NEW.id, {title}, {body}, {created});
END;

CREATE TRIGGER IF NOT EXISTS {table}_search_delete AFTER DELETE ON {table}
BEGIN
    DELETE FROM search_index WHERE entity_type = '{entity_type}' AND entity_id = OLD.id;
END;
""")
    return "".join(parts)


SCHEMA += _build_search_schema()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


def _require_entity_type(
    entity_type: str,
    allowed: set[str] = VALID_ENTITY_TYPES,
) -> str:
    if entity_type not in allowed:
        raise ValueError(
            f"entity_type must be one of {sorted(allowed)}, got {entity_type!r}"
        )
    return _TABLE[entity_type]


def _deserialize_suggestion(row: dict) -> dict:
    """Suggestions store linked_evidence as a JSON blob; unpack it."""
    row = dict(row)
    if isinstance(row.get("linked_evidence"), str):
        row["linked_evidence"] = json.loads(row["linked_evidence"])
    return row


class Storage:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            # Migrations FIRST — they may rename tables that the SCHEMA
            # block below would otherwise try to recreate empty.
            self._run_migrations(conn)
            conn.executescript(SCHEMA)
            # Backfill the v5 FTS5 search index from rows that predate it.
            # No-op on already-indexed DBs and on fresh empty DBs.
            self._seed_search_index(conn)
        self._seed_life_units()

    # ---- migrations --------------------------------------------------------

    def _run_migrations(self, conn: sqlite3.Connection) -> None:
        """Apply schema migrations in order. Each migration is idempotent
        so re-running on an already-migrated DB is a no-op."""
        self._migrate_v3_3_rename_idea_proposals_to_suggestions(conn)
        self._migrate_v5_1_session_type(conn)
        self._migrate_v6_2_rename_proposals_to_drafts(conn)

    def _migrate_v5_1_session_type(self, conn: sqlite3.Connection) -> None:
        """v5.1: add ``session_type`` and ``inherited_from_session_id`` to
        ``slu_assessments``.

        Pre-v5.1 rows had no session_type concept — they all came from the
        original full assessment flow. After this migration they read as
        ``session_type='full'``, which is honest: that's how they were
        produced. New patch / quick_update flows write the new values
        directly.
        """
        tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        # Fresh DBs don't have slu_assessments yet — SCHEMA creates it
        # with the new columns directly, so nothing to do here.
        if "slu_assessments" not in tables:
            return
        cols = {
            r[1] for r in conn.execute("PRAGMA table_info(slu_assessments)")
        }
        if "session_type" not in cols:
            # ALTER TABLE ADD COLUMN with DEFAULT also backfills every
            # existing row with that default — exactly what we want.
            conn.execute(
                "ALTER TABLE slu_assessments ADD COLUMN "
                "session_type TEXT NOT NULL DEFAULT 'full'"
            )
        if "inherited_from_session_id" not in cols:
            conn.execute(
                "ALTER TABLE slu_assessments ADD COLUMN "
                "inherited_from_session_id TEXT"
            )

    # NOTE: the literal strings 'idea_proposal' and 'idea_proposals' below
    # MUST remain as-is — they are the *old* names this migration detects.
    # Don't run a find-and-replace through this function during any future
    # rename pass; the migration depends on knowing what to rename FROM.
    def _migrate_v3_3_rename_idea_proposals_to_suggestions(
        self, conn: sqlite3.Connection
    ) -> None:
        """v3.3: rename ``idea_proposals`` table to ``suggestions``.

        Also rewrites the ``entity_type`` value in any cross-reference
        tables that stored the old name. Idempotent — safe to run on a
        DB that has already been migrated, or on a fresh DB that never
        had the old table.
        """
        tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "idea_proposals" in tables and "suggestions" not in tables:
            conn.execute("ALTER TABLE idea_proposals RENAME TO suggestions")
        for table in ("entity_slu_links", "source_links", "proposals"):
            if table in tables:
                conn.execute(
                    f"UPDATE {table} SET entity_type = 'suggestion' "
                    f"WHERE entity_type = 'idea_proposal'"
                )

    # NOTE: the literal strings 'proposals' and 'proposal' below MUST remain
    # as-is — they are the *old* names this migration detects. Don't run a
    # find-and-replace through this function during any future rename pass;
    # the migration depends on knowing what to rename FROM. (Same footgun as
    # v3.3 above.)
    def _migrate_v6_2_rename_proposals_to_drafts(
        self, conn: sqlite3.Connection
    ) -> None:
        """v6.2: rename the ``proposals`` table to ``drafts``.

        The capture-flow staging table was named ``proposals``, a near-synonym
        of ``suggestions`` (the proactive-inbox entity) that muddied the tool
        surface. ``drafts`` carries the right connotation — incomplete, awaiting
        the user's commit — with no overlap.

        Also rewrites the ``entity_type`` value in ``source_links`` rows that
        pointed at a draft as a link target (old value ``'proposal'``).
        Idempotent — safe on an already-migrated DB or a fresh DB that never
        had the old table.
        """
        tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "proposals" in tables and "drafts" not in tables:
            conn.execute("ALTER TABLE proposals RENAME TO drafts")
        if "source_links" in tables:
            conn.execute(
                "UPDATE source_links SET entity_type = 'draft' "
                "WHERE entity_type = 'proposal'"
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    # ---- label resolution (readability) ------------------------------------

    def _label_for(
        self, conn: sqlite3.Connection, table: Optional[str], id: Optional[str]
    ) -> Optional[str]:
        """The human-readable label for a row, given its table and id. Returns
        None for a null id, an unknown table, or a missing row. Long
        free-text labels are clipped to _LABEL_MAXLEN."""
        if not id or table not in _DISPLAY_FIELD:
            return None
        col = _DISPLAY_FIELD[table]
        row = conn.execute(
            f"SELECT {col} AS v FROM {table} WHERE id = ?", (id,)
        ).fetchone()
        if not row or row["v"] is None:
            return None
        v = str(row["v"])
        return v if len(v) <= _LABEL_MAXLEN else v[: _LABEL_MAXLEN - 1] + "…"

    def _label_for_entity(
        self, conn: sqlite3.Connection, entity_type: Optional[str], id: Optional[str]
    ) -> Optional[str]:
        """Label for a polymorphic (entity_type, id) reference — used by link
        rows and suggestion anchors, whose target type is data, not fixed."""
        if not entity_type:
            return None
        table = _TABLE.get(entity_type) or _ENTITY_TYPE_TABLE_EXTRA.get(entity_type)
        return self._label_for(conn, table, id)

    def _enrich_suggestion_anchors(
        self, conn: sqlite3.Connection, sug: dict
    ) -> dict:
        """Add a resolved `label` to each internal_anchor of a suggestion, so
        the anchors read as names rather than bare (entity_type, id) pairs."""
        ev = sug.get("linked_evidence")
        if isinstance(ev, dict):
            for anchor in ev.get("internal_anchors") or []:
                if isinstance(anchor, dict):
                    anchor["label"] = self._label_for_entity(
                        conn, anchor.get("entity_type"), anchor.get("entity_id")
                    )
        return sug

    # ---- state_query --------------------------------------------------------

    def query(self, entity_type: str, filters: Optional[dict] = None) -> list[dict]:
        table = _require_entity_type(entity_type)
        filters = filters or {}
        # Interests get an auto-derived source_count from source_links —
        # the field is computed at read time so a source-attach is never
        # out of sync with the displayed count.
        if entity_type == ENTITY_INTEREST:
            sql = (
                "SELECT i.*, "
                "(SELECT COUNT(*) FROM source_links sl "
                " WHERE sl.entity_type = 'interest' AND sl.entity_id = i.id"
                ") AS source_count "
                "FROM interests i"
            )
        else:
            sql = f"SELECT * FROM {table}"
        params: list[Any] = []
        if filters:
            clauses = []
            for k, v in filters.items():
                if not k.replace("_", "").isalnum():
                    raise ValueError(f"invalid filter key: {k!r}")
                clauses.append(f"{k} = ?")
                params.append(v)
            sql += " WHERE " + " AND ".join(clauses)
        with self._connect() as conn:
            try:
                rows = conn.execute(sql, params).fetchall()
            except sqlite3.OperationalError as e:
                raise ValueError(f"invalid query: {e}") from e
        if entity_type == ENTITY_SUGGESTION:
            result = [_deserialize_suggestion(r) for r in rows]
        else:
            result = [dict(r) for r in rows]
        # Readability: resolve FK ids → `<field>_name` labels, and suggestion
        # anchors → labels. Done in one connection over the result set.
        fk = _QUERY_FK_LABELS.get(entity_type)
        if fk or entity_type == ENTITY_SUGGESTION:
            with self._connect() as conn:
                for row in result:
                    for id_field, ref_table, label_key in (fk or []):
                        row[label_key] = self._label_for(
                            conn, ref_table, row.get(id_field)
                        )
                    if entity_type == ENTITY_SUGGESTION:
                        self._enrich_suggestion_anchors(conn, row)
        return result

    # ---- state_write --------------------------------------------------------

    def write(self, entity_type: str, data: dict) -> dict:
        _require_entity_type(entity_type)
        # slu_links is a v3 convenience: allows the capture flow to pass
        # tag intentions in the same dict that creates the entity. We pop
        # them before delegating to the per-type creator (the entity
        # tables themselves have no slu_links column), then create the
        # link rows after the entity exists.
        if isinstance(data, dict) and "slu_links" in data:
            data = dict(data)
            slu_links = data.pop("slu_links") or []
        else:
            slu_links = []
        if entity_type == ENTITY_PROJECT:
            entity = self._create_project(data)
        elif entity_type == ENTITY_DECISION:
            entity = self._create_decision(data)
        elif entity_type == ENTITY_SUGGESTION:
            entity = self._create_suggestion(data)
        elif entity_type == ENTITY_COMMITMENT:
            entity = self._create_commitment(data)
        elif entity_type == ENTITY_STAKEHOLDER:
            entity = self._create_stakeholder(data)
        elif entity_type == ENTITY_INTEREST:
            entity = self._create_interest(data)
        elif entity_type == ENTITY_CAPABILITY:
            entity = self._create_capability(data)
        elif entity_type == ENTITY_PRINCIPLE:
            entity = self._create_principle(data)
        elif entity_type == ENTITY_IDEA:
            entity = self._create_idea(data)
        else:  # ENTITY_RESOURCE
            entity = self._create_resource(data)
        for life_unit_id in slu_links:
            self.link_entity_to_slu(entity_type, entity["id"], life_unit_id)
        return entity

    def _create_project(
        self, data: dict, conn: Optional[sqlite3.Connection] = None
    ) -> dict:
        name = data.get("name")
        if not name:
            raise ValueError("project.name is required")
        status = data.get("status", "active")
        if status not in VALID_PROJECT_STATUS:
            raise ValueError(
                f"project.status must be one of {sorted(VALID_PROJECT_STATUS)}"
            )
        now = now_iso()
        row = {
            "id": new_id(),
            "name": name,
            "description": data.get("description"),
            "status": status,
            "created_at": now,
            "last_touched": now,
            "snooze_until": data.get("snooze_until"),
            "kill_criteria": data.get("kill_criteria"),
            "revive_criteria": data.get("revive_criteria"),
        }
        sql = """INSERT INTO projects
                   (id, name, description, status, created_at, last_touched,
                    snooze_until, kill_criteria, revive_criteria)
                   VALUES
                   (:id, :name, :description, :status, :created_at, :last_touched,
                    :snooze_until, :kill_criteria, :revive_criteria)"""
        # When a conn is passed, run inside the caller's transaction (used by
        # promote_idea so the project INSERT and the idea UPDATE commit
        # atomically). Otherwise open our own.
        if conn is not None:
            conn.execute(sql, row)
        else:
            with self._connect() as c:
                c.execute(sql, row)
        return row

    def _create_decision(self, data: dict) -> dict:
        if not data.get("what"):
            raise ValueError("decision.what is required")
        if not data.get("rationale"):
            raise ValueError("decision.rationale is required")
        project_id = data.get("project_id")
        if project_id:
            with self._connect() as conn:
                exists = conn.execute(
                    "SELECT 1 FROM projects WHERE id = ?", (project_id,)
                ).fetchone()
                if not exists:
                    raise ValueError(f"project_id {project_id} does not exist")
        row = {
            "id": new_id(),
            "project_id": project_id,
            "what": data["what"],
            "rationale": data["rationale"],
            "alternatives_considered": data.get("alternatives_considered"),
            "decided_at": data.get("decided_at") or now_iso(),
            "revisit_at": data.get("revisit_at"),
            "outcome": data.get("outcome"),
        }
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO decisions
                   (id, project_id, what, rationale, alternatives_considered,
                    decided_at, revisit_at, outcome)
                   VALUES
                   (:id, :project_id, :what, :rationale, :alternatives_considered,
                    :decided_at, :revisit_at, :outcome)""",
                row,
            )
        return row

    # ---- v3.1: commitment / stakeholder / interest creators -----------------

    def _create_commitment(self, data: dict) -> dict:
        what = data.get("what")
        if not what:
            raise ValueError("commitment.what is required")
        to_whom = data.get("to_whom")
        if not to_whom:
            raise ValueError(
                "commitment.to_whom is required (use 'self' for self-commitments)"
            )
        status = data.get("status", "open")
        if status not in VALID_COMMITMENT_STATUS:
            raise ValueError(
                f"commitment.status must be one of {sorted(VALID_COMMITMENT_STATUS)}, "
                f"got {status!r}"
            )
        related_project_id = data.get("related_project_id")
        if related_project_id:
            with self._connect() as conn:
                exists = conn.execute(
                    "SELECT 1 FROM projects WHERE id = ?", (related_project_id,)
                ).fetchone()
                if not exists:
                    raise ValueError(
                        f"related_project_id {related_project_id} does not exist"
                    )
        row = {
            "id": new_id(),
            "what": what,
            "to_whom": to_whom,
            "due_at": data.get("due_at"),
            "status": status,
            "related_project_id": related_project_id,
            "created_at": now_iso(),
            "completed_at": data.get("completed_at"),
            "outcome": data.get("outcome"),
            "dropped_reason": data.get("dropped_reason"),
        }
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO commitments
                   (id, what, to_whom, due_at, status, related_project_id,
                    created_at, completed_at, outcome, dropped_reason)
                   VALUES
                   (:id, :what, :to_whom, :due_at, :status, :related_project_id,
                    :created_at, :completed_at, :outcome, :dropped_reason)""",
                row,
            )
        return row

    def _create_stakeholder(self, data: dict) -> dict:
        name = data.get("name")
        if not name:
            raise ValueError("stakeholder.name is required")
        strength = data.get("relationship_strength")
        if strength is not None and strength not in VALID_STAKEHOLDER_STRENGTH:
            raise ValueError(
                f"stakeholder.relationship_strength must be one of "
                f"{sorted(VALID_STAKEHOLDER_STRENGTH)}, got {strength!r}"
            )
        now = now_iso()
        row = {
            "id": new_id(),
            "name": name,
            "role": data.get("role"),
            "relationship_strength": strength,
            "last_contact_at": data.get("last_contact_at"),
            "owed_action": data.get("owed_action"),
            "notes": data.get("notes"),
            "created_at": now,
            "updated_at": now,
        }
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO stakeholders
                   (id, name, role, relationship_strength, last_contact_at,
                    owed_action, notes, created_at, updated_at)
                   VALUES
                   (:id, :name, :role, :relationship_strength, :last_contact_at,
                    :owed_action, :notes, :created_at, :updated_at)""",
                row,
            )
        return row

    def _create_interest(self, data: dict) -> dict:
        topic = data.get("topic")
        if not topic:
            raise ValueError("interest.topic is required")
        status = data.get("status", "active")
        if status not in VALID_INTEREST_STATUS:
            raise ValueError(
                f"interest.status must be one of {sorted(VALID_INTEREST_STATUS)}, "
                f"got {status!r}"
            )
        now = now_iso()
        first = data.get("first_observed_at") or now
        last = data.get("last_observed_at") or first
        row = {
            "id": new_id(),
            "topic": topic,
            "description": data.get("description"),
            "status": status,
            "first_observed_at": first,
            "last_observed_at": last,
            "created_at": now,
            "updated_at": now,
        }
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO interests
                   (id, topic, description, status, first_observed_at,
                    last_observed_at, created_at, updated_at)
                   VALUES
                   (:id, :topic, :description, :status, :first_observed_at,
                    :last_observed_at, :created_at, :updated_at)""",
                row,
            )
        return row

    # ---- v4: capabilities & resources --------------------------------------

    def _create_capability(self, data: dict) -> dict:
        name = data.get("name")
        if not name:
            raise ValueError("capability.name is required")
        category = data.get("category")
        if category not in VALID_CAPABILITY_CATEGORIES:
            raise ValueError(
                f"capability.category must be one of "
                f"{sorted(VALID_CAPABILITY_CATEGORIES)}, got {category!r}"
            )
        level = data.get("level")
        if level is not None:
            if level not in VALID_CAPABILITY_LEVELS:
                raise ValueError(
                    f"capability.level must be one of "
                    f"{sorted(VALID_CAPABILITY_LEVELS)}, got {level!r}"
                )
            if category not in LEVELED_CAPABILITY_CATEGORIES:
                raise ValueError(
                    f"capability.level can only be set when category is one of "
                    f"{sorted(LEVELED_CAPABILITY_CATEGORIES)}; got category={category!r}. "
                    f"Hardware, licenses, certifications, and access don't carry levels."
                )
        status = data.get("status", "active")
        if status not in VALID_CAPABILITY_STATUS:
            raise ValueError(
                f"capability.status must be one of "
                f"{sorted(VALID_CAPABILITY_STATUS)}, got {status!r}"
            )
        renewal_lead_time = data.get("renewal_lead_time_days", 90)
        if renewal_lead_time is not None and (
            not isinstance(renewal_lead_time, int) or renewal_lead_time < 0
        ):
            raise ValueError(
                "capability.renewal_lead_time_days must be a non-negative integer"
            )
        now = now_iso()
        row = {
            "id": new_id(),
            "name": name,
            "category": category,
            "description": data.get("description"),
            "level": level,
            "acquired_at": data.get("acquired_at"),
            "expires_at": data.get("expires_at"),
            "renewal_required": 1 if data.get("renewal_required") else 0,
            "renewal_lead_time_days": renewal_lead_time,
            "last_exercised_at": data.get("last_exercised_at"),
            "status": status,
            "unlocks": data.get("unlocks"),
            "notes": data.get("notes"),
            "created_at": now,
            "updated_at": now,
        }
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO capabilities
                   (id, name, category, description, level, acquired_at,
                    expires_at, renewal_required, renewal_lead_time_days,
                    last_exercised_at, status, unlocks, notes,
                    created_at, updated_at)
                   VALUES
                   (:id, :name, :category, :description, :level, :acquired_at,
                    :expires_at, :renewal_required, :renewal_lead_time_days,
                    :last_exercised_at, :status, :unlocks, :notes,
                    :created_at, :updated_at)""",
                row,
            )
        # Normalize boolean field on the way out — clients shouldn't have
        # to know about SQLite's int-as-bool convention.
        result = dict(row)
        result["renewal_required"] = bool(result["renewal_required"])
        return result

    def _create_resource(self, data: dict) -> dict:
        name = data.get("name")
        if not name:
            raise ValueError("resource.name is required")
        category = data.get("category")
        if category not in VALID_RESOURCE_CATEGORIES:
            raise ValueError(
                f"resource.category must be one of "
                f"{sorted(VALID_RESOURCE_CATEGORIES)}, got {category!r}"
            )
        unit = data.get("unit")
        if not unit:
            raise ValueError("resource.unit is required (e.g. 'EUR', 'months', 'hours_per_week')")
        qty = data.get("current_quantity")
        if qty is None or not isinstance(qty, (int, float)) or isinstance(qty, bool):
            raise ValueError("resource.current_quantity is required and must be a number")
        for fname in ("floor", "burn_rate", "replenish_rate"):
            v = data.get(fname)
            if v is not None and (not isinstance(v, (int, float)) or isinstance(v, bool)):
                raise ValueError(f"resource.{fname} must be a number or null")
        now = now_iso()
        row = {
            "id": new_id(),
            "name": name,
            "category": category,
            "unit": unit,
            "current_quantity": float(qty),
            "as_of": data.get("as_of") or now,
            "floor": data.get("floor"),
            "burn_rate": data.get("burn_rate"),
            "replenish_rate": data.get("replenish_rate"),
            "notes": data.get("notes"),
            "created_at": now,
            "updated_at": now,
        }
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO resources
                   (id, name, category, unit, current_quantity, as_of,
                    floor, burn_rate, replenish_rate, notes,
                    created_at, updated_at)
                   VALUES
                   (:id, :name, :category, :unit, :current_quantity, :as_of,
                    :floor, :burn_rate, :replenish_rate, :notes,
                    :created_at, :updated_at)""",
                row,
            )
        return row

    # ---- state_update -------------------------------------------------------

    def update(self, entity_type: str, id: str, patch: dict) -> dict:
        # suggestions transition via disposition_suggestion, not update
        table = _require_entity_type(entity_type, VALID_UPDATE_TYPES)
        if not patch:
            raise ValueError("patch is empty")
        # never let id be patched
        patch = {k: v for k, v in patch.items() if k != "id"}
        # principles edit through a dedicated path: rank/status are off-limits
        # here, and every change logs a 'rewritten' revision atomically.
        if entity_type == ENTITY_PRINCIPLE:
            return self._update_principle(id, patch)
        if entity_type == ENTITY_PROJECT:
            if "status" in patch and patch["status"] not in VALID_PROJECT_STATUS:
                raise ValueError(
                    f"project.status must be one of {sorted(VALID_PROJECT_STATUS)}"
                )
            patch["last_touched"] = now_iso()
        elif entity_type == ENTITY_COMMITMENT:
            if "status" in patch and patch["status"] not in VALID_COMMITMENT_STATUS:
                raise ValueError(
                    f"commitment.status must be one of {sorted(VALID_COMMITMENT_STATUS)}"
                )
        elif entity_type == ENTITY_STAKEHOLDER:
            if (
                "relationship_strength" in patch
                and patch["relationship_strength"] is not None
                and patch["relationship_strength"] not in VALID_STAKEHOLDER_STRENGTH
            ):
                raise ValueError(
                    f"stakeholder.relationship_strength must be one of "
                    f"{sorted(VALID_STAKEHOLDER_STRENGTH)}"
                )
            patch["updated_at"] = now_iso()
        elif entity_type == ENTITY_INTEREST:
            if "status" in patch and patch["status"] not in VALID_INTEREST_STATUS:
                raise ValueError(
                    f"interest.status must be one of {sorted(VALID_INTEREST_STATUS)}"
                )
            patch["updated_at"] = now_iso()
        elif entity_type == ENTITY_CAPABILITY:
            if "status" in patch and patch["status"] not in VALID_CAPABILITY_STATUS:
                raise ValueError(
                    f"capability.status must be one of {sorted(VALID_CAPABILITY_STATUS)}"
                )
            if "category" in patch and patch["category"] not in VALID_CAPABILITY_CATEGORIES:
                raise ValueError(
                    f"capability.category must be one of "
                    f"{sorted(VALID_CAPABILITY_CATEGORIES)}"
                )
            if "level" in patch and patch["level"] is not None:
                if patch["level"] not in VALID_CAPABILITY_LEVELS:
                    raise ValueError(
                        f"capability.level must be one of {sorted(VALID_CAPABILITY_LEVELS)}"
                    )
                # Need to verify level/category compatibility against the
                # effective category — the new one if patched, else the
                # stored one.
                effective_cat = patch.get("category")
                if effective_cat is None:
                    with self._connect() as conn:
                        existing = conn.execute(
                            "SELECT category FROM capabilities WHERE id = ?", (id,)
                        ).fetchone()
                        if existing:
                            effective_cat = existing["category"]
                if effective_cat not in LEVELED_CAPABILITY_CATEGORIES:
                    raise ValueError(
                        f"capability.level can only be set when category is one of "
                        f"{sorted(LEVELED_CAPABILITY_CATEGORIES)}"
                    )
            # SQLite stores booleans as 0/1; normalize for the UPDATE.
            if "renewal_required" in patch:
                patch["renewal_required"] = 1 if patch["renewal_required"] else 0
            patch["updated_at"] = now_iso()
        elif entity_type == ENTITY_RESOURCE:
            if "category" in patch and patch["category"] not in VALID_RESOURCE_CATEGORIES:
                raise ValueError(
                    f"resource.category must be one of "
                    f"{sorted(VALID_RESOURCE_CATEGORIES)}"
                )
            # current_quantity and as_of must move together — that's the
            # whole contract of "this number is current as of <date>".
            if ("current_quantity" in patch) != ("as_of" in patch):
                raise ValueError(
                    "resource.current_quantity and resource.as_of must be "
                    "updated together"
                )
            for fname in ("current_quantity", "floor", "burn_rate", "replenish_rate"):
                v = patch.get(fname)
                if v is not None and fname in patch and (
                    not isinstance(v, (int, float)) or isinstance(v, bool)
                ):
                    raise ValueError(f"resource.{fname} must be a number or null")
            patch["updated_at"] = now_iso()
        elif entity_type == ENTITY_IDEA:
            if "status" in patch:
                if patch["status"] not in VALID_IDEA_STATUS:
                    raise ValueError(
                        f"idea.status must be one of {sorted(VALID_IDEA_STATUS)}"
                    )
                # The terminal states carry side effects (a project link / a
                # release reason); they go through their own tools so those
                # invariants hold. Generic update covers the live ladder
                # spark ↔ exploring ↔ someday.
                if patch["status"] in ("promoted", "released"):
                    raise ValueError(
                        "set idea.status to 'promoted' via promote_idea and "
                        "'released' via release_idea, not state_update"
                    )
            # any edit or status move counts as touching the idea, keeping
            # the derived heat honest
            patch["last_touched_at"] = now_iso()
        with self._connect() as conn:
            existing = conn.execute(
                f"SELECT * FROM {table} WHERE id = ?", (id,)
            ).fetchone()
            if not existing:
                raise ValueError(f"{entity_type} {id} not found")
            # Patch keys are interpolated into the SET clause, so they MUST be
            # validated against the table's real columns — otherwise a crafted
            # key is a SQL-injection vector and can sidestep the per-type enum
            # validators above. (query() guards its filter keys the same way.)
            valid_columns = set(existing.keys())
            unknown = [k for k in patch if k not in valid_columns]
            if unknown:
                raise ValueError(
                    f"{entity_type} has no column(s) {sorted(unknown)}; "
                    f"valid columns: {sorted(valid_columns)}"
                )
            set_clause = ", ".join(f"{k} = ?" for k in patch.keys())
            params = list(patch.values()) + [id]
            try:
                conn.execute(
                    f"UPDATE {table} SET {set_clause} WHERE id = ?", params
                )
            except sqlite3.OperationalError as e:
                raise ValueError(f"invalid patch: {e}") from e
            row = conn.execute(
                f"SELECT * FROM {table} WHERE id = ?", (id,)
            ).fetchone()
        return dict(row)

    # ---- state_draft --------------------------------------------------------

    def create_draft(
        self,
        entity_type: str,
        data: dict,
        session_id: Optional[str] = None,
    ) -> dict:
        _require_entity_type(entity_type, VALID_DRAFT_ENTITY_TYPES)
        row = {
            "id": new_id(),
            "entity_type": entity_type,
            "data": json.dumps(data),
            "status": "pending",
            "created_at": now_iso(),
            "committed_at": None,
            "rejection_reason": None,
            "session_id": session_id,
            "committed_entity_id": None,
        }
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO drafts
                   (id, entity_type, data, status, created_at, committed_at,
                    rejection_reason, session_id, committed_entity_id)
                   VALUES
                   (:id, :entity_type, :data, :status, :created_at, :committed_at,
                    :rejection_reason, :session_id, :committed_entity_id)""",
                row,
            )
        result = dict(row)
        result["data"] = data
        return result

    def list_drafts(
        self,
        session_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[dict]:
        sql = "SELECT * FROM drafts"
        clauses: list[str] = []
        params: list[Any] = []
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if status is not None:
            if status not in VALID_DRAFT_STATUS:
                raise ValueError(
                    f"status must be one of {sorted(VALID_DRAFT_STATUS)}"
                )
            clauses.append("status = ?")
            params.append(status)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at ASC"
        with self._connect() as conn:
            rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        for r in rows:
            r["data"] = json.loads(r["data"])
        return rows

    def _load_draft(self, conn: sqlite3.Connection, draft_id: str) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM drafts WHERE id = ?", (draft_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"draft {draft_id} not found")
        return row

    def commit_draft(self, draft_id: str) -> dict:
        with self._connect() as conn:
            row = self._load_draft(conn, draft_id)
            if row["status"] not in ("pending", "amended"):
                raise ValueError(
                    f"draft {draft_id} is {row['status']}, cannot commit"
                )
            entity_type = row["entity_type"]
            data = json.loads(row["data"])

        entity = self.write(entity_type, data)

        # If marking the draft committed fails, roll back the just-created
        # entity so a retry can't produce a duplicate. (write() runs the entity
        # insert in its own transaction, so we compensate rather than share a
        # transaction — which would deadlock with write()'s nested connections
        # when the draft carries slu_links.)
        try:
            with self._connect() as conn:
                conn.execute(
                    """UPDATE drafts
                       SET status = 'committed',
                           committed_at = ?,
                           committed_entity_id = ?
                       WHERE id = ?""",
                    (now_iso(), entity["id"], draft_id),
                )
        except Exception:
            with self._connect() as c:
                c.execute(
                    f"DELETE FROM {_TABLE[entity_type]} WHERE id = ?",
                    (entity["id"],),
                )
                c.execute(
                    "DELETE FROM entity_slu_links "
                    "WHERE entity_type = ? AND entity_id = ?",
                    (entity_type, entity["id"]),
                )
            raise
        return entity

    def reject_draft(
        self, draft_id: str, reason: Optional[str] = None
    ) -> dict:
        with self._connect() as conn:
            row = self._load_draft(conn, draft_id)
            if row["status"] not in ("pending", "amended"):
                raise ValueError(
                    f"draft {draft_id} is {row['status']}, cannot reject"
                )
            conn.execute(
                "UPDATE drafts SET status = 'rejected', rejection_reason = ? WHERE id = ?",
                (reason, draft_id),
            )
            row = self._load_draft(conn, draft_id)
        result = dict(row)
        result["data"] = json.loads(result["data"])
        return result

    def amend_draft(self, draft_id: str, patch: dict) -> dict:
        if not patch:
            raise ValueError("patch is empty")
        with self._connect() as conn:
            row = self._load_draft(conn, draft_id)
            if row["status"] not in ("pending", "amended"):
                raise ValueError(
                    f"draft {draft_id} is {row['status']}, cannot amend"
                )
            data = json.loads(row["data"])
            data.update(patch)
            conn.execute(
                "UPDATE drafts SET data = ?, status = 'amended' WHERE id = ?",
                (json.dumps(data), draft_id),
            )
            row = self._load_draft(conn, draft_id)
        result = dict(row)
        result["data"] = json.loads(result["data"])
        return result

    # ---- v7: principles --------------------------------------------------
    #
    # Principles are RANKED operating rules (rank 1 = highest). The model is
    # soft: a decision may depart from a principle, but a departure in
    # service of a HIGHER-ranked principle is the hierarchy working as
    # designed, not a failure. Rank uniqueness is enforced here in
    # application logic (active rows only); the reordering is the one
    # genuinely tricky piece — it runs inside a single transaction so a
    # half-completed shift can never leave two active principles at the
    # same rank.

    def _log_principle_revision(
        self,
        conn: sqlite3.Connection,
        principle_id: str,
        change_type: str,
        prior_state: Optional[dict],
        new_state: Optional[dict],
        rationale: str,
    ) -> None:
        """Append a row to the principle_revisions log. Must run inside the
        caller's transaction (takes an open conn) so the revision and the
        change it records commit together."""
        if change_type not in VALID_PRINCIPLE_CHANGE_TYPES:
            raise ValueError(
                f"change_type must be one of {sorted(VALID_PRINCIPLE_CHANGE_TYPES)}"
            )
        if not rationale:
            raise ValueError("a principle revision requires a rationale")
        conn.execute(
            """INSERT INTO principle_revisions
               (id, principle_id, revised_at, change_type, prior_state,
                new_state, rationale)
               VALUES (?,?,?,?,?,?,?)""",
            (
                new_id(), principle_id, now_iso(), change_type,
                json.dumps(prior_state) if prior_state is not None else None,
                json.dumps(new_state) if new_state is not None else None,
                rationale,
            ),
        )

    def _create_principle(self, data: dict) -> dict:
        name = data.get("name")
        if not name:
            raise ValueError("principle.name is required")
        description = data.get("description")
        if not description:
            raise ValueError("principle.description is required")
        rank = data.get("rank")
        if rank is None:
            raise ValueError("principle.rank is required (1 = highest priority)")
        if not isinstance(rank, int) or isinstance(rank, bool) or rank < 1:
            raise ValueError(
                "principle.rank must be a positive integer (1 = highest)"
            )
        # The skill may pass a creation rationale; it is logged to the
        # revision history, never stored as a column.
        rationale = data.get("revision_rationale") or "Principle defined."
        provenance = data.get("provenance")
        now = now_iso()
        pid = new_id()
        with self._connect() as conn:
            n_active = conn.execute(
                "SELECT COUNT(*) FROM principles WHERE status='active'"
            ).fetchone()[0]
            # A new principle may slot anywhere from 1..n_active+1 (the last
            # position appends). Clamp a too-high rank to the append slot
            # rather than erroring — friendlier for the intake flow.
            if rank > n_active + 1:
                rank = n_active + 1
            # Shift active principles at or below the target down by one.
            conn.execute(
                "UPDATE principles SET rank = rank + 1, last_revised_at = ? "
                "WHERE status='active' AND rank >= ?",
                (now, rank),
            )
            row = {
                "id": pid, "name": name, "description": description,
                "rank": rank, "provenance": provenance, "status": "active",
                "created_at": now, "last_revised_at": now,
            }
            conn.execute(
                """INSERT INTO principles
                   (id, name, description, rank, provenance, status,
                    created_at, last_revised_at)
                   VALUES
                   (:id, :name, :description, :rank, :provenance, :status,
                    :created_at, :last_revised_at)""",
                row,
            )
            self._log_principle_revision(conn, pid, "created", None, row, rationale)
        return row

    def _update_principle(self, id: str, patch: dict) -> dict:
        """Edit a principle's wording (name/description/provenance) and log a
        'rewritten' revision. Rank and status changes are NOT allowed here —
        they go through rerank_principle / retire_principle, which have their
        own reordering and revision semantics."""
        patch = dict(patch)
        rationale = patch.pop("rationale", None)
        if "rank" in patch:
            raise ValueError(
                "principle.rank cannot be changed via update; use rerank_principle"
            )
        if "status" in patch:
            raise ValueError(
                "principle.status cannot be changed via update; use retire_principle"
            )
        editable = {"name", "description", "provenance"}
        unexpected = set(patch) - editable
        if unexpected:
            raise ValueError(
                f"principle update accepts only {sorted(editable)} (plus a "
                f"'rationale' for the revision log); got {sorted(unexpected)}"
            )
        if not patch:
            raise ValueError("nothing to update")
        if "name" in patch and not patch["name"]:
            raise ValueError("principle.name cannot be empty")
        if "description" in patch and not patch["description"]:
            raise ValueError("principle.description cannot be empty")
        if not rationale:
            raise ValueError(
                "principle edits require a 'rationale' in the patch — every "
                "change to a principle is logged to its revision history"
            )
        now = now_iso()
        with self._connect() as conn:
            prior = conn.execute(
                "SELECT * FROM principles WHERE id = ?", (id,)
            ).fetchone()
            if not prior:
                raise ValueError(f"principle {id} not found")
            prior = dict(prior)
            patch["last_revised_at"] = now
            set_clause = ", ".join(f"{k} = ?" for k in patch)
            conn.execute(
                f"UPDATE principles SET {set_clause} WHERE id = ?",
                list(patch.values()) + [id],
            )
            new_row = dict(conn.execute(
                "SELECT * FROM principles WHERE id = ?", (id,)
            ).fetchone())
            self._log_principle_revision(
                conn, id, "rewritten", prior, new_row, rationale
            )
        return new_row

    def list_principles(self, status: Optional[str] = "active") -> list[dict]:
        """Return principles in rank order. Default is active only (the live
        hierarchy). Pass status='retired' for retired ones, or status='all'
        / None for everything (active first, then retired)."""
        sql = "SELECT * FROM principles"
        params: list[Any] = []
        if status not in (None, "all"):
            if status not in VALID_PRINCIPLE_STATUS:
                raise ValueError(
                    f"status must be one of {sorted(VALID_PRINCIPLE_STATUS)}, "
                    f"'all', or omitted"
                )
            sql += " WHERE status = ?"
            params.append(status)
        # active before retired, then by rank within each
        sql += " ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, rank"
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def rerank_principle(
        self, principle_id: str, new_rank: int, rationale: str
    ) -> dict:
        """Move an active principle to new_rank, shifting the others to keep
        ranks contiguous, and log a 'reranked' revision. Atomic."""
        if not rationale:
            raise ValueError("re-ranking a principle requires a rationale")
        if not isinstance(new_rank, int) or isinstance(new_rank, bool) or new_rank < 1:
            raise ValueError("new_rank must be a positive integer (1 = highest)")
        now = now_iso()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM principles WHERE id = ?", (principle_id,)
            ).fetchone()
            if not row:
                raise ValueError(f"principle {principle_id} not found")
            if row["status"] != "active":
                raise ValueError("cannot rerank a retired principle")
            prior = dict(row)
            old_rank = row["rank"]
            n_active = conn.execute(
                "SELECT COUNT(*) FROM principles WHERE status='active'"
            ).fetchone()[0]
            if new_rank > n_active:
                new_rank = n_active  # clamp to bottom of the active list
            if new_rank == old_rank:
                return dict(row)
            if new_rank < old_rank:
                # moving UP: the block [new_rank, old_rank-1] shifts down +1
                conn.execute(
                    "UPDATE principles SET rank = rank + 1, last_revised_at = ? "
                    "WHERE status='active' AND rank >= ? AND rank < ?",
                    (now, new_rank, old_rank),
                )
            else:
                # moving DOWN: the block (old_rank, new_rank] shifts up -1
                conn.execute(
                    "UPDATE principles SET rank = rank - 1, last_revised_at = ? "
                    "WHERE status='active' AND rank > ? AND rank <= ?",
                    (now, old_rank, new_rank),
                )
            conn.execute(
                "UPDATE principles SET rank = ?, last_revised_at = ? WHERE id = ?",
                (new_rank, now, principle_id),
            )
            new_row = dict(conn.execute(
                "SELECT * FROM principles WHERE id = ?", (principle_id,)
            ).fetchone())
            self._log_principle_revision(
                conn, principle_id, "reranked", prior, new_row, rationale
            )
        return new_row

    def retire_principle(self, principle_id: str, rationale: str) -> dict:
        """Retire a principle (status -> retired), freeing its rank among the
        active set. The gap is left intentionally; the user decides during a
        revision flow whether to compact via rerank_principle. Logs a
        'retired' revision (new_state null, per the revision contract)."""
        if not rationale:
            raise ValueError("retiring a principle requires a rationale")
        now = now_iso()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM principles WHERE id = ?", (principle_id,)
            ).fetchone()
            if not row:
                raise ValueError(f"principle {principle_id} not found")
            if row["status"] == "retired":
                raise ValueError(f"principle {principle_id} is already retired")
            prior = dict(row)
            conn.execute(
                "UPDATE principles SET status='retired', last_revised_at = ? "
                "WHERE id = ?",
                (now, principle_id),
            )
            self._log_principle_revision(
                conn, principle_id, "retired", prior, None, rationale
            )
            new_row = dict(conn.execute(
                "SELECT * FROM principles WHERE id = ?", (principle_id,)
            ).fetchone())
        return new_row

    def _severity_for_rank(self, conn: sqlite3.Connection, rank: int) -> str:
        """Map a departed principle's rank to a severity by tertiles of the
        active count: top third -> high, middle -> medium, bottom -> low.
        (For 11 active principles this is ~1-4 high, 5-8 medium, 9-11 low.)"""
        n = conn.execute(
            "SELECT COUNT(*) FROM principles WHERE status='active'"
        ).fetchone()[0]
        if n <= 0:
            return "medium"
        import math
        top = math.ceil(n / 3)
        mid = math.ceil(2 * n / 3)
        if rank <= top:
            return "high"
        if rank <= mid:
            return "medium"
        return "low"

    def evaluate_decision_against_principles(self, decision_id: str) -> dict:
        """Read helper for the capture skill: returns the active principles in
        rank order so the LLM can reason a decision against them. Does NOT
        write any evaluations — recording is a separate, explicit step."""
        with self._connect() as conn:
            dec = conn.execute(
                "SELECT id FROM decisions WHERE id = ?", (decision_id,)
            ).fetchone()
            if not dec:
                raise ValueError(f"decision {decision_id} not found")
        return {
            "decision_id": decision_id,
            "principles": self.list_principles(status="active"),
        }

    def record_principle_evaluation(
        self,
        decision_id: str,
        principle_id: str,
        relationship: str,
        overrides_principle_id: Optional[str] = None,
        severity: Optional[str] = None,
        rationale: Optional[str] = None,
    ) -> dict:
        """Record one decision↔principle evaluation. Enforces the
        relationship-specific contract:
          - justified_override: needs overrides_principle_id + rationale, and
            the override target must outrank (lower rank number) the departed
            principle.
          - unjustified_departure: needs rationale; severity is auto-derived
            from the departed principle's rank when not supplied.
          - aligned: rationale optional; no severity, no override target.
        """
        if relationship not in VALID_EVAL_RELATIONSHIPS:
            raise ValueError(
                f"relationship must be one of {sorted(VALID_EVAL_RELATIONSHIPS)}"
            )
        if severity is not None and severity not in VALID_EVAL_SEVERITY:
            raise ValueError(
                f"severity must be one of {sorted(VALID_EVAL_SEVERITY)} or null"
            )
        with self._connect() as conn:
            dec = conn.execute(
                "SELECT id FROM decisions WHERE id = ?", (decision_id,)
            ).fetchone()
            if not dec:
                raise ValueError(f"decision {decision_id} not found")
            principle = conn.execute(
                "SELECT * FROM principles WHERE id = ?", (principle_id,)
            ).fetchone()
            if not principle:
                raise ValueError(f"principle {principle_id} not found")

            if relationship == "aligned":
                # quiet, no-friction; severity/override are meaningless here
                severity = None
                overrides_principle_id = None
            elif relationship == "justified_override":
                if not overrides_principle_id:
                    raise ValueError(
                        "justified_override requires overrides_principle_id "
                        "(the higher-ranked principle being served)"
                    )
                if not rationale:
                    raise ValueError("justified_override requires a rationale")
                higher = conn.execute(
                    "SELECT * FROM principles WHERE id = ?",
                    (overrides_principle_id,),
                ).fetchone()
                if not higher:
                    raise ValueError(
                        f"overrides_principle_id {overrides_principle_id} not found"
                    )
                # "higher-ranked" means a SMALLER rank number. The override
                # target must genuinely outrank the departed principle, or
                # this isn't the hierarchy working — it's just a departure.
                if higher["rank"] >= principle["rank"]:
                    raise ValueError(
                        "justified_override: the overriding principle "
                        f"(rank {higher['rank']}) must be higher-ranked than "
                        f"the departed principle (rank {principle['rank']}) — "
                        "a smaller rank number"
                    )
                severity = None  # an override is healthy, not an alarm
            else:  # unjustified_departure
                if not rationale:
                    raise ValueError(
                        "unjustified_departure requires a rationale"
                    )
                overrides_principle_id = None
                if severity is None:
                    severity = self._severity_for_rank(conn, principle["rank"])

            eid = new_id()
            row = {
                "id": eid,
                "decision_id": decision_id,
                "principle_id": principle_id,
                "relationship": relationship,
                "overrides_principle_id": overrides_principle_id,
                "severity": severity,
                "rationale": rationale,
                "evaluated_at": now_iso(),
            }
            conn.execute(
                """INSERT INTO decision_principle_evaluations
                   (id, decision_id, principle_id, relationship,
                    overrides_principle_id, severity, rationale, evaluated_at)
                   VALUES
                   (:id, :decision_id, :principle_id, :relationship,
                    :overrides_principle_id, :severity, :rationale, :evaluated_at)""",
                row,
            )
        return row

    def list_principle_departures(
        self,
        principle_id: Optional[str] = None,
        since: Optional[str] = None,
        relationship: Optional[str] = None,
    ) -> list[dict]:
        """Return decision↔principle evaluations, newest first. For the
        scanner: filter to relationship='unjustified_departure', and/or by
        principle, and/or by time window (since = ISO lower bound)."""
        if relationship is not None and relationship not in VALID_EVAL_RELATIONSHIPS:
            raise ValueError(
                f"relationship must be one of {sorted(VALID_EVAL_RELATIONSHIPS)} or null"
            )
        sql = "SELECT * FROM decision_principle_evaluations"
        clauses: list[str] = []
        params: list[Any] = []
        if principle_id is not None:
            clauses.append("principle_id = ?")
            params.append(principle_id)
        if relationship is not None:
            clauses.append("relationship = ?")
            params.append(relationship)
        if since is not None:
            clauses.append("evaluated_at >= ?")
            params.append(since)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY evaluated_at DESC"
        with self._connect() as conn:
            rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
            for r in rows:
                r["principle_name"] = self._label_for(
                    conn, "principles", r.get("principle_id")
                )
                r["overrides_principle_name"] = self._label_for(
                    conn, "principles", r.get("overrides_principle_id")
                )
                r["decision_what"] = self._label_for(
                    conn, "decisions", r.get("decision_id")
                )
        return rows

    # ---- v8: ideas -------------------------------------------------------
    #
    # Ideas are retained sparks, pre-commitment. The point is maturation: an
    # idea must move (warm toward promotion, or be parked/released on purpose)
    # or it rots. "Heat" is DERIVED from last_touched_at, never stored, and
    # only spark/exploring ideas can read as `cooling` — `someday` is a
    # deliberate park and is never nagged.

    def _idea_heat(self, last_touched_at: str, status: str) -> str:
        """warm (<30d) / neutral (30d–6mo) / cooling (6mo+, and only for the
        coolable statuses). Computed, never stored."""
        try:
            touched = datetime.fromisoformat(last_touched_at)
        except (TypeError, ValueError):
            return "neutral"
        if touched.tzinfo is None:
            touched = touched.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - touched).total_seconds() / 86400.0
        if age_days < 30:
            return "warm"
        if age_days >= 180 and status in COOLABLE_IDEA_STATUS:
            return "cooling"
        return "neutral"

    def _enrich_idea(self, row: sqlite3.Row) -> dict:
        d = dict(row)
        d["heat"] = self._idea_heat(d["last_touched_at"], d["status"])
        return d

    def _create_idea(self, data: dict) -> dict:
        title = data.get("title")
        if not title:
            raise ValueError("idea.title is required")
        status = data.get("status", "spark")
        if status not in VALID_IDEA_STATUS:
            raise ValueError(
                f"idea.status must be one of {sorted(VALID_IDEA_STATUS)}"
            )
        now = now_iso()
        row = {
            "id": new_id(),
            "title": title,
            "body": data.get("body"),
            "status": status,
            "captured_at": data.get("captured_at") or now,
            "last_touched_at": now,
            "promoted_to_project_id": None,
            "released_reason": None,
            "created_at": now,
        }
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO ideas
                   (id, title, body, status, captured_at, last_touched_at,
                    promoted_to_project_id, released_reason, created_at)
                   VALUES
                   (:id, :title, :body, :status, :captured_at, :last_touched_at,
                    :promoted_to_project_id, :released_reason, :created_at)""",
                row,
            )
        return row

    def list_ideas(
        self, status: Optional[Any] = None, since: Optional[str] = None
    ) -> list[dict]:
        """Ideas with derived heat, newest first. `status` may be a single
        status, a list of them, or the convenience literal "active" (=
        spark + exploring + someday, the non-terminal ladder). `since` is an
        ISO lower bound on captured_at."""
        sql = "SELECT * FROM ideas"
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            if status == "active":
                statuses = ["spark", "exploring", "someday"]
            elif isinstance(status, (list, tuple, set)):
                statuses = list(status)
            else:
                statuses = [status]
            for s in statuses:
                if s not in VALID_IDEA_STATUS:
                    raise ValueError(
                        f"idea.status must be one of {sorted(VALID_IDEA_STATUS)} "
                        f"(or 'active'); got {s!r}"
                    )
            placeholders = ",".join("?" for _ in statuses)
            clauses.append(f"status IN ({placeholders})")
            params.extend(statuses)
        if since is not None:
            clauses.append("captured_at >= ?")
            params.append(since)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY captured_at DESC"
        with self._connect() as conn:
            return [self._enrich_idea(r) for r in conn.execute(sql, params).fetchall()]

    def list_cooling_ideas(self, dormant_months: float = 6) -> list[dict]:
        """Ideas in spark/exploring untouched for at least `dormant_months`.
        Never returns `someday` (deliberately parked) or terminal ideas. This
        is what the scanner's patient cooling_idea pattern calls."""
        if (
            not isinstance(dormant_months, (int, float))
            or isinstance(dormant_months, bool)
            or dormant_months <= 0
        ):
            raise ValueError("dormant_months must be a positive number")
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=dormant_months * 30)
        ).isoformat()
        statuses = sorted(COOLABLE_IDEA_STATUS)
        placeholders = ",".join("?" for _ in statuses)
        sql = (
            f"SELECT * FROM ideas WHERE status IN ({placeholders}) "
            f"AND last_touched_at < ? ORDER BY last_touched_at ASC"
        )
        with self._connect() as conn:
            rows = conn.execute(sql, statuses + [cutoff]).fetchall()
        return [self._enrich_idea(r) for r in rows]

    def touch_idea(self, idea_id: str) -> dict:
        """Bump last_touched_at to now — called when an idea is revisited or
        referenced, to keep its derived heat honest."""
        now = now_iso()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM ideas WHERE id = ?", (idea_id,)
            ).fetchone()
            if not row:
                raise ValueError(f"idea {idea_id} not found")
            conn.execute(
                "UPDATE ideas SET last_touched_at = ? WHERE id = ?", (now, idea_id)
            )
            row = conn.execute(
                "SELECT * FROM ideas WHERE id = ?", (idea_id,)
            ).fetchone()
        return self._enrich_idea(row)

    def promote_idea(self, idea_id: str) -> dict:
        """Graduate an idea into a project: create a project from the idea's
        title/body, mark the idea `promoted` with the project link, and touch
        it. Returns the new project (the user fleshes out kill criteria etc.
        afterward via the normal flows)."""
        # The project INSERT and the idea UPDATE happen in ONE transaction, so
        # a failure between them can't leave a committed project with the idea
        # still promotable (which would let it be promoted twice into a
        # duplicate project).
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM ideas WHERE id = ?", (idea_id,)
            ).fetchone()
            if not row:
                raise ValueError(f"idea {idea_id} not found")
            if row["status"] in ("promoted", "released"):
                raise ValueError(
                    f"idea {idea_id} is already {row['status']}; cannot promote"
                )
            project = self._create_project(
                {"name": row["title"], "description": row["body"]}, conn=conn
            )
            conn.execute(
                "UPDATE ideas SET status='promoted', promoted_to_project_id=?, "
                "last_touched_at=? WHERE id=?",
                (project["id"], now_iso(), idea_id),
            )
        return project

    def release_idea(self, idea_id: str, reason: Optional[str] = None) -> dict:
        """Let an idea go: status -> released (terminal), with an optional
        reason. Touches the idea. Returns the released idea."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM ideas WHERE id = ?", (idea_id,)
            ).fetchone()
            if not row:
                raise ValueError(f"idea {idea_id} not found")
            if row["status"] in ("promoted", "released"):
                raise ValueError(
                    f"idea {idea_id} is already {row['status']}; cannot release"
                )
            conn.execute(
                "UPDATE ideas SET status='released', released_reason=?, "
                "last_touched_at=? WHERE id=?",
                (reason, now_iso(), idea_id),
            )
            row = conn.execute(
                "SELECT * FROM ideas WHERE id = ?", (idea_id,)
            ).fetchone()
        return self._enrich_idea(row)

    # ---- suggestions (v2) ------------------------------------------------

    def _create_suggestion(self, data: dict) -> dict:
        kind = data.get("kind")
        if kind not in VALID_SUGGESTION_KINDS:
            raise ValueError(
                f"suggestion.kind must be one of {sorted(VALID_SUGGESTION_KINDS)}, "
                f"got {kind!r}"
            )
        if not data.get("content"):
            raise ValueError("suggestion.content is required")
        if not data.get("rationale"):
            raise ValueError("suggestion.rationale is required")

        evidence = data.get("linked_evidence")
        if not isinstance(evidence, dict):
            raise ValueError(
                "suggestion.linked_evidence must be an object with "
                "'internal_anchors' and 'external_sources'"
            )
        anchors = evidence.get("internal_anchors") or []
        sources = evidence.get("external_sources") or []

        if not isinstance(anchors, list) or len(anchors) == 0:
            raise ValueError(
                "suggestion.linked_evidence.internal_anchors must contain "
                "at least one entry — suggestions without an internal anchor "
                "are rejected at write time"
            )
        for i, anchor in enumerate(anchors):
            if not isinstance(anchor, dict):
                raise ValueError(f"internal_anchors[{i}] must be an object")
            if not anchor.get("entity_type") or not anchor.get("entity_id"):
                raise ValueError(
                    f"internal_anchors[{i}] requires entity_type and entity_id"
                )

        if kind in SUGGESTION_KINDS_REQUIRING_EXTERNAL:
            if not isinstance(sources, list) or len(sources) == 0:
                raise ValueError(
                    f"suggestion.linked_evidence.external_sources is "
                    f"required for kind={kind!r}"
                )
        for i, src in enumerate(sources):
            if not isinstance(src, dict):
                raise ValueError(f"external_sources[{i}] must be an object")
            if not src.get("url"):
                raise ValueError(f"external_sources[{i}] requires a url")

        # store the normalized evidence shape, not whatever the caller passed
        normalized_evidence = {
            "internal_anchors": anchors,
            "external_sources": sources,
        }
        row = {
            "id": new_id(),
            "created_at": now_iso(),
            "kind": kind,
            "content": data["content"],
            "rationale": data["rationale"],
            "linked_evidence": json.dumps(normalized_evidence),
            "status": "pending",
            "disposition_reason": None,
            "dispositioned_at": None,
            "session_id": data.get("session_id"),
        }
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO suggestions
                   (id, created_at, kind, content, rationale, linked_evidence,
                    status, disposition_reason, dispositioned_at, session_id)
                   VALUES
                   (:id, :created_at, :kind, :content, :rationale, :linked_evidence,
                    :status, :disposition_reason, :dispositioned_at, :session_id)""",
                row,
            )
        return _deserialize_suggestion(row)

    def disposition_suggestion(
        self,
        id: str,
        status: str,
        reason: Optional[str] = None,
    ) -> dict:
        if status not in VALID_SUGGESTION_DISPOSITIONS:
            raise ValueError(
                f"status must be one of {sorted(VALID_SUGGESTION_DISPOSITIONS)}, "
                f"got {status!r}"
            )
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM suggestions WHERE id = ?", (id,)
            ).fetchone()
            if not row:
                raise ValueError(f"suggestion {id} not found")
            if row["status"] != "pending":
                raise ValueError(
                    f"suggestion {id} is {row['status']}, "
                    f"cannot disposition (only pending can be dispositioned)"
                )
            conn.execute(
                """UPDATE suggestions
                   SET status = ?, disposition_reason = ?, dispositioned_at = ?
                   WHERE id = ?""",
                (status, reason, now_iso(), id),
            )
            row = conn.execute(
                "SELECT * FROM suggestions WHERE id = ?", (id,)
            ).fetchone()
        return _deserialize_suggestion(row)

    def list_pending_suggestions(
        self,
        session_id: Optional[str] = None,
        kind: Optional[str] = None,
        since: Optional[str] = None,
    ) -> list[dict]:
        sql = "SELECT * FROM suggestions WHERE status = 'pending'"
        params: list[Any] = []
        if session_id is not None:
            sql += " AND session_id = ?"
            params.append(session_id)
        if kind is not None:
            if kind not in VALID_SUGGESTION_KINDS:
                raise ValueError(
                    f"kind must be one of {sorted(VALID_SUGGESTION_KINDS)}, got {kind!r}"
                )
            sql += " AND kind = ?"
            params.append(kind)
        if since is not None:
            sql += " AND created_at >= ?"
            params.append(since)
        sql += " ORDER BY created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            out = [_deserialize_suggestion(r) for r in rows]
            for sug in out:
                self._enrich_suggestion_anchors(conn, sug)
        return out

    # ---- state_attach_source ------------------------------------------------

    def attach_source(
        self,
        target_type: str,
        target_id: str,
        source_type: str,
        content: str,
    ) -> str:
        if target_type not in VALID_LINK_TARGETS:
            raise ValueError(
                f"target_type must be one of {sorted(VALID_LINK_TARGETS)}"
            )
        if source_type not in VALID_SOURCE_TYPES:
            raise ValueError(
                f"source_type must be one of {sorted(VALID_SOURCE_TYPES)}"
            )
        if not content:
            raise ValueError("content is required")
        source_id = new_id()
        link_id = new_id()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO sources (id, source_type, content, created_at) VALUES (?, ?, ?, ?)",
                (source_id, source_type, content, now_iso()),
            )
            conn.execute(
                "INSERT INTO source_links (id, source_id, entity_type, entity_id) VALUES (?, ?, ?, ?)",
                (link_id, source_id, target_type, target_id),
            )
        return source_id

    # ---- helpers for tests / inspection ------------------------------------

    def sources_for(self, target_type: str, target_id: str) -> list[dict]:
        sql = """
            SELECT s.* FROM sources s
            JOIN source_links l ON l.source_id = s.id
            WHERE l.entity_type = ? AND l.entity_id = ?
            ORDER BY s.created_at ASC
        """
        with self._connect() as conn:
            rows = conn.execute(sql, (target_type, target_id)).fetchall()
        return [dict(r) for r in rows]

    # ---- v4: delete (restricted) -------------------------------------------

    def delete(self, entity_type: str, id: str) -> dict:
        """Delete an entity by id.

        Intentionally restricted to **resources** — they're the only entity
        type without a lifecycle status field that supports "no longer
        relevant" via a transition. Everything else has a status enum
        (active/snoozed/killed for projects, lapsed/retired for
        capabilities, dropped for commitments, etc.); use the relevant
        transition tool, not delete. Hard deletion loses lineage and is
        almost never the right move outside the resource case.
        """
        if entity_type != ENTITY_RESOURCE:
            raise ValueError(
                f"delete is only allowed for resources (got {entity_type!r}). "
                f"For other entities, use the appropriate status transition."
            )
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM resources WHERE id = ?", (id,)
            ).fetchone()
            if not row:
                raise ValueError(f"resource {id} not found")
            conn.execute("DELETE FROM resources WHERE id = ?", (id,))
        return dict(row)

    # ---- v4: capability helpers --------------------------------------------

    def list_active_capabilities(
        self, category: Optional[str] = None
    ) -> list[dict]:
        sql = "SELECT * FROM capabilities WHERE status = 'active'"
        params: list[Any] = []
        if category is not None:
            if category not in VALID_CAPABILITY_CATEGORIES:
                raise ValueError(
                    f"category must be one of {sorted(VALID_CAPABILITY_CATEGORIES)}"
                )
            sql += " AND category = ?"
            params.append(category)
        sql += " ORDER BY name"
        with self._connect() as conn:
            rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        for r in rows:
            r["renewal_required"] = bool(r["renewal_required"])
        return rows

    def list_expiring_capabilities(self, within_days: int) -> list[dict]:
        """Active capabilities whose ``expires_at`` falls within
        ``within_days`` from now. Sorted by expiration date (soonest first)."""
        if not isinstance(within_days, int) or within_days < 0:
            raise ValueError("within_days must be a non-negative integer")
        cutoff = (
            datetime.now(timezone.utc) + timedelta(days=within_days)
        ).isoformat()
        sql = (
            "SELECT * FROM capabilities "
            "WHERE status = 'active' "
            "  AND expires_at IS NOT NULL "
            "  AND expires_at <= ? "
            "ORDER BY expires_at ASC"
        )
        with self._connect() as conn:
            rows = [dict(r) for r in conn.execute(sql, (cutoff,)).fetchall()]
        for r in rows:
            r["renewal_required"] = bool(r["renewal_required"])
        return rows

    def list_atrophying_capabilities(
        self, months_silent: int = 12
    ) -> list[dict]:
        """Active capabilities of atrophying categories (skill, certification,
        access) whose ``last_exercised_at`` is older than ``months_silent``
        months. Capabilities that have NEVER been exercised
        (``last_exercised_at IS NULL``) are included if their ``created_at``
        is also older than the threshold — otherwise we'd flag every newly
        captured skill.
        """
        if not isinstance(months_silent, int) or months_silent < 0:
            raise ValueError("months_silent must be a non-negative integer")
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=months_silent * 30)
        ).isoformat()
        sql = (
            "SELECT * FROM capabilities "
            "WHERE status = 'active' "
            f"  AND category IN ({','.join('?' * len(ATROPHYING_CAPABILITY_CATEGORIES))}) "
            "  AND ("
            "       last_exercised_at IS NULL AND created_at < ? "
            "    OR last_exercised_at IS NOT NULL AND last_exercised_at < ? "
            "  ) "
            "ORDER BY COALESCE(last_exercised_at, created_at) ASC"
        )
        params = [*sorted(ATROPHYING_CAPABILITY_CATEGORIES), cutoff, cutoff]
        with self._connect() as conn:
            rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        for r in rows:
            r["renewal_required"] = bool(r["renewal_required"])
        return rows

    def mark_capability_exercised(
        self,
        capability_id: str,
        when: Optional[str] = None,
        source_ref: Optional[str] = None,
    ) -> dict:
        """Bump ``last_exercised_at`` on a capability.

        ``when`` defaults to now. ``source_ref`` is informational only —
        not stored (the link is established via state_attach_source if
        the caller wants provenance).
        """
        when = when or now_iso()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM capabilities WHERE id = ?", (capability_id,)
            ).fetchone()
            if not row:
                raise ValueError(f"capability {capability_id} not found")
            conn.execute(
                """UPDATE capabilities
                   SET last_exercised_at = ?, updated_at = ?
                   WHERE id = ?""",
                (when, now_iso(), capability_id),
            )
            row = conn.execute(
                "SELECT * FROM capabilities WHERE id = ?", (capability_id,)
            ).fetchone()
        result = dict(row)
        result["renewal_required"] = bool(result["renewal_required"])
        return result

    # ---- v4: resource helpers ----------------------------------------------

    def list_resources_below_floor(self) -> list[dict]:
        sql = (
            "SELECT * FROM resources "
            "WHERE floor IS NOT NULL AND current_quantity < floor "
            "ORDER BY (current_quantity * 1.0 / floor) ASC"
        )
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql).fetchall()]

    def compute_resource_runway(self) -> list[dict]:
        """For each resource with ``burn_rate`` set, compute time-to-empty
        in months: ``current_quantity / max(burn_rate - replenish_rate, 0)``.

        Resources without a burn_rate get ``runway_months = None`` (we can't
        compute it). Resources where replenish_rate ≥ burn_rate are stable
        or growing, also ``runway_months = None`` (no depletion to count
        down to).
        """
        with self._connect() as conn:
            rows = [
                dict(r) for r in conn.execute("SELECT * FROM resources").fetchall()
            ]
        out: list[dict] = []
        for r in rows:
            entry = {
                "resource_id": r["id"],
                "name": r["name"],
                "category": r["category"],
                "unit": r["unit"],
                "current_quantity": r["current_quantity"],
                "as_of": r["as_of"],
                "burn_rate": r["burn_rate"],
                "replenish_rate": r["replenish_rate"],
                "runway_months": None,
            }
            burn = r["burn_rate"]
            if burn is not None:
                net = burn - (r["replenish_rate"] or 0)
                if net > 0 and r["current_quantity"] is not None:
                    entry["runway_months"] = r["current_quantity"] / net
            out.append(entry)
        return out

    # ---- v4: capability_requirements + gaps --------------------------------

    def add_capability_requirement(
        self,
        project_id: str,
        capability_id: Optional[str] = None,
        resource_id: Optional[str] = None,
        required_level: Optional[str] = None,
        required_amount: Optional[float] = None,
        notes: Optional[str] = None,
    ) -> dict:
        """Tag a project as needing a specific capability OR resource.

        Exactly one of capability_id / resource_id must be set. When the
        target is a capability, required_amount must be null; when it's
        a resource, required_level must be null.
        """
        if (capability_id is None) == (resource_id is None):
            raise ValueError(
                "exactly one of capability_id or resource_id must be set"
            )
        if capability_id is not None and required_amount is not None:
            raise ValueError(
                "required_amount must be null when capability_id is set"
            )
        if resource_id is not None and required_level is not None:
            raise ValueError(
                "required_level must be null when resource_id is set"
            )
        if required_level is not None and required_level not in VALID_CAPABILITY_LEVELS:
            raise ValueError(
                f"required_level must be one of {sorted(VALID_CAPABILITY_LEVELS)}"
            )
        with self._connect() as conn:
            if not conn.execute(
                "SELECT 1 FROM projects WHERE id = ?", (project_id,)
            ).fetchone():
                raise ValueError(f"project {project_id} not found")
            if capability_id and not conn.execute(
                "SELECT 1 FROM capabilities WHERE id = ?", (capability_id,)
            ).fetchone():
                raise ValueError(f"capability {capability_id} not found")
            if resource_id and not conn.execute(
                "SELECT 1 FROM resources WHERE id = ?", (resource_id,)
            ).fetchone():
                raise ValueError(f"resource {resource_id} not found")
            row = {
                "id": new_id(),
                "project_id": project_id,
                "capability_id": capability_id,
                "resource_id": resource_id,
                "required_level": required_level,
                "required_amount": required_amount,
                "notes": notes,
                "created_at": now_iso(),
            }
            conn.execute(
                """INSERT INTO capability_requirements
                   (id, project_id, capability_id, resource_id,
                    required_level, required_amount, notes, created_at)
                   VALUES
                   (:id, :project_id, :capability_id, :resource_id,
                    :required_level, :required_amount, :notes, :created_at)""",
                row,
            )
        return row

    def remove_capability_requirement(self, id: str) -> dict:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM capability_requirements WHERE id = ?", (id,)
            ).fetchone()
            if not row:
                raise ValueError(f"capability_requirement {id} not found")
            conn.execute("DELETE FROM capability_requirements WHERE id = ?", (id,))
        return dict(row)

    def list_capability_requirements(
        self, project_id: Optional[str] = None
    ) -> list[dict]:
        sql = "SELECT * FROM capability_requirements"
        params: list[Any] = []
        if project_id is not None:
            sql += " WHERE project_id = ?"
            params.append(project_id)
        sql += " ORDER BY created_at ASC"
        with self._connect() as conn:
            rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
            for r in rows:
                r["project_name"] = self._label_for(
                    conn, "projects", r.get("project_id")
                )
                r["capability_name"] = self._label_for(
                    conn, "capabilities", r.get("capability_id")
                )
                r["resource_name"] = self._label_for(
                    conn, "resources", r.get("resource_id")
                )
        for r in rows:
            r["is_satisfied"] = self._is_requirement_satisfied(r)
        return rows

    def _is_requirement_satisfied(self, req: dict) -> bool:
        """Compute is_satisfied for a capability_requirement row at read time.

        Not stored — capability/resource state changes constantly, so
        materializing satisfaction would just be a stale denormalization.
        """
        with self._connect() as conn:
            if req.get("capability_id"):
                cap = conn.execute(
                    "SELECT status, level FROM capabilities WHERE id = ?",
                    (req["capability_id"],),
                ).fetchone()
                if not cap or cap["status"] != "active":
                    return False
                if req.get("required_level"):
                    have = CAPABILITY_LEVEL_ORDER.get(cap["level"], -1)
                    need = CAPABILITY_LEVEL_ORDER[req["required_level"]]
                    if have < need:
                        return False
                return True
            if req.get("resource_id"):
                res = conn.execute(
                    "SELECT current_quantity FROM resources WHERE id = ?",
                    (req["resource_id"],),
                ).fetchone()
                if not res:
                    return False
                if req.get("required_amount") is not None:
                    if res["current_quantity"] < req["required_amount"]:
                        return False
                return True
        return False

    def list_capability_gaps(
        self, project_id: Optional[str] = None
    ) -> list[dict]:
        """Unsatisfied capability_requirements.

        Defaults to gaps on **active** projects when project_id is None
        — gaps on killed/dormant projects aren't actionable. Pass an
        explicit project_id to inspect any single project's gaps.
        """
        if project_id is not None:
            reqs = self.list_capability_requirements(project_id=project_id)
        else:
            with self._connect() as conn:
                rows = conn.execute(
                    """SELECT cr.* FROM capability_requirements cr
                       JOIN projects p ON p.id = cr.project_id
                       WHERE p.status = 'active'
                       ORDER BY cr.created_at ASC"""
                ).fetchall()
            reqs = [dict(r) for r in rows]
            with self._connect() as conn:
                for r in reqs:
                    r["project_name"] = self._label_for(
                        conn, "projects", r.get("project_id")
                    )
                    r["capability_name"] = self._label_for(
                        conn, "capabilities", r.get("capability_id")
                    )
                    r["resource_name"] = self._label_for(
                        conn, "resources", r.get("resource_id")
                    )
            for r in reqs:
                r["is_satisfied"] = self._is_requirement_satisfied(r)
        return [r for r in reqs if not r["is_satisfied"]]

    # ---- v3.1: commitment / stakeholder transitions -----------------------

    def complete_commitment(
        self,
        id: str,
        outcome: Optional[str] = None,
    ) -> dict:
        """Transition an open commitment to done with an optional outcome."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM commitments WHERE id = ?", (id,)
            ).fetchone()
            if not row:
                raise ValueError(f"commitment {id} not found")
            if row["status"] != "open":
                raise ValueError(
                    f"commitment {id} is {row['status']}, cannot complete "
                    f"(only open commitments can be completed)"
                )
            conn.execute(
                """UPDATE commitments
                   SET status = 'done', completed_at = ?, outcome = ?
                   WHERE id = ?""",
                (now_iso(), outcome, id),
            )
            updated = conn.execute(
                "SELECT * FROM commitments WHERE id = ?", (id,)
            ).fetchone()
        return dict(updated)

    def drop_commitment(self, id: str, reason: Optional[str] = None) -> dict:
        """Mark an open commitment as dropped with a reason."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM commitments WHERE id = ?", (id,)
            ).fetchone()
            if not row:
                raise ValueError(f"commitment {id} not found")
            if row["status"] != "open":
                raise ValueError(
                    f"commitment {id} is {row['status']}, cannot drop"
                )
            conn.execute(
                """UPDATE commitments
                   SET status = 'dropped', dropped_reason = ?, completed_at = ?
                   WHERE id = ?""",
                (reason, now_iso(), id),
            )
            updated = conn.execute(
                "SELECT * FROM commitments WHERE id = ?", (id,)
            ).fetchone()
        return dict(updated)

    def update_contact(
        self,
        stakeholder_id: str,
        when: Optional[str] = None,
        note: Optional[str] = None,
    ) -> dict:
        """Record a contact event with a stakeholder.

        Updates last_contact_at (defaults to now if not given) and appends
        a timestamped line to the stakeholder's notes field if a note is
        provided. Preserves prior notes; the contact log is the running
        history in the notes column.
        """
        when = when or now_iso()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM stakeholders WHERE id = ?", (stakeholder_id,)
            ).fetchone()
            if not row:
                raise ValueError(f"stakeholder {stakeholder_id} not found")
            new_notes = row["notes"]
            if note:
                prefix = f"[{when}] {note}"
                new_notes = f"{new_notes}\n{prefix}" if new_notes else prefix
            conn.execute(
                """UPDATE stakeholders
                   SET last_contact_at = ?, notes = ?, updated_at = ?
                   WHERE id = ?""",
                (when, new_notes, now_iso(), stakeholder_id),
            )
            updated = conn.execute(
                "SELECT * FROM stakeholders WHERE id = ?", (stakeholder_id,)
            ).fetchone()
        return dict(updated)

    def link_stakeholder_to_project(
        self,
        stakeholder_id: str,
        project_id: str,
        role_in_project: Optional[str] = None,
    ) -> dict:
        with self._connect() as conn:
            if not conn.execute(
                "SELECT 1 FROM stakeholders WHERE id = ?", (stakeholder_id,)
            ).fetchone():
                raise ValueError(f"stakeholder {stakeholder_id} not found")
            if not conn.execute(
                "SELECT 1 FROM projects WHERE id = ?", (project_id,)
            ).fetchone():
                raise ValueError(f"project {project_id} not found")
            row = {
                "id": new_id(),
                "stakeholder_id": stakeholder_id,
                "project_id": project_id,
                "role_in_project": role_in_project,
                "created_at": now_iso(),
            }
            conn.execute(
                """INSERT INTO stakeholder_project_links
                   (id, stakeholder_id, project_id, role_in_project, created_at)
                   VALUES
                   (:id, :stakeholder_id, :project_id, :role_in_project, :created_at)""",
                row,
            )
        return row

    def list_stakeholder_project_links(
        self,
        stakeholder_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> list[dict]:
        sql = "SELECT * FROM stakeholder_project_links"
        clauses: list[str] = []
        params: list[Any] = []
        if stakeholder_id is not None:
            clauses.append("stakeholder_id = ?")
            params.append(stakeholder_id)
        if project_id is not None:
            clauses.append("project_id = ?")
            params.append(project_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC"
        with self._connect() as conn:
            rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
            for r in rows:
                r["stakeholder_name"] = self._label_for(
                    conn, "stakeholders", r.get("stakeholder_id")
                )
                r["project_name"] = self._label_for(
                    conn, "projects", r.get("project_id")
                )
        return rows

    def list_overdue_contacts(self, threshold_days: int) -> list[dict]:
        """Stakeholders not contacted in more than threshold_days days.

        Includes stakeholders with last_contact_at = NULL (never contacted
        since the table was created) — those are arguably the most overdue.
        Ordered by oldest contact first.
        """
        if not isinstance(threshold_days, int) or threshold_days < 0:
            raise ValueError("threshold_days must be a non-negative integer")
        cutoff = (
            datetime.now(timezone.utc)
            - timedelta(days=threshold_days)
        ).isoformat()
        sql = """
            SELECT * FROM stakeholders
            WHERE last_contact_at IS NULL OR last_contact_at < ?
            ORDER BY (last_contact_at IS NULL) DESC, last_contact_at ASC
        """
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql, (cutoff,)).fetchall()]

    # ---- v3: life_units, assessments, observations -------------------------

    # ---- v5: search index seeding + querying --------------------------

    def _seed_search_index(self, conn: sqlite3.Connection) -> None:
        """Backfill ``search_index`` from existing source tables.

        Idempotent: if the index already has rows we assume it's been
        populated (either by an earlier backfill or by sync triggers on
        live writes) and skip. The triggers keep it in sync from now on.
        """
        existing = conn.execute(
            "SELECT COUNT(*) FROM search_index"
        ).fetchone()[0]
        if existing > 0:
            return
        # For each searchable entity, rewrite the trigger-form expressions
        # (which use NEW.<col>) into backfill-form (qualified table.col)
        # and run a bulk INSERT … SELECT.
        for entity_type, table, title_expr, body_expr, created_expr in SEARCHABLE_ENTITIES:
            title = title_expr.replace("NEW.", f"{table}.")
            body = body_expr.replace("NEW.", f"{table}.")
            created = created_expr.replace("NEW.", f"{table}.")
            conn.execute(
                f"INSERT INTO search_index "
                f"  (entity_type, entity_id, title, body, created_at) "
                f"SELECT '{entity_type}', {table}.id, {title}, {body}, {created} "
                f"FROM {table}"
            )

    def search(
        self,
        query: str,
        entity_types: Optional[list[str]] = None,
        since: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict]:
        """Lexical search over indexed entities.

        Returns ranked results (BM25, higher = more relevant) with
        ``snippet`` strings containing ``<mark>…</mark>`` around matched
        spans. Empty or near-empty queries return ``[]`` cleanly.

        Query handling:
          - ≤1 char queries → empty result
          - Queries containing explicit FTS5 operators (``AND``, ``OR``,
            ``NOT``, trailing ``*``) are passed through unchanged.
          - Everything else is wrapped as a phrase query, so punctuation
            and stray keywords don't trigger surprise behavior.

        Filters: ``entity_types`` (list of indexed types), ``since`` (ISO
        timestamp lower bound on created_at), ``limit`` (default 20, max
        100).
        """
        if not isinstance(query, str):
            return []
        q = query.strip()
        if len(q) < 2:
            return []

        # Phrase-wrap unless the caller used explicit FTS5 operators.
        if _FTS5_ADVANCED_RE.search(q):
            match_query = q
        else:
            # Escape internal double quotes so they don't terminate the
            # phrase prematurely; FTS5 uses "" as an escape inside "".
            match_query = '"' + q.replace('"', '""') + '"'

        sql = (
            "SELECT entity_type, entity_id, title, "
            "       snippet(search_index, 3, '<mark>', '</mark>', '...', 32) AS snippet, "
            "       -bm25(search_index) AS score, "
            "       created_at "
            "FROM search_index "
            "WHERE search_index MATCH ?"
        )
        params: list[Any] = [match_query]

        if entity_types is not None:
            if not isinstance(entity_types, (list, tuple)) or not entity_types:
                raise ValueError(
                    "entity_types must be a non-empty list of strings"
                )
            for et in entity_types:
                if et not in SEARCHABLE_ENTITY_TYPES:
                    raise ValueError(
                        f"entity_type {et!r} is not searchable; must be one of "
                        f"{sorted(SEARCHABLE_ENTITY_TYPES)}"
                    )
            placeholders = ",".join("?" * len(entity_types))
            sql += f" AND entity_type IN ({placeholders})"
            params.extend(entity_types)

        if since is not None:
            sql += " AND created_at >= ?"
            params.append(since)

        if not isinstance(limit, int) or limit <= 0:
            limit = 20
        limit = min(limit, 100)
        sql += " ORDER BY score DESC LIMIT ?"
        params.append(limit)

        with self._connect() as conn:
            try:
                rows = conn.execute(sql, params).fetchall()
            except sqlite3.OperationalError as e:
                # FTS5 raises on malformed queries. Surface as empty result
                # rather than as an opaque tool error — the user retypes.
                log.warning("FTS5 query rejected (%s): %s", e, match_query)
                return []
        return [dict(r) for r in rows]

    # ---- v6: end-to-end logging --------------------------------------------

    def begin_chat(self, client_info: Optional[str] = None) -> str:
        """Open a new chat row and return its id.

        Called by the server's dispatch wrapper the first time a tool is
        called from a new MCP connection (or once per process when no
        session context is available).
        """
        now = now_iso()
        chat_id = new_id()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO chats (id, started_at, ended_at, client_info, "
                "                   created_at) "
                "VALUES (?, ?, NULL, ?, ?)",
                (chat_id, now, client_info, now),
            )
        return chat_id

    def end_chat(self, chat_id: str) -> None:
        """Mark a chat as ended (best-effort — many MCP transports don't
        notify on close, so this is nice-to-have, not load-bearing)."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE chats SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
                (now_iso(), chat_id),
            )

    def begin_operation(
        self,
        chat_id: str,
        skill_name: str,
        notes: Optional[str] = None,
    ) -> str:
        """Open a new operation. Auto-abandons any in-progress operation
        in the same chat so a forgotten end_operation doesn't leave a
        ghost session alive forever.
        """
        if not skill_name:
            raise ValueError("skill_name is required")
        now = now_iso()
        op_id = new_id()
        with self._connect() as conn:
            # Auto-abandon any active operation in this chat. Append an
            # explanation to its notes so the audit trail says why.
            conn.execute(
                "UPDATE operations "
                "SET status = 'abandoned', "
                "    completed_at = ?, "
                "    notes = CASE "
                "      WHEN notes IS NULL OR notes = '' THEN ? "
                "      ELSE notes || ' | ' || ? "
                "    END "
                "WHERE chat_id = ? AND status = 'in_progress'",
                (
                    now,
                    f"auto-closed: new operation '{skill_name}' started "
                    f"without explicit end",
                    f"auto-closed: new operation '{skill_name}' started "
                    f"without explicit end",
                    chat_id,
                ),
            )
            conn.execute(
                "INSERT INTO operations (id, chat_id, skill_name, status, "
                "                        started_at, notes, created_at) "
                "VALUES (?, ?, ?, 'in_progress', ?, ?, ?)",
                (op_id, chat_id, skill_name, now, notes, now),
            )
        return op_id

    def end_operation(
        self,
        operation_id: str,
        status: str = "completed",
        notes: Optional[str] = None,
    ) -> dict:
        """Close an operation. ``status`` must be ``completed`` or
        ``abandoned``; ``in_progress`` is not a valid termination."""
        if status not in ("completed", "abandoned"):
            raise ValueError(
                f"end_operation status must be 'completed' or 'abandoned', "
                f"got {status!r}"
            )
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM operations WHERE id = ?", (operation_id,)
            ).fetchone()
            if not row:
                raise ValueError(f"operation {operation_id} not found")
            if row["status"] != "in_progress":
                raise ValueError(
                    f"operation {operation_id} is {row['status']}, "
                    f"cannot end (already terminal)"
                )
            conn.execute(
                "UPDATE operations "
                "SET status = ?, completed_at = ?, "
                "    notes = COALESCE(?, notes) "
                "WHERE id = ?",
                (status, now_iso(), notes, operation_id),
            )
            updated = conn.execute(
                "SELECT * FROM operations WHERE id = ?", (operation_id,)
            ).fetchone()
        return dict(updated)

    def get_active_operation(self, chat_id: str) -> Optional[str]:
        """Return the in-progress operation_id for a chat, or None."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM operations "
                "WHERE chat_id = ? AND status = 'in_progress' "
                "ORDER BY started_at DESC LIMIT 1",
                (chat_id,),
            ).fetchone()
        return row["id"] if row else None

    def log_tool_call(
        self,
        chat_id: str,
        operation_id: Optional[str],
        tool_name: str,
        status: str,
        started_at: str,
        completed_at: str,
        duration_ms: int,
        arguments_json: str,
        response_json: Optional[str],
        arguments_size_bytes: int,
        response_size_bytes: Optional[int],
        error_message: Optional[str],
        payload_size_warning: bool,
    ) -> None:
        """Persist one tool_call row. Called from the dispatch wrapper.

        Designed to be call-it-and-forget — any internal failure raises
        a sqlite error which the dispatcher catches and writes to
        stderr. The tool's own return value is never blocked on this.
        """
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO tool_calls "
                "(id, chat_id, operation_id, tool_name, status, "
                " started_at, completed_at, duration_ms, "
                " arguments_json, response_json, "
                " arguments_size_bytes, response_size_bytes, "
                " error_message, payload_size_warning, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    new_id(), chat_id, operation_id, tool_name, status,
                    started_at, completed_at, duration_ms,
                    arguments_json, response_json,
                    arguments_size_bytes, response_size_bytes,
                    error_message, 1 if payload_size_warning else 0,
                    now_iso(),
                ),
            )

    def log_resource_read(
        self,
        chat_id: str,
        operation_id: Optional[str],
        resource_uri: str,
        content_size_bytes: Optional[int],
        started_at: str,
        duration_ms: int,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO resource_reads "
                "(id, chat_id, operation_id, resource_uri, "
                " content_size_bytes, started_at, duration_ms, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    new_id(), chat_id, operation_id, resource_uri,
                    content_size_bytes, started_at, duration_ms,
                    now_iso(),
                ),
            )

    # ---- v6: query surface for the trace skill -----------------------------

    def list_recent_chats(
        self,
        limit: int = 20,
        since: Optional[str] = None,
    ) -> list[dict]:
        if not isinstance(limit, int) or limit <= 0:
            limit = 20
        limit = min(limit, 200)
        sql = "SELECT * FROM chats"
        params: list[Any] = []
        if since is not None:
            sql += " WHERE started_at >= ?"
            params.append(since)
        sql += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def list_recent_operations(
        self,
        skill_name: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 20,
        since: Optional[str] = None,
    ) -> list[dict]:
        if not isinstance(limit, int) or limit <= 0:
            limit = 20
        limit = min(limit, 200)
        if status is not None and status not in (
            "in_progress", "completed", "abandoned"
        ):
            raise ValueError(
                "status must be 'in_progress', 'completed', or 'abandoned'"
            )
        sql = "SELECT * FROM operations"
        clauses: list[str] = []
        params: list[Any] = []
        if skill_name is not None:
            clauses.append("skill_name = ?")
            params.append(skill_name)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if since is not None:
            clauses.append("started_at >= ?")
            params.append(since)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def get_operation_trace(self, operation_id: str) -> Optional[dict]:
        """Return an operation plus its tool_calls and resource_reads,
        ordered chronologically. ``None`` if the operation doesn't exist.
        """
        with self._connect() as conn:
            op = conn.execute(
                "SELECT * FROM operations WHERE id = ?", (operation_id,)
            ).fetchone()
            if not op:
                return None
            tool_calls = [dict(r) for r in conn.execute(
                "SELECT * FROM tool_calls WHERE operation_id = ? "
                "ORDER BY started_at ASC",
                (operation_id,),
            ).fetchall()]
            resource_reads = [dict(r) for r in conn.execute(
                "SELECT * FROM resource_reads WHERE operation_id = ? "
                "ORDER BY started_at ASC",
                (operation_id,),
            ).fetchall()]
        # Normalize the boolean flag for clients.
        for tc in tool_calls:
            tc["payload_size_warning"] = bool(tc.get("payload_size_warning"))
        return {
            "operation": dict(op),
            "tool_calls": tool_calls,
            "resource_reads": resource_reads,
        }

    def get_chat_trace(self, chat_id: str) -> Optional[dict]:
        """Return a chat plus all its operations, tool_calls (operation-
        bound and ad-hoc), and resource_reads. ``None`` if the chat doesn't
        exist."""
        with self._connect() as conn:
            chat = conn.execute(
                "SELECT * FROM chats WHERE id = ?", (chat_id,)
            ).fetchone()
            if not chat:
                return None
            operations = [dict(r) for r in conn.execute(
                "SELECT * FROM operations WHERE chat_id = ? "
                "ORDER BY started_at ASC",
                (chat_id,),
            ).fetchall()]
            tool_calls = [dict(r) for r in conn.execute(
                "SELECT * FROM tool_calls WHERE chat_id = ? "
                "ORDER BY started_at ASC",
                (chat_id,),
            ).fetchall()]
            resource_reads = [dict(r) for r in conn.execute(
                "SELECT * FROM resource_reads WHERE chat_id = ? "
                "ORDER BY started_at ASC",
                (chat_id,),
            ).fetchall()]
        for tc in tool_calls:
            tc["payload_size_warning"] = bool(tc.get("payload_size_warning"))
        return {
            "chat": dict(chat),
            "operations": operations,
            "tool_calls": tool_calls,
            "resource_reads": resource_reads,
        }

    def list_abandoned_operations(
        self,
        since: Optional[str] = None,
        stuck_for_hours: int = 1,
    ) -> list[dict]:
        """Operations stuck in ``in_progress`` past a threshold (default
        1 hour) — either the user aborted mid-flow or a skill forgot to
        call end_operation. Useful for the trace skill's 'abandoned' view.
        Also returns operations already marked ``abandoned``."""
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=stuck_for_hours)
        ).isoformat()
        sql = (
            "SELECT * FROM operations "
            "WHERE (status = 'abandoned') "
            "   OR (status = 'in_progress' AND started_at < ?)"
        )
        params: list[Any] = [cutoff]
        if since is not None:
            sql += " AND started_at >= ?"
            params.append(since)
        sql += " ORDER BY started_at DESC LIMIT 200"
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def list_errors(
        self,
        since: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        """Recent tool_calls with status='error'."""
        if not isinstance(limit, int) or limit <= 0:
            limit = 50
        limit = min(limit, 500)
        sql = "SELECT * FROM tool_calls WHERE status = 'error'"
        params: list[Any] = []
        if since is not None:
            sql += " AND started_at >= ?"
            params.append(since)
        sql += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        for r in rows:
            r["payload_size_warning"] = bool(r.get("payload_size_warning"))
        return rows

    def purge_logs(self, before_date: str) -> dict:
        """Delete log rows older than ``before_date``. Returns per-table
        row counts of what was removed.

        Manual purge only — no automatic retention. Children-first order
        keeps FK constraints happy without needing CASCADE."""
        if not isinstance(before_date, str) or not before_date:
            raise ValueError("before_date is required (ISO timestamp string)")
        counts: dict[str, int] = {}
        with self._connect() as conn:
            for table in (
                "tool_calls", "resource_reads", "operations", "chats",
            ):
                cur = conn.execute(
                    f"DELETE FROM {table} WHERE started_at < ?",
                    (before_date,),
                )
                counts[table] = cur.rowcount
        return counts

    def _seed_life_units(self) -> None:
        """Populate the 16 Strack SLUs if life_units is empty. Idempotent."""
        with self._connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM life_units").fetchone()[0]
            if count > 0:
                return
            now = now_iso()
            rows = [
                {
                    "id": new_id(),
                    "name": name,
                    "area": area,
                    "description": description,
                    "active": 1,
                    "custom": 0,
                    "created_at": now,
                    "updated_at": now,
                }
                for (name, area, description) in SEED_LIFE_UNITS
            ]
            conn.executemany(
                """INSERT INTO life_units
                   (id, name, area, description, active, custom,
                    created_at, updated_at)
                   VALUES
                   (:id, :name, :area, :description, :active, :custom,
                    :created_at, :updated_at)""",
                rows,
            )

    @staticmethod
    def _compute_quadrant(importance: int, satisfaction: int) -> str:
        hi = QUADRANT_HIGH_THRESHOLD
        if importance >= hi and satisfaction >= hi:
            return "upper_right"
        if importance >= hi and satisfaction < hi:
            return "upper_left"
        if importance < hi and satisfaction >= hi:
            return "lower_right"
        return "lower_left"

    @staticmethod
    def _validate_rating(importance: int, satisfaction: int) -> None:
        for name, val in (("importance", importance), ("satisfaction", satisfaction)):
            if not isinstance(val, int) or val < 0 or val > 10:
                raise ValueError(f"{name} must be an integer between 0 and 10")

    @staticmethod
    def _row_with_bool(row: dict, fields: tuple[str, ...]) -> dict:
        for f in fields:
            if f in row and row[f] is not None:
                row[f] = bool(row[f])
        return row

    def list_active_slus(self, area: Optional[str] = None) -> list[dict]:
        sql = "SELECT * FROM life_units WHERE active = 1"
        params: list[Any] = []
        if area is not None:
            if area not in VALID_SLA:
                raise ValueError(
                    f"area must be one of {sorted(VALID_SLA)}, got {area!r}"
                )
            sql += " AND area = ?"
            params.append(area)
        sql += " ORDER BY area, name"
        with self._connect() as conn:
            rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        return [self._row_with_bool(r, ("active", "custom")) for r in rows]

    def list_life_units(self, include_inactive: bool = True) -> list[dict]:
        sql = "SELECT * FROM life_units"
        if not include_inactive:
            sql += " WHERE active = 1"
        sql += " ORDER BY area, name"
        with self._connect() as conn:
            rows = [dict(r) for r in conn.execute(sql).fetchall()]
        return [self._row_with_bool(r, ("active", "custom")) for r in rows]

    def _require_life_unit(self, life_unit_id: str) -> sqlite3.Row:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM life_units WHERE id = ?", (life_unit_id,)
            ).fetchone()
        if not row:
            raise ValueError(f"life_unit {life_unit_id} not found")
        return row

    def deactivate_slu(self, life_unit_id: str, reason: Optional[str] = None) -> dict:
        # reason is informational only — captured in life_units.description or
        # left for the caller's observation log. We don't add a column for it.
        self._require_life_unit(life_unit_id)
        with self._connect() as conn:
            conn.execute(
                "UPDATE life_units SET active = 0, updated_at = ? WHERE id = ?",
                (now_iso(), life_unit_id),
            )
            row = conn.execute(
                "SELECT * FROM life_units WHERE id = ?", (life_unit_id,)
            ).fetchone()
        return self._row_with_bool(dict(row), ("active", "custom"))

    def activate_slu(self, life_unit_id: str) -> dict:
        self._require_life_unit(life_unit_id)
        with self._connect() as conn:
            conn.execute(
                "UPDATE life_units SET active = 1, updated_at = ? WHERE id = ?",
                (now_iso(), life_unit_id),
            )
            row = conn.execute(
                "SELECT * FROM life_units WHERE id = ?", (life_unit_id,)
            ).fetchone()
        return self._row_with_bool(dict(row), ("active", "custom"))

    def start_assessment_session(
        self,
        type: str,
        inherited_from_session_id: Optional[str] = None,
    ) -> dict:
        """Begin an assessment session. Returns a session_id and the
        normalized type.

        v5.1: accepts ``full`` / ``patch`` / ``great_life``. ``slu_portfolio``
        is kept as a legacy alias of ``full`` (v3 callers and tests still
        work). ``inherited_from_session_id`` is required for ``patch`` and
        forbidden otherwise.
        """
        if type not in VALID_ASSESSMENT_TYPES:
            raise ValueError(
                f"type must be one of {sorted(VALID_ASSESSMENT_TYPES)}, got {type!r}"
            )
        # Normalize the legacy alias so callers downstream see only the
        # current vocabulary.
        normalized = "full" if type == "slu_portfolio" else type

        if normalized == "patch":
            if not inherited_from_session_id:
                raise ValueError(
                    "patch sessions must specify inherited_from_session_id"
                )
            with self._connect() as conn:
                exists = conn.execute(
                    "SELECT 1 FROM slu_assessments WHERE session_id = ? LIMIT 1",
                    (inherited_from_session_id,),
                ).fetchone()
                if not exists:
                    raise ValueError(
                        f"inherited_from_session_id "
                        f"{inherited_from_session_id} has no ratings on file"
                    )
        elif inherited_from_session_id is not None:
            raise ValueError(
                "inherited_from_session_id is only valid for patch sessions"
            )
        return {
            "session_id": new_id(),
            "type": normalized,
            "inherited_from_session_id": inherited_from_session_id,
            "started_at": now_iso(),
        }

    def record_slu_rating(
        self,
        session_id: str,
        life_unit_id: str,
        importance: int,
        satisfaction: int,
        hours_per_week: Optional[float] = None,
        notes: Optional[str] = None,
        session_type: str = "full",
        inherited_from_session_id: Optional[str] = None,
    ) -> dict:
        """Record one SLU rating tied to a session.

        v5.1: ``session_type`` discriminates full vs. patch (default
        ``full`` preserves v3 behavior). For ``quick_update`` —
        a session-less rating — call ``record_quick_slu_update`` instead.
        """
        if not session_id:
            raise ValueError("session_id is required")
        if session_type not in ("full", "patch"):
            raise ValueError(
                f"session_type must be 'full' or 'patch' for session-bound "
                f"ratings (use record_quick_slu_update for 'quick_update'); "
                f"got {session_type!r}"
            )
        if session_type == "patch" and not inherited_from_session_id:
            raise ValueError(
                "patch ratings must carry inherited_from_session_id "
                "(use the value returned by start_assessment_session)"
            )
        if session_type != "patch" and inherited_from_session_id is not None:
            raise ValueError(
                "inherited_from_session_id is only valid for patch ratings"
            )
        self._validate_rating(importance, satisfaction)
        self._require_life_unit(life_unit_id)
        if hours_per_week is not None:
            if not isinstance(hours_per_week, (int, float)) or hours_per_week < 0:
                raise ValueError("hours_per_week must be a non-negative number")
        quadrant = self._compute_quadrant(importance, satisfaction)
        row = {
            "id": new_id(),
            "life_unit_id": life_unit_id,
            "assessed_at": now_iso(),
            "importance": importance,
            "satisfaction": satisfaction,
            "hours_per_week": float(hours_per_week) if hours_per_week is not None else None,
            "quadrant": quadrant,
            "notes": notes,
            "session_id": session_id,
            "session_type": session_type,
            "inherited_from_session_id": inherited_from_session_id,
        }
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO slu_assessments
                   (id, life_unit_id, assessed_at, importance, satisfaction,
                    hours_per_week, quadrant, notes, session_id,
                    session_type, inherited_from_session_id)
                   VALUES
                   (:id, :life_unit_id, :assessed_at, :importance, :satisfaction,
                    :hours_per_week, :quadrant, :notes, :session_id,
                    :session_type, :inherited_from_session_id)""",
                row,
            )
        return row

    def record_quick_slu_update(
        self,
        life_unit_id: str,
        importance: int,
        satisfaction: int,
        hours_per_week: Optional[float] = None,
        notes: Optional[str] = None,
    ) -> dict:
        """Record a standalone SLU rating change.

        Writes a row with ``session_id=NULL`` and
        ``session_type='quick_update'``. Visible in per-SLU trajectories
        but excluded from portfolio snapshot comparisons by
        ``compute_portfolio_quadrants`` and the scanner's
        ``quadrant_shift`` signal.
        """
        self._validate_rating(importance, satisfaction)
        self._require_life_unit(life_unit_id)
        if hours_per_week is not None:
            if not isinstance(hours_per_week, (int, float)) or hours_per_week < 0:
                raise ValueError("hours_per_week must be a non-negative number")
        quadrant = self._compute_quadrant(importance, satisfaction)
        row = {
            "id": new_id(),
            "life_unit_id": life_unit_id,
            "assessed_at": now_iso(),
            "importance": importance,
            "satisfaction": satisfaction,
            "hours_per_week": float(hours_per_week) if hours_per_week is not None else None,
            "quadrant": quadrant,
            "notes": notes,
            "session_id": None,
            "session_type": "quick_update",
            "inherited_from_session_id": None,
        }
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO slu_assessments
                   (id, life_unit_id, assessed_at, importance, satisfaction,
                    hours_per_week, quadrant, notes, session_id,
                    session_type, inherited_from_session_id)
                   VALUES
                   (:id, :life_unit_id, :assessed_at, :importance, :satisfaction,
                    :hours_per_week, :quadrant, :notes, :session_id,
                    :session_type, :inherited_from_session_id)""",
                row,
            )
        return row

    def get_latest_full_session(self) -> Optional[dict]:
        """Return the most recent SLU portfolio session (``full`` or
        ``patch``) plus its ratings. Used by ``patch_portfolio`` to know
        which ratings to inherit. Returns ``None`` if no such session
        exists.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT session_id, session_type, MAX(assessed_at) AS latest "
                "FROM slu_assessments "
                "WHERE session_id IS NOT NULL "
                f"  AND session_type IN ({','.join('?' * len(SNAPSHOT_SESSION_TYPES))}) "
                "GROUP BY session_id "
                "ORDER BY latest DESC LIMIT 1",
                sorted(SNAPSHOT_SESSION_TYPES),
            ).fetchone()
            if not row:
                return None
            session_id = row["session_id"]
            ratings = [
                dict(r) for r in conn.execute(
                    "SELECT * FROM slu_assessments WHERE session_id = ? "
                    "ORDER BY assessed_at",
                    (session_id,),
                ).fetchall()
            ]
        return {
            "session_id": session_id,
            "session_type": row["session_type"],
            "assessed_at": row["latest"],
            "ratings": ratings,
        }

    def record_perma_rating(
        self,
        session_id: str,
        dimension: str,
        importance: int,
        satisfaction: int,
        notes: Optional[str] = None,
    ) -> dict:
        if not session_id:
            raise ValueError("session_id is required")
        if dimension not in VALID_PERMA_DIMENSIONS:
            raise ValueError(
                f"dimension must be one of {sorted(VALID_PERMA_DIMENSIONS)}, "
                f"got {dimension!r}"
            )
        self._validate_rating(importance, satisfaction)
        row = {
            "id": new_id(),
            "dimension": dimension,
            "assessed_at": now_iso(),
            "importance": importance,
            "satisfaction": satisfaction,
            "notes": notes,
            "session_id": session_id,
        }
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO great_life_dimensions
                   (id, dimension, assessed_at, importance, satisfaction,
                    notes, session_id)
                   VALUES
                   (:id, :dimension, :assessed_at, :importance, :satisfaction,
                    :notes, :session_id)""",
                row,
            )
        return row

    def list_perma_ratings(
        self,
        session_id: Optional[str] = None,
        dimension: Optional[str] = None,
        since: Optional[str] = None,
    ) -> list[dict]:
        """Read PERMA-V ratings the user has recorded.

        Mirrors list_assessments for the great_life_dimensions table. The
        scanner uses this to load the user's stated values as an
        orientation signal — without it, the scanner has no idea which
        dimensions the user actually cares about.
        """
        if dimension is not None and dimension not in VALID_PERMA_DIMENSIONS:
            raise ValueError(
                f"dimension must be one of {sorted(VALID_PERMA_DIMENSIONS)}, "
                f"got {dimension!r}"
            )
        sql = "SELECT * FROM great_life_dimensions"
        clauses: list[str] = []
        params: list[Any] = []
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if dimension is not None:
            clauses.append("dimension = ?")
            params.append(dimension)
        if since is not None:
            clauses.append("assessed_at >= ?")
            params.append(since)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY assessed_at DESC"
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def compute_portfolio_quadrants(
        self, session_id: Optional[str] = None
    ) -> dict:
        """Most recent portfolio snapshot's SLUs grouped by quadrant.

        If session_id is None, picks the most recent ``full``-or-``patch``
        session. Quick-updates are deliberately excluded — they're
        intentionally not a snapshot, just an in-the-moment correction.

        Each entry in the quadrant lists merges the SLU's name / area /
        description with the assessment fields.
        """
        with self._connect() as conn:
            if session_id is None:
                row = conn.execute(
                    "SELECT session_id, MAX(assessed_at) AS latest "
                    "FROM slu_assessments WHERE session_id IS NOT NULL "
                    f"  AND session_type IN ({','.join('?' * len(SNAPSHOT_SESSION_TYPES))}) "
                    "GROUP BY session_id ORDER BY latest DESC LIMIT 1",
                    sorted(SNAPSHOT_SESSION_TYPES),
                ).fetchone()
                if not row:
                    return {
                        "session_id": None,
                        "assessed_at": None,
                        "quadrants": {q: [] for q in VALID_QUADRANTS},
                    }
                session_id = row["session_id"]
            rows = conn.execute(
                """SELECT a.*, u.name AS slu_name, u.area AS slu_area,
                          u.description AS slu_description
                   FROM slu_assessments a
                   JOIN life_units u ON u.id = a.life_unit_id
                   WHERE a.session_id = ?
                   ORDER BY a.importance DESC, a.satisfaction ASC""",
                (session_id,),
            ).fetchall()
        quadrants: dict[str, list[dict]] = {q: [] for q in VALID_QUADRANTS}
        latest = None
        for r in rows:
            d = dict(r)
            quadrants[d["quadrant"]].append(d)
            if latest is None or d["assessed_at"] > latest:
                latest = d["assessed_at"]
        return {
            "session_id": session_id,
            "assessed_at": latest,
            "quadrants": quadrants,
        }

    def list_assessments(
        self,
        life_unit_id: Optional[str] = None,
        since: Optional[str] = None,
    ) -> list[dict]:
        sql = "SELECT * FROM slu_assessments"
        clauses: list[str] = []
        params: list[Any] = []
        if life_unit_id is not None:
            clauses.append("life_unit_id = ?")
            params.append(life_unit_id)
        if since is not None:
            clauses.append("assessed_at >= ?")
            params.append(since)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY assessed_at DESC"
        with self._connect() as conn:
            rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        return rows

    def link_entity_to_slu(
        self,
        entity_type: str,
        entity_id: str,
        life_unit_id: str,
    ) -> dict:
        if entity_type not in VALID_SLU_LINK_ENTITY_TYPES:
            raise ValueError(
                f"entity_type must be one of {sorted(VALID_SLU_LINK_ENTITY_TYPES)}, "
                f"got {entity_type!r}"
            )
        if not entity_id:
            raise ValueError("entity_id is required")
        self._require_life_unit(life_unit_id)
        row = {
            "id": new_id(),
            "entity_type": entity_type,
            "entity_id": entity_id,
            "life_unit_id": life_unit_id,
            "created_at": now_iso(),
        }
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO entity_slu_links
                   (id, entity_type, entity_id, life_unit_id, created_at)
                   VALUES (:id, :entity_type, :entity_id, :life_unit_id, :created_at)""",
                row,
            )
        return row

    def list_entity_slu_links(
        self,
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
        life_unit_id: Optional[str] = None,
        since: Optional[str] = None,
    ) -> list[dict]:
        sql = "SELECT * FROM entity_slu_links"
        clauses: list[str] = []
        params: list[Any] = []
        if entity_type is not None:
            clauses.append("entity_type = ?")
            params.append(entity_type)
        if entity_id is not None:
            clauses.append("entity_id = ?")
            params.append(entity_id)
        if life_unit_id is not None:
            clauses.append("life_unit_id = ?")
            params.append(life_unit_id)
        if since is not None:
            clauses.append("created_at >= ?")
            params.append(since)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC"
        with self._connect() as conn:
            rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
            for r in rows:
                r["life_unit_name"] = self._label_for(
                    conn, "life_units", r.get("life_unit_id")
                )
                r["entity_label"] = self._label_for_entity(
                    conn, r.get("entity_type"), r.get("entity_id")
                )
        return rows

    def write_observation(
        self,
        life_unit_id: str,
        signal: str,
        severity: str,
        evidence: dict,
        notes: Optional[str] = None,
    ) -> dict:
        if signal not in VALID_OBS_SIGNALS:
            raise ValueError(
                f"signal must be one of {sorted(VALID_OBS_SIGNALS)}, got {signal!r}"
            )
        if severity not in VALID_OBS_SEVERITY:
            raise ValueError(
                f"severity must be one of {sorted(VALID_OBS_SEVERITY)}, got {severity!r}"
            )
        if not isinstance(evidence, dict):
            raise ValueError("evidence must be an object")
        self._require_life_unit(life_unit_id)
        row = {
            "id": new_id(),
            "life_unit_id": life_unit_id,
            "observed_at": now_iso(),
            "signal": signal,
            "severity": severity,
            "evidence": json.dumps(evidence),
            "notes": notes,
        }
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO slu_observations
                   (id, life_unit_id, observed_at, signal, severity,
                    evidence, notes)
                   VALUES
                   (:id, :life_unit_id, :observed_at, :signal, :severity,
                    :evidence, :notes)""",
                row,
            )
        out = dict(row)
        out["evidence"] = evidence
        return out

    def list_observations(
        self,
        life_unit_id: Optional[str] = None,
        since: Optional[str] = None,
        severity: Optional[str] = None,
    ) -> list[dict]:
        sql = "SELECT * FROM slu_observations"
        clauses: list[str] = []
        params: list[Any] = []
        if life_unit_id is not None:
            clauses.append("life_unit_id = ?")
            params.append(life_unit_id)
        if since is not None:
            clauses.append("observed_at >= ?")
            params.append(since)
        if severity is not None:
            if severity not in VALID_OBS_SEVERITY:
                raise ValueError(
                    f"severity must be one of {sorted(VALID_OBS_SEVERITY)}"
                )
            clauses.append("severity = ?")
            params.append(severity)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY observed_at DESC"
        with self._connect() as conn:
            rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
            for r in rows:
                r["evidence"] = json.loads(r["evidence"])
                r["life_unit_name"] = self._label_for(
                    conn, "life_units", r.get("life_unit_id")
                )
        return rows
