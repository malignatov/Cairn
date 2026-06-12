"""Tests for v4: capabilities, resources, capability_requirements.

Covers per-entity create + validation (including the level/category
compatibility rule and the exactly-one-of capability_id/resource_id
rule), the five scanner-relevant helper queries, the level-aware
satisfaction logic, mark_capability_exercised, delete restricted to
resources, and the polymorphic write/propose/commit paths.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from meta_assistant.storage import Storage


# --- capability creation + validation ---------------------------------------


def test_create_capability_happy_path(storage: Storage):
    cap = storage.write(
        "capability",
        {
            "name": "Mandarin conversational",
            "category": "skill",
            "level": "intermediate",
            "acquired_at": "2018-06-01",
            "notes": "rusty",
        },
    )
    assert cap["id"]
    assert cap["status"] == "active"
    assert cap["level"] == "intermediate"
    assert cap["renewal_required"] is False
    assert cap["renewal_lead_time_days"] == 90


def test_create_capability_requires_name_and_category(storage: Storage):
    with pytest.raises(ValueError, match="name"):
        storage.write("capability", {"category": "skill"})
    with pytest.raises(ValueError, match="category"):
        storage.write("capability", {"name": "anything"})


def test_create_capability_validates_category_enum(storage: Storage):
    with pytest.raises(ValueError, match="category"):
        storage.write("capability", {"name": "X", "category": "thingy"})


def test_create_capability_rejects_level_on_hardware(storage: Storage):
    """Hardware doesn't have novice/expert grades — that's nonsensical."""
    with pytest.raises(ValueError, match="level"):
        storage.write(
            "capability",
            {"name": "Camera kit", "category": "hardware", "level": "expert"},
        )


def test_create_capability_rejects_level_on_license(storage: Storage):
    """Licenses are binary — you have one or you don't."""
    with pytest.raises(ValueError, match="level"):
        storage.write(
            "capability",
            {"name": "Driver's license", "category": "license",
             "level": "intermediate"},
        )


def test_create_capability_rejects_level_on_certification(storage: Storage):
    with pytest.raises(ValueError, match="level"):
        storage.write(
            "capability",
            {"name": "AWS SAA", "category": "certification",
             "level": "advanced"},
        )


def test_create_capability_allows_level_on_skill_credential_other(storage: Storage):
    for cat in ("skill", "credential", "other"):
        cap = storage.write(
            "capability",
            {"name": f"thing-{cat}", "category": cat, "level": "advanced"},
        )
        assert cap["level"] == "advanced"


def test_create_capability_validates_level_enum(storage: Storage):
    with pytest.raises(ValueError, match="level"):
        storage.write(
            "capability",
            {"name": "X", "category": "skill", "level": "guru"},
        )


def test_create_capability_validates_status_enum(storage: Storage):
    with pytest.raises(ValueError, match="status"):
        storage.write(
            "capability",
            {"name": "X", "category": "skill", "status": "ghost"},
        )


def test_create_capability_renewal_required_boolean_handling(storage: Storage):
    cap = storage.write(
        "capability",
        {"name": "Passport", "category": "license", "renewal_required": True,
         "expires_at": "2031-05-01"},
    )
    # SQLite stores as 0/1; ensure callers see a real bool
    assert cap["renewal_required"] is True
    [back] = storage.query("capability", {"id": cap["id"]})
    # query returns the raw row — boolean exposure is by the create/list
    # APIs that normalize; the raw row may be int. Both are acceptable
    # as long as truth value matches.
    assert bool(back["renewal_required"]) is True


# --- update path level/category constraint ----------------------------------


def test_update_capability_rejects_level_when_category_incompatible(
    storage: Storage,
):
    """If you patch a level onto a row whose stored category is hardware,
    the storage layer should still reject it — the constraint is on
    the effective category, not just on inserts."""
    cap = storage.write(
        "capability", {"name": "Camera", "category": "hardware"},
    )
    with pytest.raises(ValueError, match="level"):
        storage.update("capability", cap["id"], {"level": "expert"})


