"""Tests for v6.1: tool-wrapped access to constitution/skills/schemas/guides.

The architectural reason these tools exist: most production MCP clients
(Claude Desktop, Cowork, claude.ai) don't surface resource-reading to
the LLM — only tools. Without these wrappers, every skill markdown,
the constitution, the overview guide, and every schema doc is
invisible to the model in those surfaces. The wrappers give the LLM
a tool-grammar path to the same content the resource:// URIs expose.

These tests cover the seven new tools (state_read_constitution,
state_list_skills, state_read_skill, state_list_schemas,
state_read_schema, state_list_guides, state_read_guide), name
validation (no path traversal), missing-file errors, and content
parity with what `resources/list` would return.
"""

import json
from pathlib import Path

import pytest


SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


def _build(tmp_path):
    from meta_assistant.server import build_server
    skills_dir = tmp_path / "skills"
    schemas_dir = tmp_path / "schemas"
    guides_dir = tmp_path / "guides"
    constitution = tmp_path / "constitution.md"
    for d in (skills_dir, schemas_dir, guides_dir):
        d.mkdir()
    constitution.write_text("# Constitution\n\nBe kind. Be terse.\n")
    # A skill, a schema, a guide — each with frontmatter so we can check
    # description parsing.
    (skills_dir / "demo.md").write_text(
        "---\nname: demo\ndescription: |\n  Demo skill.\n\n  Triggers: \"/demo\".\n\n"
        "  Not for: anything serious.\n---\n\n# Demo\n\nbody of demo skill\n"
    )
    (schemas_dir / "demo.md").write_text(
        "# demo\n\nfields of the demo entity\n"
    )
    (guides_dir / "overview.md").write_text(
        "---\nname: overview\ndescription: |\n  Test overview.\n\n  Triggers: \"/help\".\n\n"
        "  Not for: anything.\n---\n\n# Overview\n\nbody of overview\n"
    )
    mcp, storage = build_server(
        db_path=str(tmp_path / "v6_1.db"),
        skills_dir=skills_dir,
        schemas_dir=schemas_dir,
        guides_dir=guides_dir,
        constitution_path=constitution,
    )
    return mcp, storage


def _call(mcp, tool_name, **kwargs):
    """Invoke a registered tool by name. The local arg is `tool_name`
    (not `name`) because several tools take a `name=` kwarg of their own
    and shadowing would clobber them."""
    return mcp._tool_manager._tools[tool_name].fn(**kwargs)


# --- registration --------------------------------------------------------


def test_v6_1_tools_are_registered(tmp_path):
    mcp, _ = _build(tmp_path)
    names = {t.name for t in mcp._tool_manager.list_tools()}
    expected = {
        "state_read_constitution",
        "state_list_skills",
        "state_read_skill",
        "state_list_schemas",
        "state_read_schema",
        "state_list_guides",
        "state_read_guide",
    }
    assert expected <= names


# --- constitution --------------------------------------------------------


def test_read_constitution_returns_full_body(tmp_path):
    mcp, _ = _build(tmp_path)
    body = _call(mcp, "state_read_constitution")
    assert "Be kind" in body
    assert "Be terse" in body


def test_read_constitution_missing(tmp_path):
    """If the constitution file isn't there, the tool errors cleanly
    rather than returning empty content."""
    mcp, _ = _build(tmp_path)
    # Remove it AFTER server build so registration succeeded with a
    # valid path; the tool reads on each call so it'll now miss.
    (tmp_path / "constitution.md").unlink()
    with pytest.raises(ValueError, match="constitution not found"):
        _call(mcp, "state_read_constitution")


# --- skills --------------------------------------------------------------


def test_list_skills_returns_name_description_uri(tmp_path):
    mcp, _ = _build(tmp_path)
    catalog = _call(mcp, "state_list_skills")
    assert len(catalog) == 1
    entry = catalog[0]
    assert entry["name"] == "demo"
    assert entry["uri"] == "skill://demo"
    # Description came from frontmatter — should mention triggers
    assert "Triggers" in entry["description"]


def test_list_skills_falls_back_to_stub_when_no_frontmatter(tmp_path):
    """A skill markdown without frontmatter still appears, with the
    auto-stub description."""
    mcp, _ = _build(tmp_path)
    # Add a no-frontmatter skill
    (tmp_path / "skills" / "bare.md").write_text("# Bare\n\njust a body\n")
    catalog = _call(mcp, "state_list_skills")
    bare = next(c for c in catalog if c["name"] == "bare")
    assert bare["description"] == "Skill procedure: bare"


def test_read_skill_returns_body(tmp_path):
    mcp, _ = _build(tmp_path)
    body = _call(mcp, "state_read_skill", name="demo")
    assert "body of demo skill" in body
    # Frontmatter should be present too — the LLM needs the WHOLE file
    # for the "Before you begin" cue, the steps, etc.
    assert "name: demo" in body


def test_read_skill_missing_name_errors(tmp_path):
    mcp, _ = _build(tmp_path)
    with pytest.raises(ValueError, match="no skill"):
        _call(mcp, "state_read_skill", name="no-such-skill")


# --- name validation: path-traversal & garbage protection ---------------


@pytest.mark.parametrize("bad_name", [
    "../etc/passwd",
    "/absolute/path",
    "with spaces",
    "..",
    ".",
    "",
    "foo/bar",
    "foo.md",          # already has extension; tool appends .md itself
    "foo$bar",
    "foo;bar",
    "foo`bar",
])
def test_read_skill_rejects_unsafe_names(tmp_path, bad_name):
    mcp, _ = _build(tmp_path)
    with pytest.raises(ValueError, match="alphanumeric"):
        _call(mcp, "state_read_skill", name=bad_name)


