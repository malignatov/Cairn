"""v6.2 migration: the capture-flow `proposals` table → `drafts`.

Mirrors what happens to a real user's data on first boot after the rename:
the table is renamed in place (rows preserved), and any source_links that
pointed at a proposal as a link target have their entity_type rewritten
from 'proposal' to 'draft'. Also covers the fresh-DB case and a functional
round-trip through the renamed storage methods.
"""

import json
import sqlite3
import uuid
from datetime import datetime, timezone

from meta_assistant.storage import Storage


def _build_pre_v6_2_db(db_path):
    """Construct a DB shaped like pre-v6.2: a `proposals` table with rows
    and a `source_links` row whose entity_type is the old 'proposal'."""
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """CREATE TABLE proposals (
                id TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                data TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                committed_at TEXT,
                rejection_reason TEXT,
                session_id TEXT,
                committed_entity_id TEXT
            )"""
        )
        conn.execute(
            """CREATE TABLE source_links (
                id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE sources (
                id TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            "INSERT INTO sources (id, source_type, content, created_at) "
            "VALUES (?,?,?,?)",
            ("src-1", "chat", "user: yes, capture that", now),
        )
        prop_ids = []
        for i in range(3):
            pid = str(uuid.uuid4())
            prop_ids.append(pid)
            conn.execute(
                "INSERT INTO proposals (id, entity_type, data, status, "
                "created_at, session_id) VALUES (?,?,?,?,?,?)",
                (
                    pid, "decision",
                    json.dumps({"what": f"legacy draft {i}",
                                "rationale": "preserved from old schema"}),
                    "pending", now, "cap-legacy",
                ),
            )
        # a source attached to the first proposal, using the OLD target_type
        conn.execute(
            "INSERT INTO source_links (id, source_id, entity_type, entity_id) "
            "VALUES (?,?,?,?)",
            (str(uuid.uuid4()), "src-1", "proposal", prop_ids[0]),
        )
        conn.commit()
    return prop_ids


def _tables(db_path):
    with sqlite3.connect(str(db_path)) as conn:
        return {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}


def test_v6_2_migration_renames_table_and_preserves_rows(tmp_path):
    db_path = tmp_path / "old_style.db"
    _build_pre_v6_2_db(db_path)

    # Sanity: old table exists, new doesn't
    before = _tables(db_path)
    assert "proposals" in before
    assert "drafts" not in before

    # Open via Storage — migration runs in __init__
    storage = Storage(db_path)

    after = _tables(db_path)
    assert "proposals" not in after
    assert "drafts" in after

    # Rows intact and readable through the renamed method
    rows = storage.list_drafts(session_id="cap-legacy")
    assert len(rows) == 3
    assert {r["data"]["what"] for r in rows} == {
        f"legacy draft {i}" for i in range(3)
    }
    for r in rows:
        assert r["status"] == "pending"
        assert r["entity_type"] == "decision"


def test_v6_2_migration_rewrites_source_link_target_type(tmp_path):
    db_path = tmp_path / "links.db"
    prop_ids = _build_pre_v6_2_db(db_path)

    storage = Storage(db_path)

    # The old 'proposal' target_type is now 'draft'
    with sqlite3.connect(str(db_path)) as conn:
        types = {r[0] for r in conn.execute(
            "SELECT DISTINCT entity_type FROM source_links"
        )}
    assert types == {"draft"}

    # And it's reachable through the public accessor under the new name
    found = storage.sources_for("draft", prop_ids[0])
    assert len(found) == 1
    assert storage.sources_for("proposal", prop_ids[0]) == []


def test_v6_2_migration_is_idempotent(tmp_path):
    """Running Storage() twice must not break — the migration is a no-op
    after the first run, and a fresh DB never had a proposals table."""
    db_path = tmp_path / "twice.db"
    _build_pre_v6_2_db(db_path)
    Storage(db_path)              # first open: migrates
    storage = Storage(db_path)    # second open: no-op

    assert "proposals" not in _tables(db_path)
    assert "drafts" in _tables(db_path)
    assert len(storage.list_drafts(session_id="cap-legacy")) == 3


def test_v6_2_fresh_db_creates_drafts_not_proposals(tmp_path):
    """A brand-new DB is created with `drafts` directly; `proposals` never
    appears."""
    storage = Storage(tmp_path / "fresh.db")
    tables = _tables(tmp_path / "fresh.db")
    assert "drafts" in tables
    assert "proposals" not in tables
    # full round-trip through the renamed methods
    d = storage.create_draft(
        "decision",
        {"what": "ship v6.2", "rationale": "ends the naming collision"},
        session_id="s1",
    )
    assert storage.list_drafts(status="pending")[0]["id"] == d["id"]
    entity = storage.commit_draft(d["id"])
    assert entity["id"]
    committed = storage.list_drafts(session_id="s1")[0]
    assert committed["status"] == "committed"
    assert committed["committed_entity_id"] == entity["id"]