def test_update_capability_status_validates(storage: Storage):
    cap = storage.write(
        "capability", {"name": "X", "category": "skill"},
    )
    with pytest.raises(ValueError, match="status"):
        storage.update("capability", cap["id"], {"status": "ghost"})
    updated = storage.update("capability", cap["id"], {"status": "lapsed"})
    assert updated["status"] == "lapsed"


# --- resource creation + validation -----------------------------------------


def test_create_resource_happy_path(storage: Storage):
    r = storage.write(
        "resource",
        {
            "name": "Cash runway",
            "category": "money",
            "unit": "months",
            "current_quantity": 8.0,
            "floor": 4.0,
            "burn_rate": 1.0,
            "replenish_rate": 0.5,
        },
    )
    assert r["id"]
    assert r["current_quantity"] == 8.0
    assert r["as_of"]  # defaulted to now


def test_create_resource_requires_name_category_unit_quantity(storage: Storage):
    with pytest.raises(ValueError, match="name"):
        storage.write("resource", {"category": "money", "unit": "EUR",
                                   "current_quantity": 1})
    with pytest.raises(ValueError, match="category"):
        storage.write("resource", {"name": "x", "unit": "EUR",
                                   "current_quantity": 1})
    with pytest.raises(ValueError, match="unit"):
        storage.write("resource", {"name": "x", "category": "money",
                                   "current_quantity": 1})
    with pytest.raises(ValueError, match="current_quantity"):
        storage.write("resource", {"name": "x", "category": "money",
                                   "unit": "EUR"})


def test_create_resource_validates_category(storage: Storage):
    with pytest.raises(ValueError, match="category"):
        storage.write(
            "resource",
            {"name": "x", "category": "stuff", "unit": "u",
             "current_quantity": 1},
        )


def test_create_resource_quantity_must_be_number(storage: Storage):
    with pytest.raises(ValueError, match="current_quantity"):
        storage.write(
            "resource",
            {"name": "x", "category": "money", "unit": "EUR",
             "current_quantity": "lots"},
        )


def test_update_resource_quantity_and_as_of_move_together(storage: Storage):
    r = storage.write(
        "resource",
        {"name": "x", "category": "money", "unit": "EUR",
         "current_quantity": 10},
    )
    # Either both or neither — half is rejected
    with pytest.raises(ValueError, match="current_quantity and resource.as_of"):
        storage.update("resource", r["id"], {"current_quantity": 5})
    with pytest.raises(ValueError, match="current_quantity and resource.as_of"):
        storage.update("resource", r["id"], {"as_of": "2026-01-01"})
    # Both together is fine
    updated = storage.update(
        "resource", r["id"],
        {"current_quantity": 5.0, "as_of": "2026-01-01T00:00:00Z"},
    )
    assert updated["current_quantity"] == 5.0
    assert updated["as_of"] == "2026-01-01T00:00:00Z"


# --- delete restricted to resources -----------------------------------------


def test_delete_only_allowed_for_resources(storage: Storage):
    p = storage.write("project", {"name": "x"})
    with pytest.raises(ValueError, match="delete is only allowed for resources"):
        storage.delete("project", p["id"])
    cap = storage.write("capability", {"name": "x", "category": "skill"})
    with pytest.raises(ValueError, match="delete is only allowed for resources"):
        storage.delete("capability", cap["id"])


def test_delete_resource_works(storage: Storage):
    r = storage.write(
        "resource",
        {"name": "ephemeral", "category": "other", "unit": "x",
         "current_quantity": 1},
    )
    deleted = storage.delete("resource", r["id"])
    assert deleted["id"] == r["id"]
    assert storage.query("resource", {"id": r["id"]}) == []


def test_delete_resource_missing(storage: Storage):
    with pytest.raises(ValueError, match="not found"):
        storage.delete("resource", "no-such-id")


# --- list_active_capabilities -----------------------------------------------


