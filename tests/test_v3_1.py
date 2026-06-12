"""Tests for v3.1: commitments, stakeholders, interests.

Covers per-entity create + validation, the new transition tools, the
propose/commit flow extended to all three new types, auto-derived
interests.source_count, overdue-contact filtering, and an end-to-end
replay of the seed-extraction writes the caller previously couldn't land.
"""

from datetime import datetime, timedelta, timezone

import pytest

from meta_assistant.storage import Storage


# --- commitments -------------------------------------------------------------


def test_create_commitment_happy_path(storage: Storage):
    c = storage.write(
        "commitment",
        {
            "what": "send the parents a long letter",
            "to_whom": "parents",
            "due_at": "2026-06-01T00:00:00Z",
        },
    )
    assert c["id"]
    assert c["status"] == "open"
    assert c["to_whom"] == "parents"
    assert c["completed_at"] is None


def test_create_commitment_requires_what_and_to_whom(storage: Storage):
    with pytest.raises(ValueError, match="what"):
        storage.write("commitment", {"to_whom": "self"})
    with pytest.raises(ValueError, match="to_whom"):
        storage.write("commitment", {"what": "do a thing"})


def test_create_commitment_rejects_bad_status(storage: Storage):
    with pytest.raises(ValueError, match="status"):
        storage.write(
            "commitment",
            {"what": "x", "to_whom": "self", "status": "maybe"},
        )


def test_create_commitment_validates_related_project(storage: Storage):
    with pytest.raises(ValueError, match="related_project_id"):
        storage.write(
            "commitment",
            {"what": "x", "to_whom": "self", "related_project_id": "no-such-id"},
        )


def test_complete_commitment(storage: Storage):
    c = storage.write(
        "commitment", {"what": "ship v3.1", "to_whom": "self"}
    )
    done = storage.complete_commitment(c["id"], outcome="shipped on time")
    assert done["status"] == "done"
    assert done["completed_at"]
    assert done["outcome"] == "shipped on time"


def test_complete_commitment_errors_on_non_open(storage: Storage):
    c = storage.write(
        "commitment", {"what": "x", "to_whom": "self"}
    )
    storage.complete_commitment(c["id"])
    with pytest.raises(ValueError, match="cannot complete"):
        storage.complete_commitment(c["id"])


def test_drop_commitment(storage: Storage):
    c = storage.write("commitment", {"what": "x", "to_whom": "self"})
    dropped = storage.drop_commitment(c["id"], reason="circumstances changed")
    assert dropped["status"] == "dropped"
    assert dropped["dropped_reason"] == "circumstances changed"
    assert dropped["completed_at"]


def test_update_commitment_via_state_update(storage: Storage):
    c = storage.write("commitment", {"what": "x", "to_whom": "self"})
    updated = storage.update(
        "commitment", c["id"], {"due_at": "2026-07-01T00:00:00Z"}
    )
    assert updated["due_at"] == "2026-07-01T00:00:00Z"
    with pytest.raises(ValueError, match="status"):
        storage.update("commitment", c["id"], {"status": "maybe"})


def test_query_commitments_with_filters(storage: Storage):
    storage.write("commitment", {"what": "a", "to_whom": "self"})
    b = storage.write("commitment", {"what": "b", "to_whom": "self"})
    storage.complete_commitment(b["id"])
    open_rows = storage.query("commitment", {"status": "open"})
    done_rows = storage.query("commitment", {"status": "done"})
    assert len(open_rows) == 1 and open_rows[0]["what"] == "a"
    assert len(done_rows) == 1 and done_rows[0]["what"] == "b"


# --- stakeholders ------------------------------------------------------------


def test_create_stakeholder_happy_path(storage: Storage):
    s = storage.write(
        "stakeholder",
        {
            "name": "Sarah from the surf bar",
            "role": "club regular",
            "relationship_strength": "regular",
            "owed_action": "send the playlist",
        },
    )
    assert s["id"]
    assert s["last_contact_at"] is None
    assert s["created_at"] == s["updated_at"]


def test_create_stakeholder_requires_name(storage: Storage):
    with pytest.raises(ValueError, match="name"):
        storage.write("stakeholder", {"role": "neighbor"})


def test_create_stakeholder_validates_strength(storage: Storage):
    with pytest.raises(ValueError, match="relationship_strength"):
        storage.write(
            "stakeholder",
            {"name": "X", "relationship_strength": "intense"},
        )


