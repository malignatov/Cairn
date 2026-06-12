"""Tests for the v2 suggestions table, tools, and disposition flow."""

import pytest

from meta_assistant.storage import Storage


# --- helpers -----------------------------------------------------------------


def _seed_project(storage: Storage) -> dict:
    return storage.write(
        "project",
        {"name": "ship the assistant", "description": "v2 work"},
    )


def _valid_proposal(project_id: str, *, kind: str = "opportunity") -> dict:
    """A minimally-valid suggestion payload anchored to a project."""
    data = {
        "kind": kind,
        "content": "Talk to two existing users next week about their inbox use.",
        "rationale": "Inbox skill hasn't been validated against real usage yet.",
        "linked_evidence": {
            "internal_anchors": [
                {
                    "entity_type": "project",
                    "entity_id": project_id,
                    "why_anchored": "the inbox skill ships in this project",
                }
            ],
            "external_sources": [],
        },
    }
    if kind in {"opportunity", "risk"}:
        data["linked_evidence"]["external_sources"] = [
            {
                "url": "https://example.com/research",
                "claim_supported": "early-user interviews reveal hidden friction",
                "accessed_at": "2026-05-17T00:00:00Z",
            }
        ]
    return data


# --- write & validation ------------------------------------------------------


def test_write_suggestion_happy_path(storage: Storage):
    project = _seed_project(storage)
    proposal = storage.write("suggestion", _valid_proposal(project["id"]))
    assert proposal["id"]
    assert proposal["status"] == "pending"
    assert proposal["kind"] == "opportunity"
    assert proposal["linked_evidence"]["internal_anchors"][0]["entity_id"] == project["id"]
    assert proposal["linked_evidence"]["external_sources"][0]["url"].startswith("https://")


def test_write_rejects_zero_internal_anchors(storage: Storage):
    project = _seed_project(storage)
    data = _valid_proposal(project["id"])
    data["linked_evidence"]["internal_anchors"] = []
    with pytest.raises(ValueError, match="internal_anchors"):
        storage.write("suggestion", data)


def test_write_rejects_missing_linked_evidence(storage: Storage):
    data = {
        "kind": "opportunity",
        "content": "do a thing",
        "rationale": "because",
    }
    with pytest.raises(ValueError, match="linked_evidence"):
        storage.write("suggestion", data)


def test_opportunity_requires_external_sources(storage: Storage):
    project = _seed_project(storage)
    data = _valid_proposal(project["id"], kind="opportunity")
    data["linked_evidence"]["external_sources"] = []
    with pytest.raises(ValueError, match="external_sources"):
        storage.write("suggestion", data)


def test_risk_requires_external_sources(storage: Storage):
    project = _seed_project(storage)
    data = _valid_proposal(project["id"], kind="risk")
    data["linked_evidence"]["external_sources"] = []
    with pytest.raises(ValueError, match="external_sources"):
        storage.write("suggestion", data)


def test_blindspot_may_omit_external_sources(storage: Storage):
    project = _seed_project(storage)
    data = _valid_proposal(project["id"], kind="blindspot")
    # blindspot defaults to no external sources in _valid_proposal — good
    data["linked_evidence"]["external_sources"] = []
    proposal = storage.write("suggestion", data)
    assert proposal["kind"] == "blindspot"
    assert proposal["linked_evidence"]["external_sources"] == []


def test_pattern_may_omit_external_sources(storage: Storage):
    project = _seed_project(storage)
    data = _valid_proposal(project["id"], kind="pattern")
    data["linked_evidence"]["external_sources"] = []
    proposal = storage.write("suggestion", data)
    assert proposal["kind"] == "pattern"


def test_write_rejects_invalid_kind(storage: Storage):
    project = _seed_project(storage)
    data = _valid_proposal(project["id"])
    data["kind"] = "vibe"
    with pytest.raises(ValueError, match="kind"):
        storage.write("suggestion", data)


def test_write_rejects_malformed_anchor(storage: Storage):
    project = _seed_project(storage)
    data = _valid_proposal(project["id"])
    data["linked_evidence"]["internal_anchors"] = [{"entity_type": "project"}]
    with pytest.raises(ValueError, match="entity_id"):
        storage.write("suggestion", data)


# --- list_pending_suggestions ------------------------------------------------------


def test_list_pending_suggestions_filters(storage: Storage):
    project = _seed_project(storage)
    a = storage.write(
        "suggestion",
        {**_valid_proposal(project["id"]), "session_id": "s1"},
    )
    storage.write(
        "suggestion",
        {**_valid_proposal(project["id"], kind="blindspot"), "session_id": "s2"},
    )
    by_session = storage.list_pending_suggestions(session_id="s1")
    assert {p["id"] for p in by_session} == {a["id"]}
    by_kind = storage.list_pending_suggestions(kind="blindspot")
    assert all(p["kind"] == "blindspot" for p in by_kind)
    assert len(by_kind) == 1