def test_list_active_capabilities_filters_status_and_category(storage: Storage):
    active = storage.write("capability", {"name": "A", "category": "skill"})
    storage.write("capability", {"name": "B", "category": "skill",
                                  "status": "lapsed"})
    hw = storage.write("capability", {"name": "C", "category": "hardware"})

    all_active = storage.list_active_capabilities()
    names = {c["name"] for c in all_active}
    assert "A" in names and "C" in names and "B" not in names

    skills_only = storage.list_active_capabilities(category="skill")
    assert {c["name"] for c in skills_only} == {"A"}


def test_list_active_capabilities_rejects_bad_category(storage: Storage):
    with pytest.raises(ValueError, match="category"):
        storage.list_active_capabilities(category="thingy")


# --- list_expiring_capabilities ---------------------------------------------


def test_list_expiring_capabilities_window_and_sort(storage: Storage):
    now = datetime.now(timezone.utc)

    def iso_in(days):
        return (now + timedelta(days=days)).isoformat()

    storage.write("capability", {"name": "soon", "category": "license",
                                  "expires_at": iso_in(10)})
    storage.write("capability", {"name": "mid", "category": "license",
                                  "expires_at": iso_in(60)})
    storage.write("capability", {"name": "far", "category": "license",
                                  "expires_at": iso_in(500)})
    storage.write("capability", {"name": "no-expiry", "category": "skill"})

    within_90 = storage.list_expiring_capabilities(90)
    assert [c["name"] for c in within_90] == ["soon", "mid"]

    within_30 = storage.list_expiring_capabilities(30)
    assert [c["name"] for c in within_30] == ["soon"]


def test_list_expiring_excludes_lapsed_and_retired(storage: Storage):
    """An expired license that's already marked lapsed shouldn't be
    flagged for renewal — it's already past tense."""
    soon = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
    storage.write("capability", {"name": "active", "category": "license",
                                  "expires_at": soon})
    storage.write("capability", {"name": "lapsed", "category": "license",
                                  "expires_at": soon, "status": "lapsed"})
    rows = storage.list_expiring_capabilities(90)
    assert {c["name"] for c in rows} == {"active"}


def test_list_expiring_validates_window(storage: Storage):
    with pytest.raises(ValueError, match="within_days"):
        storage.list_expiring_capabilities(-1)


# --- list_atrophying_capabilities -------------------------------------------


def test_atrophy_only_fires_for_atrophying_categories(storage: Storage):
    """License, credential, hardware, other → don't atrophy. Skill,
    certification, access → can atrophy. Brief says atrophy fires only
    for skill/certification/access."""
    long_ago = (datetime.now(timezone.utc) - timedelta(days=500)).isoformat()
    # All have last_exercised_at ages ago
    storage.write("capability", {"name": "old_skill", "category": "skill",
                                  "last_exercised_at": long_ago})
    storage.write("capability", {"name": "old_cert", "category": "certification",
                                  "last_exercised_at": long_ago})
    storage.write("capability", {"name": "old_access", "category": "access",
                                  "last_exercised_at": long_ago})
    storage.write("capability", {"name": "old_license", "category": "license",
                                  "last_exercised_at": long_ago})
    storage.write("capability", {"name": "old_hw", "category": "hardware",
                                  "last_exercised_at": long_ago})
    storage.write("capability", {"name": "old_credential",
                                  "category": "credential",
                                  "last_exercised_at": long_ago})

    rows = storage.list_atrophying_capabilities(months_silent=12)
    names = {c["name"] for c in rows}
    assert names == {"old_skill", "old_cert", "old_access"}


def test_atrophy_includes_never_exercised_old_capabilities(storage: Storage):
    """A skill created 18 months ago that was never marked as exercised
    counts as atrophying — but one created yesterday doesn't (we'd flag
    every fresh capture)."""
    import sqlite3
    long_ago = (datetime.now(timezone.utc) - timedelta(days=600)).isoformat()
    recent = storage.write("capability", {"name": "fresh", "category": "skill"})
    # Backdate the second skill's created_at via raw SQL — the create
    # path sets it to now and we need an old one for this case.
    old = storage.write("capability", {"name": "old_unused", "category": "skill"})
    with sqlite3.connect(storage.db_path) as conn:
        conn.execute(
            "UPDATE capabilities SET created_at = ? WHERE id = ?",
            (long_ago, old["id"]),
        )

    rows = storage.list_atrophying_capabilities(months_silent=12)
    names = {c["name"] for c in rows}
    assert "old_unused" in names
    assert "fresh" not in names