@pytest.mark.parametrize("bad_name", ["../foo", "foo.md", "foo/bar"])
def test_read_schema_rejects_unsafe_names(tmp_path, bad_name):
    mcp, _ = _build(tmp_path)
    with pytest.raises(ValueError, match="alphanumeric"):
        _call(mcp, "state_read_schema", name=bad_name)


@pytest.mark.parametrize("bad_name", ["../foo", "foo.md", "foo/bar"])
def test_read_guide_rejects_unsafe_names(tmp_path, bad_name):
    mcp, _ = _build(tmp_path)
    with pytest.raises(ValueError, match="alphanumeric"):
        _call(mcp, "state_read_guide", name=bad_name)


# --- schemas + guides (same shape as skills) ----------------------------


def test_list_schemas_and_read_schema(tmp_path):
    mcp, _ = _build(tmp_path)
    catalog = _call(mcp, "state_list_schemas")
    assert len(catalog) == 1
    assert catalog[0]["name"] == "demo"
    assert catalog[0]["uri"] == "schema://demo"
    body = _call(mcp, "state_read_schema", name="demo")
    assert "fields of the demo entity" in body


def test_list_guides_and_read_guide(tmp_path):
    mcp, _ = _build(tmp_path)
    catalog = _call(mcp, "state_list_guides")
    assert len(catalog) == 1
    assert catalog[0]["name"] == "overview"
    body = _call(mcp, "state_read_guide", name="overview")
    assert "body of overview" in body


# --- parity with resource:// URIs ---------------------------------------


def test_tool_and_resource_paths_return_same_content(tmp_path):
    """The whole point of v6.1 is that tool-wrapped access returns the
    same bytes as resource-wrapped access. Verify by reading both ways
    and comparing."""
    mcp, _ = _build(tmp_path)

    # Constitution
    via_tool = _call(mcp, "state_read_constitution")
    via_resource = mcp._resource_manager._resources["constitution://main"].fn()
    assert via_tool == via_resource

    # A skill
    via_tool = _call(mcp, "state_read_skill", name="demo")
    via_resource = mcp._resource_manager._resources["skill://demo"].fn()
    assert via_tool == via_resource

    # A guide
    via_tool = _call(mcp, "state_read_guide", name="overview")
    via_resource = mcp._resource_manager._resources["guide://overview"].fn()
    assert via_tool == via_resource


def test_list_skills_matches_resource_list_for_skill_scheme(tmp_path):
    """state_list_skills should surface the same {name, description}
    pairs that resources/list does for skill:// URIs."""
    mcp, _ = _build(tmp_path)
    tool_catalog = {c["name"]: c["description"]
                    for c in _call(mcp, "state_list_skills")}
    resource_catalog = {
        str(r.uri).removeprefix("skill://"): r.description
        for r in mcp._resource_manager.list_resources()
        if str(r.uri).startswith("skill://")
    }
    assert tool_catalog == resource_catalog


# --- v6 logging integration ---------------------------------------------


def test_content_access_calls_are_logged(tmp_path):
    """The v6 wrapper sweeps over every registered tool; the v6.1 tools
    should land in tool_calls just like every other one."""
    mcp, storage = _build(tmp_path)
    _call(mcp, "state_read_constitution")
    _call(mcp, "state_list_skills")
    _call(mcp, "state_read_skill", name="demo")

    import sqlite3
    with sqlite3.connect(storage.db_path) as conn:
        rows = conn.execute(
            "SELECT tool_name, status FROM tool_calls "
            "WHERE tool_name IN "
            "('state_read_constitution', 'state_list_skills', 'state_read_skill') "
            "ORDER BY started_at"
        ).fetchall()
    names = [r[0] for r in rows]
    assert names == [
        "state_read_constitution", "state_list_skills", "state_read_skill",
    ]
    assert all(r[1] == "ok" for r in rows)


# --- shipped skills are reachable through the wrapper -------------------


def test_real_shipped_skills_listed(tmp_path):
    """Smoke test: build a server pointing at the actual project skills
    directory and confirm every shipped skill shows up in
    state_list_skills with its real frontmatter description."""
    from meta_assistant.server import build_server
    schemas_dir = tmp_path / "schemas"
    guides_dir = tmp_path / "guides"
    schemas_dir.mkdir()
    guides_dir.mkdir()
    constitution = tmp_path / "constitution.md"
    constitution.write_text("x")
    mcp, _ = build_server(
        db_path=str(tmp_path / "shipped.db"),
        skills_dir=SKILLS_DIR,
        schemas_dir=schemas_dir,
        guides_dir=guides_dir,
        constitution_path=constitution,
    )
    catalog = _call(mcp, "state_list_skills")
    names = {c["name"] for c in catalog}
    # The full v6 skill set
    expected = {
        "capture", "inbox", "opportunity_research",
        "scan_life_units", "scan_inventory",
        "inventory_intake", "add_inventory_items",
        "find", "define_great_life", "assess_life_portfolio",
        "patch_portfolio", "quick_update_slu", "update_resources",
        "trace",
    }
    assert expected <= names
    # Every shipped skill should have a real (non-stub) description with
    # the trigger pattern we standardized in v3.2.
    for c in catalog:
        if c["name"] in expected:
            assert "Triggers" in c["description"], (
                f"shipped skill {c['name']} has no Triggers in description"
            )


def test_overview_guide_mentions_both_access_paths():
    """The discovery doc should tell agents about the resource vs. tool
    paths — otherwise they have no way to know to use the wrappers."""
    body = (
        Path(__file__).resolve().parent.parent / "guides" / "overview.md"
    ).read_text()
    assert "state_read_constitution" in body
    assert "state_read_skill" in body
    assert "resource" in body.lower()
