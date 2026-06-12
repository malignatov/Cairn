"""Readability: every entity read resolves its opaque FK UUIDs to a
human-readable `<field>_name` label alongside the id — never replacing it,
never stored (resolved fresh each read, so a rename can't leave a stale
label behind)."""

from meta_assistant.storage import Storage


def test_decision_carries_project_name(storage: Storage):
    p = storage.write("project", {"name": "Meta-Assistant MCP Server"})
    storage.write("decision", {"what": "ship", "rationale": "r", "project_id": p["id"]})
    d = storage.query("decision")[0]
    assert d["project_id"] == p["id"]                       # id kept
    assert d["project_name"] == "Meta-Assistant MCP Server"  # label added


def test_null_fk_label_is_none(storage: Storage):
    storage.write("decision", {"what": "standalone", "rationale": "r"})
    d = storage.query("decision")[0]
    assert d["project_id"] is None
    assert d["project_name"] is None


def test_commitment_and_idea_project_names(storage: Storage):
    p = storage.write("project", {"name": "Proj"})
    storage.write("commitment", {"what": "do", "to_whom": "self",
                                  "related_project_id": p["id"]})
    assert storage.query("commitment")[0]["related_project_name"] == "Proj"

    idea = storage.write("idea", {"title": "Tea club"})
    storage.promote_idea(idea["id"])
    promoted = [i for i in storage.query("idea") if i["id"] == idea["id"]][0]
    assert promoted["promoted_to_project_name"] == "Tea club"


def test_principle_evaluation_names(storage: Storage):
    p1 = storage.write("principle", {"name": "Courage", "description": "x", "rank": 1})
    p2 = storage.write("principle", {"name": "Health first", "description": "y", "rank": 2})
    dec = storage.write("decision", {"what": "rest instead", "rationale": "r"})
    storage.record_principle_evaluation(
        dec["id"], p2["id"], "justified_override",
        overrides_principle_id=p1["id"], rationale="health wins")
    ev = storage.list_principle_departures(relationship="justified_override")[0]
    assert ev["principle_name"] == "Health first"
    assert ev["overrides_principle_name"] == "Courage"
    assert ev["decision_what"] == "rest instead"


def test_slu_link_names(storage: Storage):
    p = storage.write("project", {"name": "Proj"})
    slu = storage.list_active_slus()[0]
    storage.link_entity_to_slu("project", p["id"], slu["id"])
    link = storage.list_entity_slu_links(entity_type="project", entity_id=p["id"])[0]
    assert link["life_unit_name"] == slu["name"]
    assert link["entity_label"] == "Proj"


def test_stakeholder_project_link_names(storage: Storage):
    p = storage.write("project", {"name": "Proj"})
    st = storage.write("stakeholder", {"name": "Yasha", "relationship_strength": "close"})
    storage.link_stakeholder_to_project(st["id"], p["id"], "connector")
    spl = storage.list_stakeholder_project_links(project_id=p["id"])[0]
    assert spl["stakeholder_name"] == "Yasha"
    assert spl["project_name"] == "Proj"


def test_observation_carries_slu_name(storage: Storage):
    slu = storage.list_active_slus()[0]
    storage.write_observation(life_unit_id=slu["id"], signal="silence",
                              severity="low", evidence={"x": 1})
    obs = storage.list_observations()[0]
    assert obs["life_unit_name"] == slu["name"]


def test_capability_requirement_names(storage: Storage):
    p = storage.write("project", {"name": "Launch"})
    cap = storage.write("capability", {"name": "Spanish", "category": "skill"})
    storage.add_capability_requirement(project_id=p["id"], capability_id=cap["id"])
    req = storage.list_capability_requirements(project_id=p["id"])[0]
    assert req["project_name"] == "Launch"
    assert req["capability_name"] == "Spanish"
    assert req["resource_name"] is None


def test_suggestion_anchor_labels(storage: Storage):
    p = storage.write("project", {"name": "Anchored proj"})
    storage.write("suggestion", {
        "kind": "opportunity", "content": "do X", "rationale": "why",
        "linked_evidence": {
            "internal_anchors": [
                {"entity_type": "project", "entity_id": p["id"], "why_anchored": "t"}
            ],
            "external_sources": [
                {"url": "http://e.com", "claim_supported": "c", "accessed_at": "2026-01-01"}
            ],
        },
    })
    anchors = storage.list_pending_suggestions()[0]["linked_evidence"]["internal_anchors"]
    assert anchors[0]["label"] == "Anchored proj"
    # also via state_query
    qa = storage.query("suggestion")[0]["linked_evidence"]["internal_anchors"]
    assert qa[0]["label"] == "Anchored proj"


def test_labels_are_fresh_after_rename(storage: Storage):
    """The whole point of resolving at read time: a rename is reflected
    immediately, with no stale denormalized copy anywhere."""
    p = storage.write("project", {"name": "Old Name"})
    storage.write("decision", {"what": "d", "rationale": "r", "project_id": p["id"]})
    assert storage.query("decision")[0]["project_name"] == "Old Name"
    storage.update("project", p["id"], {"name": "New Name"})
    assert storage.query("decision")[0]["project_name"] == "New Name"


def test_long_label_is_clipped(storage: Storage):
    long_what = "x" * 200
    p = storage.write("project", {"name": "P"})
    dec = storage.write("decision", {"what": long_what, "rationale": "r"})
    p2 = storage.write("principle", {"name": "Pr", "description": "d", "rank": 1})
    storage.record_principle_evaluation(dec["id"], p2["id"], "unjustified_departure",
                                        rationale="x")
    ev = storage.list_principle_departures()[0]
    assert len(ev["decision_what"]) <= 80
    assert ev["decision_what"].endswith("…")