# --- mark_capability_exercised ----------------------------------------------


def test_mark_capability_exercised_updates_timestamp(storage: Storage):
    cap = storage.write("capability", {"name": "Python", "category": "skill"})
    assert cap["last_exercised_at"] is None
    updated = storage.mark_capability_exercised(cap["id"])
    assert updated["last_exercised_at"]


def test_mark_capability_exercised_with_explicit_when(storage: Storage):
    cap = storage.write("capability", {"name": "Python", "category": "skill"})
    when = "2026-04-01T10:00:00+00:00"
    updated = storage.mark_capability_exercised(cap["id"], when=when)
    assert updated["last_exercised_at"] == when


def test_mark_capability_exercised_missing(storage: Storage):
    with pytest.raises(ValueError, match="not found"):
        storage.mark_capability_exercised("nope")


def test_mark_capability_exercised_removes_atrophy(storage: Storage):
    """Exercising an atrophying capability should clear it from the list."""
    long_ago = (datetime.now(timezone.utc) - timedelta(days=500)).isoformat()
    cap = storage.write("capability", {"name": "X", "category": "skill",
                                        "last_exercised_at": long_ago})
    assert cap["id"] in {c["id"] for c in storage.list_atrophying_capabilities()}
    storage.mark_capability_exercised(cap["id"])
    assert cap["id"] not in {c["id"] for c in storage.list_atrophying_capabilities()}


# --- resource floor + runway -------------------------------------------------


def test_list_resources_below_floor(storage: Storage):
    storage.write("resource", {"name": "ok", "category": "money", "unit": "m",
                                "current_quantity": 8, "floor": 4})
    below = storage.write(
        "resource",
        {"name": "low", "category": "money", "unit": "m",
         "current_quantity": 3, "floor": 4},
    )
    storage.write("resource", {"name": "no_floor", "category": "time",
                                "unit": "h", "current_quantity": 1})
    rows = storage.list_resources_below_floor()
    assert {r["id"] for r in rows} == {below["id"]}


def test_compute_runway_with_net_burn(storage: Storage):
    r = storage.write(
        "resource",
        {"name": "Cash", "category": "money", "unit": "EUR",
         "current_quantity": 10000, "burn_rate": 1000, "replenish_rate": 500},
    )
    out = storage.compute_resource_runway()
    [entry] = [e for e in out if e["resource_id"] == r["id"]]
    # net = 1000 - 500 = 500; runway = 10000 / 500 = 20 months
    assert entry["runway_months"] == 20.0


def test_compute_runway_no_burn_rate_returns_null(storage: Storage):
    r = storage.write(
        "resource",
        {"name": "Hours", "category": "time", "unit": "h",
         "current_quantity": 40},
    )
    [entry] = [e for e in storage.compute_resource_runway()
               if e["resource_id"] == r["id"]]
    assert entry["runway_months"] is None


def test_compute_runway_replenish_exceeds_burn_returns_null(storage: Storage):
    """If money's coming in faster than it's going out, there's no
    runway to count down — the math is undefined."""
    r = storage.write(
        "resource",
        {"name": "Growing", "category": "money", "unit": "EUR",
         "current_quantity": 1000, "burn_rate": 100, "replenish_rate": 200},
    )
    [entry] = [e for e in storage.compute_resource_runway()
               if e["resource_id"] == r["id"]]
    assert entry["runway_months"] is None


# --- capability_requirements + gaps -----------------------------------------