def test_update_contact_records_timestamp_and_appends_note(storage: Storage):
    s = storage.write("stakeholder", {"name": "Sam"})
    when = "2026-05-10T12:00:00+00:00"
    updated = storage.update_contact(s["id"], when=when, note="coffee uptown")
    assert updated["last_contact_at"] == when
    assert "coffee uptown" in updated["notes"]
    assert when in updated["notes"]
    # second contact preserves the first
    later = "2026-05-15T12:00:00+00:00"
    updated2 = storage.update_contact(s["id"], when=later, note="text reply")
    assert "coffee uptown" in updated2["notes"]
    assert "text reply" in updated2["notes"]
    assert updated2["last_contact_at"] == later


def test_update_contact_defaults_when_to_now(storage: Storage):
    s = storage.write("stakeholder", {"name": "Sam"})
    updated = storage.update_contact(s["id"])
    assert updated["last_contact_at"] is not None


def test_update_contact_errors_on_missing(storage: Storage):
    with pytest.raises(ValueError, match="not found"):
        storage.update_contact("no-such-id")


def test_link_stakeholder_to_project(storage: Storage):
    s = storage.write("stakeholder", {"name": "Co-founder"})
    p = storage.write("project", {"name": "the new thing"})
    link = storage.link_stakeholder_to_project(
        s["id"], p["id"], role_in_project="design lead"
    )
    assert link["stakeholder_id"] == s["id"]
    assert link["role_in_project"] == "design lead"
    links = storage.list_stakeholder_project_links(stakeholder_id=s["id"])
    assert len(links) == 1
    with pytest.raises(ValueError, match="stakeholder"):
        storage.link_stakeholder_to_project("no-such-id", p["id"])
    with pytest.raises(ValueError, match="project"):
        storage.link_stakeholder_to_project(s["id"], "no-such-id")


def test_list_overdue_contacts(storage: Storage):
    a = storage.write("stakeholder", {"name": "Long ago"})
    b = storage.write("stakeholder", {"name": "Recent"})
    c = storage.write("stakeholder", {"name": "Never contacted"})
    # 60 days ago
    old = (
        datetime.now(timezone.utc) - timedelta(days=60)
    ).isoformat()
    fresh = (
        datetime.now(timezone.utc) - timedelta(days=3)
    ).isoformat()
    storage.update_contact(a["id"], when=old)
    storage.update_contact(b["id"], when=fresh)
    # c left untouched (last_contact_at NULL)

    overdue = storage.list_overdue_contacts(threshold_days=30)
    names = [r["name"] for r in overdue]
    assert "Recent" not in names
    assert "Long ago" in names
    assert "Never contacted" in names
    # never-contacted comes first
    assert names[0] == "Never contacted"


def test_list_overdue_contacts_validates(storage: Storage):
    with pytest.raises(ValueError, match="threshold_days"):
        storage.list_overdue_contacts(-1)
    with pytest.raises(ValueError, match="threshold_days"):
        storage.list_overdue_contacts("thirty")


# --- interests ---------------------------------------------------------------


def test_create_interest_happy_path(storage: Storage):
    i = storage.write(
        "interest",
        {
            "topic": "Russian acmeist poetry",
            "description": "Mandelstam, Akhmatova; comes up across years of notes.",
        },
    )
    assert i["id"]
    assert i["status"] == "active"
    assert i["first_observed_at"] == i["last_observed_at"]


def test_create_interest_requires_topic(storage: Storage):
    with pytest.raises(ValueError, match="topic"):
        storage.write("interest", {"description": "no topic"})


def test_create_interest_validates_status(storage: Storage):
    with pytest.raises(ValueError, match="status"):
        storage.write(
            "interest",
            {"topic": "x", "status": "asleep"},
        )


def test_interest_source_count_is_auto_derived(storage: Storage):
    i = storage.write("interest", {"topic": "ham radio"})
    # zero before any source is attached
    [row] = storage.query("interest", {"id": i["id"]})
    assert row["source_count"] == 0

    storage.attach_source(
        "interest", i["id"], "chat", "user: still thinking about ham radio"
    )
    [row] = storage.query("interest", {"id": i["id"]})
    assert row["source_count"] == 1

    storage.attach_source(
        "interest", i["id"], "paste", "another mention in journal"
    )
    [row] = storage.query("interest", {"id": i["id"]})
    assert row["source_count"] == 2


def test_interests_can_be_slu_tagged(storage: Storage):
    i = storage.write("interest", {"topic": "longboarding"})
    health = next(
        u for u in storage.list_active_slus() if u["name"] == "Physical health"
    )
    storage.link_entity_to_slu("interest", i["id"], health["id"])
    links = storage.list_entity_slu_links(
        entity_type="interest", entity_id=i["id"]
    )
    assert len(links) == 1 and links[0]["life_unit_id"] == health["id"]


# --- propose/commit for the three new entity types ---------------------------