def test_list_pending_suggestions_excludes_dispositioned(storage: Storage):
    project = _seed_project(storage)
    a = storage.write("suggestion", _valid_proposal(project["id"]))
    b = storage.write("suggestion", _valid_proposal(project["id"], kind="blindspot"))
    storage.disposition_suggestion(a["id"], "accepted", "looks good")
    pending = storage.list_pending_suggestions()
    assert {p["id"] for p in pending} == {b["id"]}


# --- disposition_suggestion --------------------------------------------------------


def test_disposition_suggestion_accepted(storage: Storage):
    project = _seed_project(storage)
    proposal = storage.write("suggestion", _valid_proposal(project["id"]))
    updated = storage.disposition_suggestion(proposal["id"], "accepted", "agree, will act this week")
    assert updated["status"] == "accepted"
    assert updated["disposition_reason"] == "agree, will act this week"
    assert updated["dispositioned_at"]


def test_disposition_suggestion_rejected_without_reason(storage: Storage):
    project = _seed_project(storage)
    proposal = storage.write("suggestion", _valid_proposal(project["id"]))
    updated = storage.disposition_suggestion(proposal["id"], "rejected")
    assert updated["status"] == "rejected"
    # reason is optional at storage layer; the inbox skill is responsible for prompting
    assert updated["disposition_reason"] is None


def test_disposition_suggestion_errors_on_non_pending(storage: Storage):
    project = _seed_project(storage)
    proposal = storage.write("suggestion", _valid_proposal(project["id"]))
    storage.disposition_suggestion(proposal["id"], "accepted", "yes")
    with pytest.raises(ValueError, match="not pending|cannot disposition"):
        storage.disposition_suggestion(proposal["id"], "rejected", "wait no")


def test_disposition_suggestion_errors_on_missing_id(storage: Storage):
    with pytest.raises(ValueError, match="not found"):
        storage.disposition_suggestion("does-not-exist", "accepted")


def test_disposition_suggestion_rejects_invalid_status(storage: Storage):
    project = _seed_project(storage)
    proposal = storage.write("suggestion", _valid_proposal(project["id"]))
    with pytest.raises(ValueError, match="status"):
        storage.disposition_suggestion(proposal["id"], "maybe-later")


# --- query("suggestion") --------------------------------------------------


def test_query_suggestions_deserializes_evidence(storage: Storage):
    project = _seed_project(storage)
    storage.write("suggestion", _valid_proposal(project["id"]))
    rows = storage.query("suggestion")
    assert len(rows) == 1
    assert isinstance(rows[0]["linked_evidence"], dict)
    assert rows[0]["linked_evidence"]["internal_anchors"][0]["entity_id"] == project["id"]


# --- update is not allowed for suggestions --------------------------------


def test_update_rejects_suggestion(storage: Storage):
    project = _seed_project(storage)
    proposal = storage.write("suggestion", _valid_proposal(project["id"]))
    with pytest.raises(ValueError, match="entity_type"):
        storage.update("suggestion", proposal["id"], {"status": "accepted"})


# --- end-to-end disposition flow --------------------------------------------


def test_e2e_research_to_disposition(tmp_path):
    """Write a proposal → list_pending_suggestions returns it → disposition_suggestion
    accepts it → query confirms status and reason. Mirrors the acceptance
    criterion for v2."""
    storage = Storage(tmp_path / "v2_e2e.db")
    project = storage.write(
        "project",
        {"name": "meta-assistant", "description": "the assistant itself"},
    )

    # opportunity_research skill writes a proposal anchored to the project
    proposal = storage.write(
        "suggestion",
        {
            "kind": "opportunity",
            "content": "Talk to two existing users about the inbox flow this week.",
            "rationale": "We have no real-usage signal on the inbox yet.",
            "linked_evidence": {
                "internal_anchors": [
                    {
                        "entity_type": "project",
                        "entity_id": project["id"],
                        "why_anchored": "the inbox is part of this project",
                    }
                ],
                "external_sources": [
                    {
                        "url": "https://example.com/user-research",
                        "claim_supported": "early-user interviews surface hidden friction",
                        "accessed_at": "2026-05-17T00:00:00Z",
                    }
                ],
            },
            "session_id": "research-run-1",
        },
    )

    # inbox skill loads pending proposals
    pending = storage.list_pending_suggestions(session_id="research-run-1")
    assert len(pending) == 1
    assert pending[0]["id"] == proposal["id"]

    # user says: accept, here's why
    accepted = storage.disposition_suggestion(
        proposal["id"], "accepted", "good catch, will book the calls"
    )
    assert accepted["status"] == "accepted"
    assert accepted["disposition_reason"] == "good catch, will book the calls"

    # confirm via query
    [row] = storage.query("suggestion", {"id": proposal["id"]})
    assert row["status"] == "accepted"
    assert row["disposition_reason"] == "good catch, will book the calls"
    assert row["dispositioned_at"]

    # it no longer shows up as pending
    assert storage.list_pending_suggestions() == []


# --- server wiring smoke -----------------------------------------------------