def test_add_capability_requirement_happy_path(storage: Storage):
    p = storage.write("project", {"name": "ship the drone reel"})
    cap = storage.write("capability", {"name": "drone license", "category": "license"})
    req = storage.add_capability_requirement(p["id"], capability_id=cap["id"])
    assert req["id"] and req["project_id"] == p["id"]
    assert req["capability_id"] == cap["id"]
    assert req["resource_id"] is None


def test_add_requirement_rejects_neither_or_both(storage: Storage):
    p = storage.write("project", {"name": "x"})
    cap = storage.write("capability", {"name": "c", "category": "skill"})
    r = storage.write("resource", {"name": "r", "category": "money",
                                    "unit": "EUR", "current_quantity": 1})
    with pytest.raises(ValueError, match="exactly one"):
        storage.add_capability_requirement(p["id"])
    with pytest.raises(ValueError, match="exactly one"):
        storage.add_capability_requirement(
            p["id"], capability_id=cap["id"], resource_id=r["id"],
        )


def test_add_requirement_level_only_with_capability(storage: Storage):
    p = storage.write("project", {"name": "x"})
    r = storage.write("resource", {"name": "r", "category": "money",
                                    "unit": "EUR", "current_quantity": 1})
    with pytest.raises(ValueError, match="required_level"):
        storage.add_capability_requirement(
            p["id"], resource_id=r["id"], required_level="advanced",
        )


def test_add_requirement_amount_only_with_resource(storage: Storage):
    p = storage.write("project", {"name": "x"})
    cap = storage.write("capability", {"name": "c", "category": "skill"})
    with pytest.raises(ValueError, match="required_amount"):
        storage.add_capability_requirement(
            p["id"], capability_id=cap["id"], required_amount=100,
        )


def test_add_requirement_validates_fk_existence(storage: Storage):
    p = storage.write("project", {"name": "x"})
    cap = storage.write("capability", {"name": "c", "category": "skill"})
    with pytest.raises(ValueError, match="project"):
        storage.add_capability_requirement("no-such", capability_id=cap["id"])
    with pytest.raises(ValueError, match="capability"):
        storage.add_capability_requirement(p["id"], capability_id="no-such")
    with pytest.raises(ValueError, match="resource"):
        storage.add_capability_requirement(p["id"], resource_id="no-such")


def test_gap_capability_missing_status(storage: Storage):
    """A requirement against a non-active capability is an unsatisfied gap."""
    p = storage.write("project", {"name": "x"})
    cap = storage.write("capability", {"name": "c", "category": "skill",
                                        "status": "lapsed"})
    storage.add_capability_requirement(p["id"], capability_id=cap["id"])
    gaps = storage.list_capability_gaps()
    assert len(gaps) == 1


def test_gap_capability_level_below_required(storage: Storage):
    """A capability at intermediate doesn't satisfy a 'expert required'."""
    p = storage.write("project", {"name": "x"})
    cap = storage.write("capability", {"name": "Python", "category": "skill",
                                        "level": "intermediate"})
    storage.add_capability_requirement(
        p["id"], capability_id=cap["id"], required_level="expert",
    )
    gaps = storage.list_capability_gaps()
    assert len(gaps) == 1


def test_gap_capability_level_meets_required(storage: Storage):
    p = storage.write("project", {"name": "x"})
    cap = storage.write("capability", {"name": "Python", "category": "skill",
                                        "level": "advanced"})
    storage.add_capability_requirement(
        p["id"], capability_id=cap["id"], required_level="intermediate",
    )
    gaps = storage.list_capability_gaps()
    assert gaps == []


def test_gap_resource_below_required_amount(storage: Storage):
    p = storage.write("project", {"name": "x"})
    r = storage.write("resource", {"name": "Cash", "category": "money",
                                    "unit": "EUR", "current_quantity": 1000})
    storage.add_capability_requirement(
        p["id"], resource_id=r["id"], required_amount=5000,
    )
    gaps = storage.list_capability_gaps()
    assert len(gaps) == 1


