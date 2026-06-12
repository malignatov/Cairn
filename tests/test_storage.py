"""Happy-path tests for each storage method (one per MCP tool)."""

import pytest

from meta_assistant.storage import Storage


def test_write_project(storage: Storage):
    p = storage.write(
        "project",
        {"name": "meta-assistant", "description": "the MVP", "kill_criteria": "if I lose interest"},
    )
    assert p["id"]
    assert p["name"] == "meta-assistant"
    assert p["status"] == "active"
    assert p["created_at"] == p["last_touched"]


def test_write_decision_requires_what_and_rationale(storage: Storage):
    with pytest.raises(ValueError):
        storage.write("decision", {"what": "ship it"})
    with pytest.raises(ValueError):
        storage.write("decision", {"rationale": "because"})


def test_write_decision(storage: Storage):
    p = storage.write("project", {"name": "x"})
    d = storage.write(
        "decision",
        {
            "project_id": p["id"],
            "what": "use SQLite",
            "rationale": "lowest-friction local store",
            "alternatives_considered": "postgres, files",
        },
    )
    assert d["id"]
    assert d["project_id"] == p["id"]


def test_query_with_filters(storage: Storage):
    storage.write("project", {"name": "alpha", "status": "active"})
    storage.write("project", {"name": "beta", "status": "killed"})
    active = storage.query("project", {"status": "active"})
    killed = storage.query("project", {"status": "killed"})
    assert len(active) == 1 and active[0]["name"] == "alpha"
    assert len(killed) == 1 and killed[0]["name"] == "beta"


def test_query_rejects_unknown_entity_type(storage: Storage):
    with pytest.raises(ValueError):
        storage.query("widgets")


def test_update(storage: Storage):
    p = storage.write("project", {"name": "to-update"})
    original_touched = p["last_touched"]
    updated = storage.update("project", p["id"], {"status": "snoozed"})
    assert updated["status"] == "snoozed"
    assert updated["last_touched"] >= original_touched
    assert updated["name"] == "to-update"  # unchanged


def test_update_rejects_bad_status(storage: Storage):
    p = storage.write("project", {"name": "x"})
    with pytest.raises(ValueError):
        storage.update("project", p["id"], {"status": "exploding"})


def test_propose_creates_pending(storage: Storage):
    prop = storage.create_draft(
        "decision",
        {"what": "do less", "rationale": "more is enemy of better"},
        session_id="sess-1",
    )
    assert prop["status"] == "pending"
    assert prop["session_id"] == "sess-1"
    assert prop["data"]["what"] == "do less"


def test_list_proposals_filters(storage: Storage):
    a = storage.create_draft("project", {"name": "p1"}, session_id="s")
    b = storage.create_draft("project", {"name": "p2"}, session_id="other")
    by_session = storage.list_drafts(session_id="s")
    assert {r["id"] for r in by_session} == {a["id"]}
    pending = storage.list_drafts(status="pending")
    assert {r["id"] for r in pending} == {a["id"], b["id"]}


def test_commit_proposal(storage: Storage):
    prop = storage.create_draft(
        "decision",
        {"what": "ship v1", "rationale": "stop polishing"},
    )
    entity = storage.commit_draft(prop["id"])
    assert entity["id"] and entity["id"] != prop["id"]
    after = storage.list_drafts()
    after_one = next(r for r in after if r["id"] == prop["id"])
    assert after_one["status"] == "committed"
    assert after_one["committed_entity_id"] == entity["id"]


def test_commit_proposal_twice_errors(storage: Storage):
    prop = storage.create_draft("project", {"name": "p"})
    storage.commit_draft(prop["id"])
    with pytest.raises(ValueError):
        storage.commit_draft(prop["id"])


def test_reject_proposal(storage: Storage):
    prop = storage.create_draft("project", {"name": "doomed"})
    rejected = storage.reject_draft(prop["id"], "duplicate")
    assert rejected["status"] == "rejected"
    assert rejected["rejection_reason"] == "duplicate"
    with pytest.raises(ValueError):
        storage.commit_draft(prop["id"])


def test_amend_proposal(storage: Storage):
    prop = storage.create_draft(
        "decision",
        {"what": "use postgres", "rationale": "scale"},
    )
    amended = storage.amend_draft(
        prop["id"],
        {"what": "use sqlite", "rationale": "local-first MVP"},
    )
    assert amended["status"] == "amended"
    assert amended["data"]["what"] == "use sqlite"
    # amended proposals can still be committed
    entity = storage.commit_draft(prop["id"])
    assert entity["what"] == "use sqlite"


def test_attach_source(storage: Storage):
    p = storage.write("project", {"name": "x"})
    source_id = storage.attach_source(
        "project", p["id"], "chat", "user said: let's just build it"
    )
    assert source_id
    sources = storage.sources_for("project", p["id"])
    assert len(sources) == 1
    assert sources[0]["id"] == source_id
    assert sources[0]["source_type"] == "chat"


def test_attach_source_rejects_bad_target(storage: Storage):
    with pytest.raises(ValueError):
        storage.attach_source("widget", "abc", "chat", "hi")
    with pytest.raises(ValueError):
        storage.attach_source("project", "abc", "telegram", "hi")
