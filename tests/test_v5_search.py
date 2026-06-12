"""Tests for v5: the FTS5 search index and the ``state_search`` tool.

Covers sync triggers for every indexed entity type (insert / update /
delete keep the index in step with source rows), the one-time backfill
migration path, query handling (phrase wrapping, advanced operators,
short/empty queries), filters (entity_types / since / limit), and the
two properties the brief calls out explicitly: BM25 ranking returns
higher-better scores, and diacritic-insensitive matching works
("Valéncia" finds "Valencia").
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from meta_assistant.storage import Storage


SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"
SCHEMAS_DIR = Path(__file__).resolve().parent.parent / "schemas"


# --- index is created + has the right shape --------------------------------


def test_search_index_table_and_triggers_exist(storage: Storage):
    """search_index plus three triggers per source table should be
    created during Storage init."""
    import sqlite3
    with sqlite3.connect(storage.db_path) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        triggers = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        )}
    assert "search_index" in tables
    # Spot-check: every indexed table has three search-related triggers
    for table in ("projects", "decisions", "commitments", "stakeholders",
                  "interests", "capabilities", "resources", "suggestions",
                  "slu_observations", "sources", "life_units",
                  "great_life_dimensions", "slu_assessments"):
        for kind in ("insert", "update", "delete"):
            name = f"{table}_search_{kind}"
            assert name in triggers, f"missing trigger {name}"


def test_search_index_seeded_from_life_units_on_fresh_db(storage: Storage):
    """The 16 SLUs are seeded into life_units in __init__, so the FTS5
    index should already hold matching rows by the time __init__ returns."""
    rows = storage.search("Physical health")
    assert any(r["entity_type"] == "life_unit" for r in rows)


# --- sync triggers: insert / update / delete -------------------------------


def test_insert_then_search_finds(storage: Storage):
    storage.write(
        "project",
        {"name": "Move to Valencia", "description": "climate and cost"},
    )
    hits = storage.search("Valencia")
    assert any(r["title"] == "Move to Valencia" for r in hits)


def test_update_reindexes(storage: Storage):
    p = storage.write(
        "project",
        {"name": "P", "description": "Lisbon plans"},
    )
    # Pre-update: old word matches, new word doesn't
    assert storage.search("Lisbon")
    assert storage.search("Andalusia") == []
    storage.update("project", p["id"], {"description": "Andalusia plans"})
    # Post-update: new word matches, old word doesn't
    assert any(r["entity_id"] == p["id"] for r in storage.search("Andalusia"))
    assert storage.search("Lisbon") == []


def test_delete_removes_from_index(storage: Storage):
    p = storage.write(
        "project",
        {"name": "Ephemeral", "description": "Solaranian frost"},
    )
    assert storage.search("Solaranian")
    # Storage doesn't expose a generic delete on projects (entities have
    # status transitions instead), but the trigger should still fire if
    # someone deletes a project via raw SQL. Verify that the FTS index
    # follows.
    import sqlite3
    with sqlite3.connect(storage.db_path) as conn:
        conn.execute("DELETE FROM projects WHERE id = ?", (p["id"],))
    assert storage.search("Solaranian") == []


# --- backfill from existing rows -------------------------------------------


def test_backfill_indexes_existing_rows(tmp_path):
    """When Storage opens a DB that already has source rows but no
    search_index, the migration backfills every indexed table."""
    import sqlite3
    db = tmp_path / "preexisting.db"

    # Create a partial DB matching the v4 schema, but WITHOUT search_index.
    # We seed rows directly so we can verify backfill picks them up.
    with sqlite3.connect(str(db)) as conn:
        conn.executescript("""
            CREATE TABLE projects (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL, last_touched TEXT NOT NULL,
                snooze_until TEXT, kill_criteria TEXT, revive_criteria TEXT
            );
        """)
        conn.execute(
            "INSERT INTO projects (id, name, description, status, "
            "created_at, last_touched) VALUES "
            "('p1', 'Old Project', 'preexisting Bratislavian content', "
            "'active', '2025-01-01T00:00:00Z', '2025-01-01T00:00:00Z')"
        )
        conn.commit()

    storage = Storage(db)  # SCHEMA runs (creates search_index + triggers),
                           # then _seed_search_index backfills
    hits = storage.search("Bratislavian")
    assert any(r["entity_id"] == "p1" for r in hits)


def test_backfill_is_idempotent(tmp_path):
    """Opening the same DB twice must not double-insert into the index."""
    db = tmp_path / "twice.db"
    s1 = Storage(db)
    s1.write("project", {"name": "Quarrian", "description": "x"})
    initial = len(s1.search("Quarrian"))
    # Reopen
    s2 = Storage(db)
    again = len(s2.search("Quarrian"))
    assert initial == again == 1


# --- query handling: phrase wrapping, operators, edge cases ----------------


def test_empty_query_returns_empty(storage: Storage):
    assert storage.search("") == []
    assert storage.search("   ") == []
    assert storage.search("a") == []  # 1-char too short


def test_phrase_wrapping_prevents_fts5_syntax_surprise(storage: Storage):
    """A user typing "Surf Bar Valencia" should match the phrase, not
    treat "Bar" as an FTS5 column operator or anything weird. Punctuation
    in the query shouldn't crash."""
    storage.write("project", {"name": "Surf Bar Valencia",
                              "description": "the joint venture"})
    # The phrase-wrapped search finds the project
    assert storage.search("Surf Bar Valencia")
    # Stray punctuation doesn't crash
    assert storage.search("Valencia.") == [] or True  # may or may not match — must not error
    storage.search("(foo) [bar]")  # must not raise