def test_server_registers_v2_tools_and_resources(tmp_path):
    from meta_assistant.server import build_server

    skills_dir = tmp_path / "skills"
    schemas_dir = tmp_path / "schemas"
    constitution = tmp_path / "constitution.md"
    skills_dir.mkdir()
    schemas_dir.mkdir()
    (skills_dir / "opportunity_research.md").write_text("# oppo")
    (skills_dir / "inbox.md").write_text("# inbox")
    (schemas_dir / "suggestions.md").write_text("# suggestions")
    constitution.write_text("hi")

    mcp, _ = build_server(
        db_path=str(tmp_path / "v2.db"),
        skills_dir=skills_dir,
        schemas_dir=schemas_dir,
        constitution_path=constitution,
    )

    tool_names = {t.name for t in mcp._tool_manager.list_tools()}
    assert {"state_disposition_suggestion", "state_list_pending_suggestions"} <= tool_names

    resource_uris = {str(r.uri) for r in mcp._resource_manager.list_resources()}
    assert "skill://opportunity_research" in resource_uris
    assert "skill://inbox" in resource_uris
    assert "schema://suggestions" in resource_uris


# --- v3.3 migration: idea_proposals → suggestions ---------------------------


def test_v3_3_migration_renames_table_and_preserves_rows(tmp_path):
    """Open a DB that has the old idea_proposals table and confirm Storage
    init renames it to suggestions without losing rows. Mirrors what
    happens to a real user's data on the first boot after this rename."""
    import sqlite3
    import json
    import uuid
    from datetime import datetime, timezone

    db_path = tmp_path / "old_style.db"

    # Build a DB that looks like pre-v3.3: idea_proposals table with rows,
    # no suggestions table.
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """CREATE TABLE idea_proposals (
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
            )"""
        )
        now = datetime.now(timezone.utc).isoformat()
        for i in range(3):
            conn.execute(
                "INSERT INTO idea_proposals (id, created_at, kind, content, "
                "rationale, linked_evidence, status) VALUES (?,?,?,?,?,?,?)",
                (
                    str(uuid.uuid4()), now, "opportunity",
                    f"legacy row {i}", "preserved from old schema",
                    json.dumps({
                        "internal_anchors": [{"entity_type": "project",
                                              "entity_id": "x",
                                              "why_anchored": "test"}],
                        "external_sources": [],
                    }),
                    "pending",
                ),
            )
        conn.commit()

    # Sanity: old table exists, new doesn't
    with sqlite3.connect(str(db_path)) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    assert "idea_proposals" in tables
    assert "suggestions" not in tables

    # Open via Storage — migration runs in __init__
    storage = Storage(db_path)

    # Old table gone, new table present, rows intact
    with sqlite3.connect(str(db_path)) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    assert "idea_proposals" not in tables
    assert "suggestions" in tables

    rows = storage.query("suggestion")
    assert len(rows) == 3
    assert {r["content"] for r in rows} == {f"legacy row {i}" for i in range(3)}
    # linked_evidence still deserializes correctly via the same path used
    # for fresh-write suggestions
    for r in rows:
        assert isinstance(r["linked_evidence"], dict)
        assert r["linked_evidence"]["internal_anchors"]


def test_v3_3_migration_is_idempotent(tmp_path):
    """Running Storage() twice on the same DB must not break anything —
    the migration is a no-op after first run."""
    db_path = tmp_path / "twice.db"
    s1 = Storage(db_path)
    s1.write("suggestion", {
        "kind": "blindspot",
        "content": "test",
        "rationale": "test",
        "linked_evidence": {
            "internal_anchors": [
                {"entity_type": "project", "entity_id": "x",
                 "why_anchored": "test"}
            ],
            "external_sources": [],
        },
    })
    # Re-open
    s2 = Storage(db_path)
    rows = s2.query("suggestion")
    assert len(rows) == 1


def test_v3_3_migration_updates_cross_reference_entity_types(tmp_path):
    """Migration should rewrite entity_type='idea_proposal' → 'suggestion'
    in entity_slu_links and source_links rows that referenced the old
    name. The current user has zero such rows, but the migration must
    handle the case for anyone who did."""
    import sqlite3
    import uuid
    from datetime import datetime, timezone

    db_path = tmp_path / "with_refs.db"

    # Build a partial old-style DB: idea_proposals (with the full pre-rename
    # schema, since v5's FTS5 backfill reads content/rationale after the
    # rename) plus an entity_slu_links and source_links row pointing at it
    # with the old entity_type string.
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript("""
            CREATE TABLE idea_proposals (
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
            CREATE TABLE entity_slu_links (
                id TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                life_unit_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE source_links (
                id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL
            );
        """)
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO entity_slu_links VALUES (?, 'idea_proposal', 'fake-id', 'fake-slu', ?)",
            (str(uuid.uuid4()), now),
        )
        conn.execute(
            "INSERT INTO source_links VALUES (?, 'fake-source', 'idea_proposal', 'fake-id')",
            (str(uuid.uuid4()),),
        )
        conn.commit()

    Storage(db_path)  # runs migration

    with sqlite3.connect(str(db_path)) as conn:
        link_types = [r[0] for r in conn.execute(
            "SELECT entity_type FROM entity_slu_links"
        )]
        source_types = [r[0] for r in conn.execute(
            "SELECT entity_type FROM source_links"
        )]
    assert link_types == ["suggestion"]
    assert source_types == ["suggestion"]