def test_gap_resource_meets_required_amount(storage: Storage):
    p = storage.write("project", {"name": "x"})
    r = storage.write("resource", {"name": "Cash", "category": "money",
                                    "unit": "EUR", "current_quantity": 5000})
    storage.add_capability_requirement(
        p["id"], resource_id=r["id"], required_amount=1000,
    )
    assert storage.list_capability_gaps() == []


def test_gaps_default_filters_to_active_projects(storage: Storage):
    """Gaps on killed projects aren't actionable — they shouldn't surface
    in the default scan."""
    active = storage.write("project", {"name": "active"})
    killed = storage.write("project", {"name": "killed", "status": "killed"})
    cap_lapsed = storage.write("capability", {"name": "X", "category": "skill",
                                               "status": "lapsed"})
    storage.add_capability_requirement(active["id"], capability_id=cap_lapsed["id"])
    storage.add_capability_requirement(killed["id"], capability_id=cap_lapsed["id"])

    default = storage.list_capability_gaps()
    assert {g["project_id"] for g in default} == {active["id"]}

    # Explicit project filter sees both
    on_killed = storage.list_capability_gaps(project_id=killed["id"])
    assert len(on_killed) == 1


def test_remove_capability_requirement(storage: Storage):
    p = storage.write("project", {"name": "x"})
    cap = storage.write("capability", {"name": "c", "category": "skill"})
    req = storage.add_capability_requirement(p["id"], capability_id=cap["id"])
    storage.remove_capability_requirement(req["id"])
    assert storage.list_capability_requirements(project_id=p["id"]) == []
    with pytest.raises(ValueError, match="not found"):
        storage.remove_capability_requirement(req["id"])


# --- polymorphic write / query / propose ------------------------------------


def test_query_capabilities(storage: Storage):
    storage.write("capability", {"name": "X", "category": "skill"})
    rows = storage.query("capability")
    assert len(rows) == 1


def test_propose_commit_capability(storage: Storage):
    """inventory_intake uses the proposal flow for bulk import."""
    prop = storage.create_draft(
        "capability",
        {"name": "Drone license", "category": "license"},
        session_id="intake-1",
    )
    assert prop["status"] == "pending"
    entity = storage.commit_draft(prop["id"])
    assert entity["name"] == "Drone license"
    assert entity["status"] == "active"


def test_propose_commit_resource(storage: Storage):
    prop = storage.create_draft(
        "resource",
        {"name": "Runway", "category": "money", "unit": "months",
         "current_quantity": 8},
        session_id="intake-1",
    )
    entity = storage.commit_draft(prop["id"])
    assert entity["current_quantity"] == 8.0


def test_capability_and_resource_can_be_slu_tagged(storage: Storage):
    cap = storage.write("capability", {"name": "Photography", "category": "skill"})
    res = storage.write("resource", {"name": "Cash", "category": "money",
                                      "unit": "EUR", "current_quantity": 1})
    slu = storage.list_active_slus()[0]
    storage.link_entity_to_slu("capability", cap["id"], slu["id"])
    storage.link_entity_to_slu("resource", res["id"], slu["id"])
    assert len(storage.list_entity_slu_links(entity_type="capability")) == 1
    assert len(storage.list_entity_slu_links(entity_type="resource")) == 1


# --- server wiring smoke ----------------------------------------------------


