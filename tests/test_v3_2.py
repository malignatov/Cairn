"""Tests for v3.2: scanner prerequisites + audit-driven hardening.

Covers the two new read tools (`list_perma_ratings`, `since` filter on
`list_entity_slu_links`), the constitution-cue presence in every skill,
and the server-side surfacing of frontmatter descriptions in the MCP
resource list.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from meta_assistant.storage import Storage


SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


# --- list_perma_ratings -----------------------------------------------------


def test_list_perma_ratings_empty(storage: Storage):
    assert storage.list_perma_ratings() == []


def test_list_perma_ratings_happy_path(storage: Storage):
    s = storage.start_assessment_session("great_life")["session_id"]
    for dim in ("positive_emotions", "engagement", "meaning"):
        storage.record_perma_rating(s, dim, importance=8, satisfaction=5)
    rows = storage.list_perma_ratings()
    assert len(rows) == 3
    assert {r["dimension"] for r in rows} == {
        "positive_emotions", "engagement", "meaning",
    }


def test_list_perma_ratings_filter_by_dimension(storage: Storage):
    s = storage.start_assessment_session("great_life")["session_id"]
    storage.record_perma_rating(s, "meaning", 9, 4)
    storage.record_perma_rating(s, "vitality", 7, 6)
    only = storage.list_perma_ratings(dimension="meaning")
    assert len(only) == 1 and only[0]["dimension"] == "meaning"


def test_list_perma_ratings_filter_by_session(storage: Storage):
    a = storage.start_assessment_session("great_life")["session_id"]
    b = storage.start_assessment_session("great_life")["session_id"]
    storage.record_perma_rating(a, "meaning", 8, 5)
    storage.record_perma_rating(b, "meaning", 9, 4)
    only_a = storage.list_perma_ratings(session_id=a)
    assert len(only_a) == 1
    assert only_a[0]["importance"] == 8


def test_list_perma_ratings_validates_dimension(storage: Storage):
    with pytest.raises(ValueError, match="dimension"):
        storage.list_perma_ratings(dimension="vibes")


def test_list_perma_ratings_newest_first(storage: Storage):
    s = storage.start_assessment_session("great_life")["session_id"]
    storage.record_perma_rating(s, "positive_emotions", 5, 5)
    storage.record_perma_rating(s, "vitality", 7, 7)
    rows = storage.list_perma_ratings()
    # newest first
    assert rows[0]["dimension"] == "vitality"


# --- list_entity_slu_links since filter -------------------------------------


def test_list_entity_slu_links_since_filter(storage: Storage):
    project = storage.write("project", {"name": "P"})
    slu_a, slu_b = storage.list_active_slus()[:2]
    storage.link_entity_to_slu("project", project["id"], slu_a["id"])
    # the second link gets a slightly later timestamp; use 'since' on a
    # midpoint to confirm filtering
    storage.link_entity_to_slu("project", project["id"], slu_b["id"])

    all_links = storage.list_entity_slu_links()
    assert len(all_links) == 2

    # since=future should return nothing
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    assert storage.list_entity_slu_links(since=future) == []

    # since=past should return both
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    assert len(storage.list_entity_slu_links(since=past)) == 2


# --- constitution cue present in every skill --------------------------------


@pytest.mark.parametrize(
    "skill_file",
    sorted(SKILLS_DIR.glob("*.md")),
    ids=lambda p: p.stem,
)
def test_skill_references_constitution(skill_file: Path):
    """Every skill should tell the LLM to read the constitution at the top.

    Without this cue the constitution remains silently optional and the
    system's tone discipline becomes load-bearing on LLM discretion.
    """
    body = skill_file.read_text(encoding="utf-8")
    assert "constitution://main" in body, (
        f"{skill_file.name} doesn't tell the LLM to load constitution://main; "
        f"add a 'Before you begin' block after the H1."
    )


# --- frontmatter description surfacing --------------------------------------


def test_server_uses_frontmatter_description_for_skills(tmp_path):
    """The MCP resource description for each skill must be the skill's
    own frontmatter description, not the auto-generated stub.

    Audit finding #1: discovery is fragile when every skill description
    is 'Skill procedure: X'. The fix is to surface the frontmatter
    description at registration time.
    """
    from meta_assistant.server import build_server

    skills_dir = tmp_path / "skills"
    schemas_dir = tmp_path / "schemas"
    constitution = tmp_path / "constitution.md"
    skills_dir.mkdir()
    schemas_dir.mkdir()
    constitution.write_text("x")

    # Build a fake skill with a known frontmatter description; ensure the
    # registered resource exposes it (not the stub).
    (skills_dir / "fake_skill.md").write_text(
        "---\n"
        "name: fake_skill\n"
        "description: |\n"
        "  WHAT this does.\n"
        "\n"
        "  Triggers: \"/fake\", \"do the fake thing\".\n"
        "\n"
        "  Not for: anything else.\n"
        "---\n"
        "\n"
        "# Fake Skill\n"
    )
    # A second skill with no frontmatter — should fall back to stub.
    (skills_dir / "noframe.md").write_text("# Just a body, no frontmatter\n")

    mcp, _ = build_server(
        db_path=str(tmp_path / "v3_2.db"),
        skills_dir=skills_dir,
        schemas_dir=schemas_dir,
        constitution_path=constitution,
    )

    by_uri = {
        str(r.uri): r.description or ""
        for r in mcp._resource_manager.list_resources()
    }
    fake_desc = by_uri["skill://fake_skill"]
    assert "WHAT this does." in fake_desc
    assert "Triggers" in fake_desc
    assert "Skill procedure: fake_skill" not in fake_desc

    # Fallback path for missing frontmatter
    noframe_desc = by_uri["skill://noframe"]
    assert noframe_desc == "Skill procedure: noframe"


def test_real_skill_descriptions_are_actionable():
    """Every real skill on disk should have a parseable, trigger-bearing
    frontmatter description (not the stub)."""
    from meta_assistant.server import _parse_skill_description

    for skill_file in sorted(SKILLS_DIR.glob("*.md")):
        desc = _parse_skill_description(skill_file)
        assert desc is not None, f"{skill_file.name} has no parseable description"
        assert "Triggers" in desc, (
            f"{skill_file.name} description has no 'Triggers' section — "
            f"discovery via phrase-match will fail"
        )
        assert "Not for" in desc, (
            f"{skill_file.name} description has no 'Not for' section — "
            f"sibling-skill disambiguation will fail"
        )


# --- server smoke for the new tools -----------------------------------------


def test_server_registers_v3_2_tools(tmp_path):
    from meta_assistant.server import build_server

    skills_dir = tmp_path / "skills"
    schemas_dir = tmp_path / "schemas"
    guides_dir = tmp_path / "guides"
    constitution = tmp_path / "constitution.md"
    skills_dir.mkdir()
    schemas_dir.mkdir()
    guides_dir.mkdir()
    constitution.write_text("x")

    mcp, _ = build_server(
        db_path=str(tmp_path / "v3_2.db"),
        skills_dir=skills_dir,
        schemas_dir=schemas_dir,
        guides_dir=guides_dir,
        constitution_path=constitution,
    )
    tool_names = {t.name for t in mcp._tool_manager.list_tools()}
    assert "state_list_perma_ratings" in tool_names
    assert "state_list_entity_slu_links" in tool_names


# --- guides directory + overview --------------------------------------------


GUIDES_DIR = Path(__file__).resolve().parent.parent / "guides"


def test_guides_directory_registered_with_frontmatter_descriptions(tmp_path):
    """A markdown file in guides/ should be served at guide://<name> and
    surface its frontmatter description in resources/list."""
    from meta_assistant.server import build_server

    skills_dir = tmp_path / "skills"
    schemas_dir = tmp_path / "schemas"
    guides_dir = tmp_path / "guides"
    constitution = tmp_path / "constitution.md"
    for d in (skills_dir, schemas_dir, guides_dir):
        d.mkdir()
    constitution.write_text("x")

    (guides_dir / "fake_guide.md").write_text(
        "---\n"
        "name: fake_guide\n"
        "description: |\n"
        "  Test guide content.\n"
        "  Triggers: \"/fake\".\n"
        "  Not for: anything real.\n"
        "---\n"
        "\n"
        "# Fake Guide\n"
    )

    mcp, _ = build_server(
        db_path=str(tmp_path / "v3_2.db"),
        skills_dir=skills_dir,
        schemas_dir=schemas_dir,
        guides_dir=guides_dir,
        constitution_path=constitution,
    )
    by_uri = {
        str(r.uri): r.description or ""
        for r in mcp._resource_manager.list_resources()
    }
    assert "guide://fake_guide" in by_uri
    assert "Test guide content" in by_uri["guide://fake_guide"]


def test_overview_guide_exists_and_is_discoverable():
    """The shipped overview guide must exist, have an actionable
    description with triggers and 'Not for', and live at guide://overview."""
    overview = GUIDES_DIR / "overview.md"
    assert overview.exists(), "guides/overview.md should be present"

    from meta_assistant.server import _parse_skill_description

    desc = _parse_skill_description(overview)
    assert desc is not None
    assert "Triggers" in desc
    assert "Not for" in desc
    # The overview is a discovery aid — its description must reference
    # the system it maps.
    body = overview.read_text(encoding="utf-8")
    for must_mention in (
        "skill://capture",
        "skill://scan_life_units",
        "skill://opportunity_research",
        "constitution://main",
        "Intent → skill",
    ):
        assert must_mention in body, f"overview.md should mention {must_mention!r}"
