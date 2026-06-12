"""End-to-end: draft a decision, attach a source, commit it, query it back.

Mirrors the acceptance criterion: 'draft → list → attach source → commit →
query → source link is queryable.'
"""

from meta_assistant.storage import Storage


def test_full_capture_cycle(tmp_path):
    storage = Storage(tmp_path / "e2e.db")

    # 1. draft a decision (capture skill, step 3)
    prop = storage.create_draft(
        entity_type="decision",
        data={
            "what": "Build the MVP in Python with FastMCP",
            "rationale": "official SDK, least friction for HTTP transport",
            "alternatives_considered": "TypeScript SDK, hand-rolled JSON-RPC",
        },
        session_id="cap-001",
    )

    # 2. list drafts (capture skill, step 5/6 — show user)
    pending = storage.list_drafts(session_id="cap-001", status="pending")
    assert len(pending) == 1
    assert pending[0]["id"] == prop["id"]

    # 3. attach a source excerpt (capture skill, step 4)
    source_id = storage.attach_source(
        target_type="draft",
        target_id=prop["id"],
        source_type="chat",
        content="user: yes go with FastMCP and HTTP transport",
    )
    assert source_id

    # 4. commit the draft (capture skill, step 6 — user said all good)
    entity = storage.commit_draft(prop["id"])
    assert entity["what"].startswith("Build the MVP")
    assert entity["id"]

    # 5. query decisions — the committed entity is there
    decisions = storage.query("decision")
    assert any(d["id"] == entity["id"] for d in decisions)

    # 6. the source attached to the draft is queryable
    draft_sources = storage.sources_for("draft", prop["id"])
    assert len(draft_sources) == 1
    assert draft_sources[0]["id"] == source_id

    # 7. the draft itself now shows committed status with the entity id
    after = storage.list_drafts(session_id="cap-001")
    assert after[0]["status"] == "committed"
    assert after[0]["committed_entity_id"] == entity["id"]


def test_persistence_across_reopen(tmp_path):
    db = tmp_path / "persist.db"
    s1 = Storage(db)
    p = s1.write("project", {"name": "persistent"})

    # simulate process restart
    s2 = Storage(db)
    rows = s2.query("project", {"name": "persistent"})
    assert len(rows) == 1
    assert rows[0]["id"] == p["id"]


def test_server_registers_tools_and_resources(tmp_path):
    """Smoke test: build_server wires up tools and resources without errors."""
    from meta_assistant.server import build_server

    skills_dir = tmp_path / "skills"
    schemas_dir = tmp_path / "schemas"
    constitution = tmp_path / "constitution.md"
    skills_dir.mkdir()
    schemas_dir.mkdir()
    (skills_dir / "capture.md").write_text("# capture\nhello")
    (schemas_dir / "projects.md").write_text("# projects\nfields")
    constitution.write_text("hi there")

    mcp, storage = build_server(
        db_path=str(tmp_path / "srv.db"),
        skills_dir=skills_dir,
        schemas_dir=schemas_dir,
        constitution_path=constitution,
    )
    # storage is functional
    p = storage.write("project", {"name": "via server"})
    assert p["id"]
    # tools and resources are registered (managers expose internal dicts)
    tool_names = {t.name for t in mcp._tool_manager.list_tools()}
    expected = {
        "state_query", "state_write", "state_update",
        "state_draft", "state_list_drafts", "state_commit_draft",
        "state_reject_draft", "state_amend_draft", "state_attach_source",
    }
    assert expected <= tool_names

    resource_uris = {str(r.uri) for r in mcp._resource_manager.list_resources()}
    assert "constitution://main" in resource_uris
    assert "skill://capture" in resource_uris
    assert "schema://projects" in resource_uris