def test_server_registers_v4_tools_and_resources(tmp_path):
    from meta_assistant.server import build_server

    skills_dir = tmp_path / "skills"
    schemas_dir = tmp_path / "schemas"
    guides_dir = tmp_path / "guides"
    constitution = tmp_path / "constitution.md"
    for d in (skills_dir, schemas_dir, guides_dir):
        d.mkdir()
    for s in ("inventory_intake", "scan_inventory", "update_resources"):
        (skills_dir / f"{s}.md").write_text(f"# {s}")
    for s in ("capabilities", "resources", "capability_requirements"):
        (schemas_dir / f"{s}.md").write_text(f"# {s}")
    constitution.write_text("hi")

    mcp, _ = build_server(
        db_path=str(tmp_path / "v4.db"),
        skills_dir=skills_dir,
        schemas_dir=schemas_dir,
        guides_dir=guides_dir,
        constitution_path=constitution,
    )
    tool_names = {t.name for t in mcp._tool_manager.list_tools()}
    expected = {
        "state_list_active_capabilities",
        "state_list_expiring_capabilities",
        "state_list_atrophying_capabilities",
        "state_mark_capability_exercised",
        "state_list_resources_below_floor",
        "state_compute_resource_runway",
        "state_add_capability_requirement",
        "state_remove_capability_requirement",
        "state_list_capability_requirements",
        "state_list_capability_gaps",
        "state_delete",
    }
    assert expected <= tool_names

    resource_uris = {str(r.uri) for r in mcp._resource_manager.list_resources()}
    assert "skill://inventory_intake" in resource_uris
    assert "skill://scan_inventory" in resource_uris
    assert "skill://update_resources" in resource_uris
    assert "schema://capabilities" in resource_uris
    assert "schema://resources" in resource_uris
    assert "schema://capability_requirements" in resource_uris


# --- shipped skill descriptions are actionable ------------------------------


SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


@pytest.mark.parametrize(
    "skill_name",
    ["inventory_intake", "scan_inventory", "update_resources"],
)
def test_v4_skill_has_actionable_frontmatter(skill_name):
    from meta_assistant.server import _parse_skill_description

    path = SKILLS_DIR / f"{skill_name}.md"
    desc = _parse_skill_description(path)
    assert desc is not None
    assert "Triggers" in desc
    assert "Not for" in desc


@pytest.mark.parametrize(
    "skill_name",
    ["inventory_intake", "scan_inventory", "update_resources"],
)
def test_v4_skill_references_constitution(skill_name):
    body = (SKILLS_DIR / f"{skill_name}.md").read_text()
    assert "constitution://main" in body


# --- e2e: realistic intake → scan → gap detection ---------------------------


def test_e2e_intake_to_scan_signals(tmp_path):
    """Walks: build an inventory with each scanner signal seeded, confirm
    the helper queries each return what scan_inventory expects to see."""
    storage = Storage(tmp_path / "v4_e2e.db")
    now = datetime.now(timezone.utc)

    # Expiring license (within 30 days → severity HIGH per the skill)
    license_soon = storage.write(
        "capability",
        {"name": "Drone license", "category": "license",
         "expires_at": (now + timedelta(days=20)).isoformat(),
         "renewal_required": True},
    )
    # Atrophying skill (last exercised > 12 months ago)
    skill_atrophying = storage.write(
        "capability",
        {"name": "Mandarin", "category": "skill", "level": "intermediate",
         "last_exercised_at": (now - timedelta(days=540)).isoformat()},
    )
    # Resource below floor
    resource_low = storage.write(
        "resource",
        {"name": "Cash runway", "category": "money", "unit": "months",
         "current_quantity": 3.5, "floor": 4.0,
         "burn_rate": 0.5},
    )
    # Project requiring the soon-to-expire license
    project = storage.write("project", {"name": "Coast videography"})
    req = storage.add_capability_requirement(
        project["id"], capability_id=license_soon["id"],
    )

    # All five scanner signals are observable from the helpers:
    expiring = storage.list_expiring_capabilities(90)
    assert license_soon["id"] in {c["id"] for c in expiring}

    atrophying = storage.list_atrophying_capabilities(12)
    assert skill_atrophying["id"] in {c["id"] for c in atrophying}

    below_floor = storage.list_resources_below_floor()
    assert resource_low["id"] in {r["id"] for r in below_floor}

    runway = storage.compute_resource_runway()
    entry = next(r for r in runway if r["resource_id"] == resource_low["id"])
    # 3.5 months / 0.5 per month = 7 months
    assert entry["runway_months"] == 7.0

    # No gap yet (license is active and present)
    assert storage.list_capability_gaps() == []
    # If the user lets the license lapse, gap appears
    storage.update("capability", license_soon["id"], {"status": "lapsed"})
    gaps = storage.list_capability_gaps()
    assert len(gaps) == 1 and gaps[0]["project_id"] == project["id"]
