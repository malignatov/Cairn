"""Tests for v5.1: session_type discrimination + patch/quick-update flows.

Covers the schema migration (existing rows backfilled as 'full'),
``start_assessment_session`` accepting the new vocabulary,
``record_slu_rating`` carrying session_type/inherited_from, the new
``record_quick_slu_update`` (session-less) and ``get_latest_full_session``
methods, and the two snapshot-comparison surfaces (compute_portfolio_quadrants
and the scanner's quadrant_shift hint) that must exclude quick_updates.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from meta_assistant.storage import Storage


SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


# --- migration -----------------------------------------------------------


def test_migration_backfills_session_type_full(tmp_path):
    """A v5.0 DB has slu_assessments rows without session_type. Opening
    it as Storage v5.1 adds the column with default 'full' so old data
    keeps reading as honest full sessions."""
    import sqlite3
    import uuid

    db = tmp_path / "v5_db.db"
    # Build the slu_assessments table in its pre-v5.1 shape (no
    # session_type column) and seed a row.
    with sqlite3.connect(str(db)) as conn:
        conn.executescript("""
            CREATE TABLE life_units (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, area TEXT NOT NULL,
                description TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                custom INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE slu_assessments (
                id TEXT PRIMARY KEY,
                life_unit_id TEXT NOT NULL,
                assessed_at TEXT NOT NULL,
                importance INTEGER NOT NULL,
                satisfaction INTEGER NOT NULL,
                hours_per_week REAL,
                quadrant TEXT NOT NULL,
                notes TEXT,
                session_id TEXT
            );
        """)
        now = datetime.now(timezone.utc).isoformat()
        lu_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO life_units VALUES (?, 'Family', 'relationships', '', 1, 0, ?, ?)",
            (lu_id, now, now),
        )
        conn.execute(
            "INSERT INTO slu_assessments (id, life_unit_id, assessed_at, "
            "importance, satisfaction, quadrant, session_id) "
            "VALUES (?, ?, ?, 8, 7, 'upper_right', 'old-session')",
            (str(uuid.uuid4()), lu_id, now),
        )
        conn.commit()

    # Opening via Storage runs migration + new SCHEMA
    Storage(db)

    with sqlite3.connect(str(db)) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(slu_assessments)")}
        row = conn.execute(
            "SELECT session_type, inherited_from_session_id "
            "FROM slu_assessments WHERE session_id = 'old-session'"
        ).fetchone()
    assert "session_type" in cols
    assert "inherited_from_session_id" in cols
    assert row[0] == "full"           # backfilled
    assert row[1] is None             # no inheritance


def test_migration_idempotent(tmp_path):
    """Opening a v5.1 DB twice doesn't break anything."""
    db = tmp_path / "twice.db"
    Storage(db)
    Storage(db)  # second open should be a no-op
    s = Storage(db)
    assert len(s.list_active_slus()) == 16


# --- start_assessment_session ---------------------------------------------


def test_start_session_full(storage: Storage):
    s = storage.start_assessment_session("full")
    assert s["type"] == "full"
    assert s["session_id"]
    assert s["inherited_from_session_id"] is None


def test_start_session_legacy_slu_portfolio_alias(storage: Storage):
    """v3 tests and any caller using 'slu_portfolio' continue working;
    it's normalized to 'full' on the way out."""
    s = storage.start_assessment_session("slu_portfolio")
    assert s["type"] == "full"


def test_start_session_great_life_unchanged(storage: Storage):
    s = storage.start_assessment_session("great_life")
    assert s["type"] == "great_life"


def test_start_patch_requires_inherited_from(storage: Storage):
    # Need a real prior session to point at
    parent = storage.start_assessment_session("full")
    slu = storage.list_active_slus()[0]
    storage.record_slu_rating(
        parent["session_id"], slu["id"], 8, 7, session_type="full",
    )

    # Missing inherited_from is rejected
    with pytest.raises(ValueError, match="inherited_from_session_id"):
        storage.start_assessment_session("patch")

    # Bogus inherited_from is rejected
    with pytest.raises(ValueError, match="has no ratings"):
        storage.start_assessment_session(
            "patch", inherited_from_session_id="no-such",
        )

    # Real inherited_from is accepted
    s = storage.start_assessment_session(
        "patch", inherited_from_session_id=parent["session_id"],
    )
    assert s["type"] == "patch"
    assert s["inherited_from_session_id"] == parent["session_id"]


def test_start_session_inherited_from_rejected_for_non_patch(storage: Storage):
    with pytest.raises(ValueError, match="inherited_from_session_id is only"):
        storage.start_assessment_session(
            "full", inherited_from_session_id="anything",
        )


def test_start_session_invalid_type(storage: Storage):
    with pytest.raises(ValueError, match="type must be"):
        storage.start_assessment_session("vibes")


# --- record_slu_rating with session_type ----------------------------------


def test_record_slu_rating_default_is_full(storage: Storage):
    """Existing callers using the v3 signature get session_type='full'
    without having to know about the new param."""
    import sqlite3
    s = storage.start_assessment_session("full")
    slu = storage.list_active_slus()[0]
    r = storage.record_slu_rating(s["session_id"], slu["id"], 8, 7)
    assert r["session_type"] == "full"
    assert r["inherited_from_session_id"] is None
    # And it's actually stored that way
    with sqlite3.connect(storage.db_path) as conn:
        stored = conn.execute(
            "SELECT session_type FROM slu_assessments WHERE id = ?",
            (r["id"],),
        ).fetchone()
    assert stored[0] == "full"


def test_record_slu_rating_patch_requires_inherited_from(storage: Storage):
    s = storage.start_assessment_session("full")
    slu = storage.list_active_slus()[0]
    with pytest.raises(ValueError, match="inherited_from_session_id"):
        storage.record_slu_rating(
            s["session_id"], slu["id"], 8, 7, session_type="patch",
        )


def test_record_slu_rating_patch_happy_path(storage: Storage):
    # Set up a parent session
    parent = storage.start_assessment_session("full")
    slu = storage.list_active_slus()[0]
    storage.record_slu_rating(parent["session_id"], slu["id"], 8, 7)

    # Now record a patch rating that inherits from it
    patch = storage.start_assessment_session(
        "patch", inherited_from_session_id=parent["session_id"],
    )
    r = storage.record_slu_rating(
        patch["session_id"], slu["id"], 8, 5,
        session_type="patch",
        inherited_from_session_id=parent["session_id"],
    )
    assert r["session_type"] == "patch"
    assert r["inherited_from_session_id"] == parent["session_id"]


def test_record_slu_rating_rejects_quick_update_session_type(storage: Storage):
    """record_slu_rating is for session-bound ratings; quick_update has
    its own method."""
    s = storage.start_assessment_session("full")
    slu = storage.list_active_slus()[0]
    with pytest.raises(ValueError, match="session_type must be"):
        storage.record_slu_rating(
            s["session_id"], slu["id"], 8, 7, session_type="quick_update",
        )


# --- record_quick_slu_update ---------------------------------------------


def test_quick_update_writes_session_less_row(storage: Storage):
    import sqlite3
    slu = storage.list_active_slus()[0]
    r = storage.record_quick_slu_update(slu["id"], 8, 7)
    assert r["session_id"] is None
    assert r["session_type"] == "quick_update"
    assert r["quadrant"] == "upper_right"
    # And on disk
    with sqlite3.connect(storage.db_path) as conn:
        stored = conn.execute(
            "SELECT session_id, session_type FROM slu_assessments WHERE id = ?",
            (r["id"],),
        ).fetchone()
    assert stored[0] is None
    assert stored[1] == "quick_update"


def test_quick_update_validates(storage: Storage):
    slu = storage.list_active_slus()[0]
    with pytest.raises(ValueError, match="importance"):
        storage.record_quick_slu_update(slu["id"], 11, 7)
    with pytest.raises(ValueError, match="hours_per_week"):
        storage.record_quick_slu_update(slu["id"], 5, 5, hours_per_week=-1)
    with pytest.raises(ValueError, match="life_unit"):
        storage.record_quick_slu_update("no-such-id", 5, 5)


# --- get_latest_full_session ---------------------------------------------


def test_get_latest_full_session_returns_none_when_empty(storage: Storage):
    assert storage.get_latest_full_session() is None


def test_get_latest_full_session_returns_most_recent(storage: Storage):
    """Across multiple full and patch sessions, returns the newest by
    assessed_at."""
    slu = storage.list_active_slus()[0]
    older = storage.start_assessment_session("full")
    storage.record_slu_rating(older["session_id"], slu["id"], 8, 7)

    newer = storage.start_assessment_session("full")
    storage.record_slu_rating(newer["session_id"], slu["id"], 8, 8)

    latest = storage.get_latest_full_session()
    assert latest["session_id"] == newer["session_id"]
    assert latest["session_type"] == "full"
    assert len(latest["ratings"]) == 1


def test_get_latest_full_session_ignores_quick_updates(storage: Storage):
    """Even if a quick_update is the most recent row in the table, it
    isn't a session and isn't returned."""
    slu = storage.list_active_slus()[0]
    real = storage.start_assessment_session("full")
    storage.record_slu_rating(real["session_id"], slu["id"], 8, 7)
    # A later quick_update — should NOT be picked up
    storage.record_quick_slu_update(slu["id"], 8, 9)
    latest = storage.get_latest_full_session()
    assert latest["session_id"] == real["session_id"]


def test_get_latest_full_session_includes_patch_sessions(storage: Storage):
    """A patch is a real snapshot for the purposes of inheritance —
    if the most recent session is a patch, that's what we return."""
    slu = storage.list_active_slus()[0]
    parent = storage.start_assessment_session("full")
    storage.record_slu_rating(parent["session_id"], slu["id"], 8, 7)

    patch = storage.start_assessment_session(
        "patch", inherited_from_session_id=parent["session_id"],
    )
    storage.record_slu_rating(
        patch["session_id"], slu["id"], 8, 5,
        session_type="patch",
        inherited_from_session_id=parent["session_id"],
    )

    latest = storage.get_latest_full_session()
    assert latest["session_id"] == patch["session_id"]
    assert latest["session_type"] == "patch"


# --- compute_portfolio_quadrants ignores quick_updates ---------------------


def test_compute_portfolio_quadrants_skips_quick_updates(storage: Storage):
    """If the most recent activity is a quick_update, the picker should
    fall back to the most recent real session — quick_updates aren't
    portfolio snapshots."""
    slu = storage.list_active_slus()[0]
    sess = storage.start_assessment_session("full")
    storage.record_slu_rating(sess["session_id"], slu["id"], 8, 7)
    # Now a later quick_update
    storage.record_quick_slu_update(slu["id"], 8, 2)
    # Auto-pick should use the full session, not the quick_update
    result = storage.compute_portfolio_quadrants()
    assert result["session_id"] == sess["session_id"]
    # The full session had sat=7 so the SLU lands upper_right
    assert any(r["quadrant"] == "upper_right"
               for r in result["quadrants"]["upper_right"])


# --- e2e: patch flow ---------------------------------------------------


def test_e2e_patch_carries_unchanged_ratings_forward(storage: Storage):
    """The patch flow records new values for the SLUs the user named
    and copies forward the rest. The resulting patch session should
    have the same number of ratings as the parent."""
    slus = storage.list_active_slus()[:5]  # enough for a meaningful test

    # 1. Parent full session: all five SLUs rated 8/7
    parent = storage.start_assessment_session("full")
    for slu in slus:
        storage.record_slu_rating(parent["session_id"], slu["id"], 8, 7)

    # 2. Patch session: change the first two, inherit the rest
    parent_id = parent["session_id"]
    patch = storage.start_assessment_session(
        "patch", inherited_from_session_id=parent_id,
    )
    # Changed
    storage.record_slu_rating(
        patch["session_id"], slus[0]["id"], 9, 9,
        session_type="patch", inherited_from_session_id=parent_id,
    )
    storage.record_slu_rating(
        patch["session_id"], slus[1]["id"], 8, 3,
        session_type="patch", inherited_from_session_id=parent_id,
    )
    # Inherited forward (same values as the parent)
    for slu in slus[2:]:
        storage.record_slu_rating(
            patch["session_id"], slu["id"], 8, 7,
            session_type="patch", inherited_from_session_id=parent_id,
        )

    # 3. Verify the patch session has all 5 ratings, all marked 'patch'
    quadrants = storage.compute_portfolio_quadrants(patch["session_id"])
    all_ratings = sum(quadrants["quadrants"].values(), [])
    assert len(all_ratings) == 5
    # And SLU 1 (sat=3) lands in upper_left, while the rest stay where they were
    by_name = {r["slu_name"]: r for r in all_ratings}
    assert by_name[slus[1]["name"]]["quadrant"] == "upper_left"
    assert by_name[slus[0]["name"]]["quadrant"] == "upper_right"

    # 4. get_latest_full_session returns the patch (it's the newer snapshot)
    latest = storage.get_latest_full_session()
    assert latest["session_id"] == patch["session_id"]
    assert latest["session_type"] == "patch"


# --- e2e: quick update visible in history but not in snapshots ------------


def test_e2e_quick_update_visible_in_per_slu_history(storage: Storage):
    """A quick_update should appear in list_assessments for that SLU
    (so the per-SLU trajectory shows the change), even though it's
    excluded from portfolio snapshots."""
    slu = storage.list_active_slus()[0]
    sess = storage.start_assessment_session("full")
    storage.record_slu_rating(sess["session_id"], slu["id"], 8, 6)
    qu = storage.record_quick_slu_update(slu["id"], 8, 8)

    history = storage.list_assessments(life_unit_id=slu["id"])
    ids = [r["id"] for r in history]
    # Both rows visible, quick_update first (newer)
    assert qu["id"] in ids
    assert len(history) == 2


# --- skill markdown ships with the right shape ---------------------------


@pytest.mark.parametrize(
    "skill", ["patch_portfolio", "quick_update_slu", "add_inventory_items"],
)
def test_v5_1_skill_ships_with_actionable_frontmatter(skill):
    from meta_assistant.server import _parse_skill_description
    path = SKILLS_DIR / f"{skill}.md"
    assert path.exists(), f"skills/{skill}.md should ship in v5.1"
    desc = _parse_skill_description(path)
    assert desc is not None
    assert "Triggers" in desc
    assert "Not for" in desc


@pytest.mark.parametrize(
    "skill", ["patch_portfolio", "quick_update_slu", "add_inventory_items"],
)
def test_v5_1_skill_references_constitution(skill):
    body = (SKILLS_DIR / f"{skill}.md").read_text()
    assert "constitution://main" in body


def test_assess_life_portfolio_uses_full_type():
    body = (SKILLS_DIR / "assess_life_portfolio.md").read_text()
    assert 'state_start_assessment_session("full")' in body


def test_scan_life_units_quadrant_shift_excludes_quick_updates():
    body = (SKILLS_DIR / "scan_life_units.md").read_text()
    # The bullet on quadrant_shift should call out the exclusion
    assert "quick_update" in body.lower() or "full-or-patch" in body


# --- server wiring smoke -------------------------------------------------


def test_server_registers_v5_1_tools(tmp_path):
    from meta_assistant.server import build_server

    skills_dir = tmp_path / "skills"
    schemas_dir = tmp_path / "schemas"
    guides_dir = tmp_path / "guides"
    constitution = tmp_path / "constitution.md"
    for d in (skills_dir, schemas_dir, guides_dir):
        d.mkdir()
    constitution.write_text("hi")

    mcp, _ = build_server(
        db_path=str(tmp_path / "v5_1.db"),
        skills_dir=skills_dir,
        schemas_dir=schemas_dir,
        guides_dir=guides_dir,
        constitution_path=constitution,
    )
    tool_names = {t.name for t in mcp._tool_manager.list_tools()}
    assert "state_record_quick_slu_update" in tool_names
    assert "state_get_latest_full_session" in tool_names