def test_proposal_flow_for_commitment(storage: Storage):
    prop = storage.create_draft(
        "commitment",
        {"what": "send the playlist", "to_whom": "Sarah"},
        session_id="cap-1",
    )
    assert prop["status"] == "pending"
    entity = storage.commit_draft(prop["id"])
    assert entity["what"] == "send the playlist"
    assert entity["status"] == "open"
    # appears in query
    rows = storage.query("commitment", {"id": entity["id"]})
    assert len(rows) == 1


def test_proposal_flow_for_stakeholder(storage: Storage):
    prop = storage.create_draft(
        "stakeholder",
        {"name": "Surf Bar owner", "role": "club host"},
        session_id="cap-1",
    )
    entity = storage.commit_draft(prop["id"])
    assert entity["name"] == "Surf Bar owner"


def test_proposal_flow_for_interest(storage: Storage):
    prop = storage.create_draft(
        "interest",
        {"topic": "deep work practices", "description": "recurring theme"},
        session_id="cap-1",
    )
    entity = storage.commit_draft(prop["id"])
    assert entity["topic"] == "deep work practices"


def test_amend_then_commit_works_for_new_types(storage: Storage):
    prop = storage.create_draft("interest", {"topic": "wrong topic"})
    storage.amend_draft(prop["id"], {"topic": "right topic"})
    entity = storage.commit_draft(prop["id"])
    assert entity["topic"] == "right topic"


# --- entity-type catalog --------------------------------------------------


def test_write_rejects_unknown_entity_type(storage: Storage):
    with pytest.raises(ValueError, match="entity_type"):
        storage.write("widget", {"name": "x"})


def test_query_rejects_unknown_entity_type(storage: Storage):
    with pytest.raises(ValueError, match="entity_type"):
        storage.query("widget")


# --- e2e: seed replay -------------------------------------------------------


def test_e2e_seed_replay(tmp_path):
    """Mirrors the writes that the seed extraction couldn't land before v3.1:
    two commitments, three stakeholders, ten interests. All go through the
    propose/commit flow (the v3.1 design choice). At the end, the data is
    queryable and counts match."""
    storage = Storage(tmp_path / "seed.db")
    session = "seed-001"

    commitments = [
        {"what": "send long letter to parents", "to_whom": "parents",
         "due_at": "2026-06-01T00:00:00Z"},
        {"what": "draft the new portfolio post", "to_whom": "self",
         "due_at": "2026-05-31T00:00:00Z"},
    ]
    stakeholders = [
        {"name": "Parents", "role": "family", "relationship_strength": "close",
         "owed_action": "long letter"},
        {"name": "Surf Bar owner", "role": "club host",
         "relationship_strength": "regular"},
        {"name": "Club regulars", "role": "weekly crowd",
         "relationship_strength": "regular"},
    ]
    interests = [
        {"topic": t} for t in [
            "Russian acmeist poetry",
            "long-form essays on attention",
            "small CLI tooling",
            "olympic-style weightlifting",
            "single-malt history",
            "Hebrew morphology",
            "city-walking as practice",
            "biographies of late-career switchers",
            "self-hosted infra",
            "the politics of attention economies",
        ]
    ]

    proposal_ids = []
    for c in commitments:
        proposal_ids.append(storage.create_draft("commitment", c, session_id=session)["id"])
    for s in stakeholders:
        proposal_ids.append(storage.create_draft("stakeholder", s, session_id=session)["id"])
    for i in interests:
        proposal_ids.append(storage.create_draft("interest", i, session_id=session)["id"])

    # user confirms everything
    for pid in proposal_ids:
        storage.commit_draft(pid)

    assert len(storage.query("commitment")) == 2
    assert len(storage.query("stakeholder")) == 3
    assert len(storage.query("interest")) == 10
    # the interest source_count starts at zero across the board
    for row in storage.query("interest"):
        assert row["source_count"] == 0


# --- server wiring smoke ----------------------------------------------------


def test_server_registers_v3_1_tools(tmp_path):
    from meta_assistant.server import build_server

    skills_dir = tmp_path / "skills"
    schemas_dir = tmp_path / "schemas"
    constitution = tmp_path / "constitution.md"
    for d in (skills_dir, schemas_dir):
        d.mkdir()
    constitution.write_text("x")

    mcp, _ = build_server(
        db_path=str(tmp_path / "v3_1.db"),
        skills_dir=skills_dir,
        schemas_dir=schemas_dir,
        constitution_path=constitution,
    )
    tool_names = {t.name for t in mcp._tool_manager.list_tools()}
    expected = {
        "state_complete_commitment",
        "state_drop_commitment",
        "state_update_contact",
        "state_link_stakeholder_to_project",
        "state_list_overdue_contacts",
    }
    assert expected <= tool_names