def test_advanced_operators_pass_through(storage: Storage):
    storage.write("project", {"name": "Alpha", "description": "first"})
    storage.write("project", {"name": "Beta", "description": "second"})
    # "Alpha OR Beta" should hit both
    hits = storage.search("Alpha OR Beta")
    names = {r["title"] for r in hits}
    assert names == {"Alpha", "Beta"}


def test_prefix_search_passes_through(storage: Storage):
    storage.write("project", {"name": "Valenzia experiment", "description": "x"})
    # Prefix * should match
    hits = storage.search("Valenz*")
    assert any(r["title"] == "Valenzia experiment" for r in hits)


def test_malformed_query_returns_empty_not_error(storage: Storage):
    """FTS5 raises OperationalError on some malformed syntax. Wrap as
    phrase OR pass-through, the user shouldn't see an exception."""
    # An unbalanced quote in pass-through mode would crash FTS5 — but
    # since this string contains no AND/OR/NOT/* it gets phrase-wrapped
    # and double-quotes inside are escaped. Must not raise.
    result = storage.search('he said "hello')
    assert isinstance(result, list)


# --- ranking + snippets ----------------------------------------------------


def test_bm25_higher_is_better(storage: Storage):
    """A row that mentions the term more often / more centrally should
    rank above one that mentions it incidentally."""
    storage.write("project", {
        "name": "Sailing project",
        "description": "Sailing across the Mediterranean. Sailing weekly. Sailing forever.",
    })
    storage.write("project", {
        "name": "Mixed project",
        "description": "Mostly painting. Briefly mentioned sailing once.",
    })
    hits = storage.search("sailing")
    titles_ordered = [h["title"] for h in hits]
    assert titles_ordered[0] == "Sailing project"
    # All scores positive and the first is at least as high as the second
    assert hits[0]["score"] >= hits[-1]["score"]
    assert all(h["score"] > 0 for h in hits)


def test_snippet_contains_mark_tags(storage: Storage):
    storage.write("project", {
        "name": "Coastal videography",
        "description": "Long-form film about coastal Valencia and the surrounding region.",
    })
    [hit] = storage.search("Valencia", entity_types=["project"])
    assert "<mark>" in hit["snippet"] and "</mark>" in hit["snippet"]
    assert "Valencia" in hit["snippet"]


# --- diacritics ------------------------------------------------------------


def test_diacritic_insensitive_match(storage: Storage):
    storage.write("project", {
        "name": "Move to Valéncia",
        "description": "spelled with the diacritic in the title",
    })
    # ASCII query finds diacritic content
    assert any(r["title"] == "Move to Valéncia"
               for r in storage.search("Valencia"))
    # And vice versa: diacritic query finds ASCII content
    storage.write("project", {
        "name": "Stay in Valencia",
        "description": "plain ASCII",
    })
    by_diacritic = storage.search("Valéncia")
    titles = {r["title"] for r in by_diacritic}
    assert "Move to Valéncia" in titles
    assert "Stay in Valencia" in titles


def test_case_insensitive_match(storage: Storage):
    storage.write("project", {"name": "MIXED case PROJECT", "description": "x"})
    # Lowercase query matches uppercase title
    assert any(r["title"] == "MIXED case PROJECT" for r in storage.search("mixed"))


# --- filters --------------------------------------------------------------


def test_filter_by_entity_types(storage: Storage):
    storage.write("project", {"name": "Sailing", "description": "boat work"})
    storage.write("interest", {"topic": "Sailing", "description": "watching others sail"})
    only_projects = storage.search("Sailing", entity_types=["project"])
    assert all(r["entity_type"] == "project" for r in only_projects)
    assert len(only_projects) == 1


def test_filter_rejects_unindexed_entity_type(storage: Storage):
    with pytest.raises(ValueError, match="not searchable"):
        storage.search("anything", entity_types=["widget"])


def test_filter_by_since(storage: Storage):
    """`since` filter restricts to entities created at-or-after the
    timestamp."""
    # Project from "way back": we manipulate created_at via raw SQL after
    # the write, since the create method always uses now.
    import sqlite3
    p_old = storage.write("project", {"name": "Quanzelite", "description": "x"})
    p_new = storage.write("project", {"name": "Other Quanzelite", "description": "x"})
    with sqlite3.connect(storage.db_path) as conn:
        old_iso = "2020-01-01T00:00:00+00:00"
        conn.execute(
            "UPDATE search_index SET created_at = ? "
            "WHERE entity_id = ?",
            (old_iso, p_old["id"]),
        )

    fresh = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    only_recent = storage.search("Quanzelite", since=fresh)
    ids = {r["entity_id"] for r in only_recent}
    assert p_new["id"] in ids
    assert p_old["id"] not in ids


def test_limit_caps_and_defaults(storage: Storage):
    for i in range(25):
        storage.write("project", {"name": f"Pessolium {i}",
                                  "description": "pessolium plans"})
    default_limit = storage.search("pessolium")
    assert len(default_limit) == 20  # default
    explicit_small = storage.search("pessolium", limit=5)
    assert len(explicit_small) == 5
    # Limit caps at 100 even if a larger value is requested
    explicit_huge = storage.search("pessolium", limit=10_000)
    assert len(explicit_huge) <= 100


def test_limit_invalid_falls_back_to_default(storage: Storage):
    for i in range(3):
        storage.write("project", {"name": f"Karenov {i}", "description": "x"})
    # 0 and negative limits get coerced to default
    assert len(storage.search("Karenov", limit=0)) == 3
    assert len(storage.search("Karenov", limit=-5)) == 3


# --- coverage across all entity types --------------------------------------


def test_each_indexed_entity_type_is_searchable(storage: Storage):
    """One write per entity type with a unique word, then search for that
    word and confirm it lands in the right entity_type bucket."""
    # Use synthetic words so they don't collide with seeded SLU data
    # ('zlx' prefix is unlikely to appear anywhere else).
    cases = [
        ("project",     lambda: storage.write("project",
                          {"name": "zlxa Project", "description": "x"})),
        ("decision",    lambda: storage.write("decision",
                          {"what": "zlxb decision", "rationale": "x"})),
        ("commitment",  lambda: storage.write("commitment",
                          {"what": "zlxc commitment", "to_whom": "self"})),
        ("stakeholder", lambda: storage.write("stakeholder",
                          {"name": "zlxd person"})),
        ("interest",    lambda: storage.write("interest",
                          {"topic": "zlxe interest"})),
        ("capability",  lambda: storage.write("capability",
                          {"name": "zlxf capability", "category": "skill"})),
        ("resource",    lambda: storage.write("resource",
                          {"name": "zlxg resource", "category": "money",
                           "unit": "EUR", "current_quantity": 1})),
    ]
    for entity_type, writer in cases:
        entity = writer()
        # Use the unique zlx-word as the search query
        word = "zlx" + entity_type[0]  # zlxp, zlxd, etc. — won't all match exact id-keys
        # Actually let me search by the word in the title, deterministically
        # constructed above (zlxa, zlxb, ...).
        word_map = {"project": "zlxa", "decision": "zlxb",
                    "commitment": "zlxc", "stakeholder": "zlxd",
                    "interest": "zlxe", "capability": "zlxf",
                    "resource": "zlxg"}
        hits = storage.search(word_map[entity_type])
        assert any(
            r["entity_id"] == entity["id"] and r["entity_type"] == entity_type
            for r in hits
        ), f"{entity_type} write did not appear in search results"


# --- server wiring smoke ---------------------------------------------------


def test_server_registers_search_tool(tmp_path):
    from meta_assistant.server import build_server

    skills_dir = tmp_path / "skills"
    schemas_dir = tmp_path / "schemas"
    guides_dir = tmp_path / "guides"
    constitution = tmp_path / "constitution.md"
    for d in (skills_dir, schemas_dir, guides_dir):
        d.mkdir()
    constitution.write_text("hi")

    mcp, _ = build_server(
        db_path=str(tmp_path / "v5.db"),
        skills_dir=skills_dir,
        schemas_dir=schemas_dir,
        guides_dir=guides_dir,
        constitution_path=constitution,
    )
    tool_names = {t.name for t in mcp._tool_manager.list_tools()}
    assert "state_search" in tool_names


# --- find skill ships, with actionable description -------------------------


def test_find_skill_ships_with_actionable_frontmatter():
    from meta_assistant.server import _parse_skill_description
    path = SKILLS_DIR / "find.md"
    assert path.exists(), "skills/find.md should ship in v5"
    desc = _parse_skill_description(path)
    assert desc is not None
    assert "Triggers" in desc
    assert "Not for" in desc


def test_find_skill_references_constitution():
    body = (SKILLS_DIR / "find.md").read_text()
    assert "constitution://main" in body


def test_search_index_schema_doc_ships():
    assert (SCHEMAS_DIR / "search_index.md").exists()


# --- capture / opportunity_research updates ship ---------------------------


def test_capture_skill_has_duplicate_detection_step():
    body = (SKILLS_DIR / "capture.md").read_text()
    assert "state_search" in body  # capture should reference search now
    # Step 2a is the new duplicate-detection block
    assert "2a." in body or "duplicate" in body.lower()


def test_opportunity_research_uses_search_for_large_data():
    body = (SKILLS_DIR / "opportunity_research.md").read_text()
    assert "state_search" in body or "state.search" in body
