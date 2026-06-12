"""Tests for the v3 strategic substrate: SLUs, assessments, observations."""

import pytest

from meta_assistant.storage import (
    SEED_LIFE_UNITS,
    Storage,
    VALID_QUADRANTS,
    VALID_SLA,
)


# --- pre-seed ---------------------------------------------------------------


def test_fresh_db_seeds_16_slus(tmp_path):
    storage = Storage(tmp_path / "seed.db")
    units = storage.list_active_slus()
    assert len(units) == 16
    by_area = {}
    for u in units:
        by_area.setdefault(u["area"], []).append(u["name"])
    assert set(by_area.keys()) == VALID_SLA
    # Strack's distribution: 3+3+2+3+3+2
    counts = {k: len(v) for k, v in by_area.items()}
    assert counts == {
        "relationships": 3,
        "body_mind_spirit": 3,
        "community_society": 2,
        "job_learning_finances": 3,
        "interests_entertainment": 3,
        "personal_care": 2,
    }
    # all start active, none custom
    assert all(u["active"] is True for u in units)
    assert all(u["custom"] is False for u in units)
    # names match the seed list exactly
    assert {u["name"] for u in units} == {n for (n, _, _) in SEED_LIFE_UNITS}


def test_seed_is_idempotent(tmp_path):
    db = tmp_path / "seed2.db"
    Storage(db)
    Storage(db)  # reopen — must not double-seed
    assert len(Storage(db).list_active_slus()) == 16


def test_list_active_slus_filter_by_area(storage: Storage):
    rel = storage.list_active_slus(area="relationships")
    assert len(rel) == 3
    assert all(u["area"] == "relationships" for u in rel)


def test_list_active_slus_rejects_bad_area(storage: Storage):
    with pytest.raises(ValueError, match="area"):
        storage.list_active_slus(area="vibes")


# --- (de)activation ---------------------------------------------------------


def test_deactivate_hides_from_active_list(storage: Storage):
    sig = next(u for u in storage.list_active_slus() if u["name"] == "Significant other")
    storage.deactivate_slu(sig["id"], reason="single right now")
    names = {u["name"] for u in storage.list_active_slus()}
    assert "Significant other" not in names
    # still present overall
    all_names = {u["name"] for u in storage.list_life_units()}
    assert "Significant other" in all_names


def test_activate_reverses_deactivate(storage: Storage):
    sig = next(u for u in storage.list_active_slus() if u["name"] == "Significant other")
    storage.deactivate_slu(sig["id"])
    storage.activate_slu(sig["id"])
    names = {u["name"] for u in storage.list_active_slus()}
    assert "Significant other" in names


# --- quadrant computation ---------------------------------------------------


@pytest.mark.parametrize(
    "imp,sat,expected",
    [
        (6, 6, "upper_right"),  # boundary at the threshold
        (10, 10, "upper_right"),
        (6, 5, "upper_left"),  # high imp, low sat
        (8, 0, "upper_left"),
        (5, 6, "lower_right"),
        (0, 10, "lower_right"),
        (5, 5, "lower_left"),  # just below threshold both
        (0, 0, "lower_left"),
    ],
)
def test_quadrant_at_boundaries(imp, sat, expected, storage: Storage):
    session = storage.start_assessment_session("slu_portfolio")
    slu = storage.list_active_slus()[0]
    row = storage.record_slu_rating(
        session["session_id"], slu["id"], imp, sat, hours_per_week=1.0
    )
    assert row["quadrant"] == expected


def test_record_slu_rating_validates_range(storage: Storage):
    session = storage.start_assessment_session("slu_portfolio")
    slu = storage.list_active_slus()[0]
    for bad in (-1, 11, "five", 5.5):
        with pytest.raises(ValueError):
            storage.record_slu_rating(session["session_id"], slu["id"], bad, 5)
        with pytest.raises(ValueError):
            storage.record_slu_rating(session["session_id"], slu["id"], 5, bad)


def test_record_slu_rating_requires_known_slu(storage: Storage):
    session = storage.start_assessment_session("slu_portfolio")
    with pytest.raises(ValueError, match="life_unit"):
        storage.record_slu_rating(session["session_id"], "no-such-id", 5, 5)


# --- PERMA-V intake ---------------------------------------------------------


def test_record_perma_rating_happy_path(storage: Storage):
    session = storage.start_assessment_session("great_life")
    row = storage.record_perma_rating(
        session["session_id"], "meaning", 9, 5, notes="want more of this"
    )
    assert row["dimension"] == "meaning"
    assert row["importance"] == 9
    assert row["satisfaction"] == 5


def test_record_perma_rating_rejects_bad_dimension(storage: Storage):
    session = storage.start_assessment_session("great_life")
    with pytest.raises(ValueError, match="dimension"):
        storage.record_perma_rating(session["session_id"], "vibes", 5, 5)


def test_start_assessment_session_validates_type(storage: Storage):
    with pytest.raises(ValueError, match="type"):
        storage.start_assessment_session("vibes")


# --- compute_portfolio_quadrants --------------------------------------------


def _rate_all(storage: Storage, session_id: str, ratings: dict) -> None:
    """ratings is a dict mapping slu-name -> (importance, satisfaction)."""
    by_name = {u["name"]: u for u in storage.list_active_slus()}
    for name, (imp, sat) in ratings.items():
        storage.record_slu_rating(session_id, by_name[name]["id"], imp, sat, 1.0)


def test_compute_portfolio_quadrants_groups_correctly(storage: Storage):
    session = storage.start_assessment_session("slu_portfolio")
    _rate_all(
        storage,
        session["session_id"],
        {
            "Significant other": (9, 3),  # upper_left
            "Family": (8, 8),  # upper_right
            "Online entertainment": (2, 7),  # lower_right
            "Spirituality": (3, 3),  # lower_left
        },
    )
    result = storage.compute_portfolio_quadrants(session["session_id"])
    assert result["session_id"] == session["session_id"]
    names_in = lambda q: {r["slu_name"] for r in result["quadrants"][q]}
    assert names_in("upper_left") == {"Significant other"}
    assert names_in("upper_right") == {"Family"}
    assert names_in("lower_right") == {"Online entertainment"}
    assert names_in("lower_left") == {"Spirituality"}


def test_compute_portfolio_quadrants_picks_most_recent_session(storage: Storage):
    sa = storage.start_assessment_session("slu_portfolio")
    _rate_all(storage, sa["session_id"], {"Family": (8, 3)})
    sb = storage.start_assessment_session("slu_portfolio")
    _rate_all(storage, sb["session_id"], {"Family": (8, 8)})
    auto = storage.compute_portfolio_quadrants()
    assert auto["session_id"] == sb["session_id"]
    assert auto["quadrants"]["upper_right"][0]["slu_name"] == "Family"


def test_compute_portfolio_quadrants_empty(storage: Storage):
    out = storage.compute_portfolio_quadrants()
    assert out["session_id"] is None
    assert all(out["quadrants"][q] == [] for q in VALID_QUADRANTS)


# --- entity_slu_links + capture flow integration ----------------------------


def test_link_entity_to_slu_validates(storage: Storage):
    project = storage.write("project", {"name": "x"})
    slu = storage.list_active_slus()[0]
    link = storage.link_entity_to_slu("project", project["id"], slu["id"])
    assert link["entity_type"] == "project"
    assert link["life_unit_id"] == slu["id"]
    with pytest.raises(ValueError, match="entity_type"):
        storage.link_entity_to_slu("vibes", project["id"], slu["id"])
    with pytest.raises(ValueError, match="life_unit"):
        storage.link_entity_to_slu("project", project["id"], "no-such-id")


def test_write_decision_with_slu_links_creates_links(storage: Storage):
    project = storage.write("project", {"name": "host"})
    health = next(u for u in storage.list_active_slus() if u["name"] == "Physical health")
    partner = next(
        u for u in storage.list_active_slus() if u["name"] == "Significant other"
    )
    decision = storage.write(
        "decision",
        {
            "project_id": project["id"],
            "what": "Sunday jogs together",
            "rationale": "ties physical activity to time with partner",
            "slu_links": [health["id"], partner["id"]],
        },
    )
    links = storage.list_entity_slu_links(
        entity_type="decision", entity_id=decision["id"]
    )
    assert {l["life_unit_id"] for l in links} == {health["id"], partner["id"]}
    # decision row itself has no slu_links column polluting it
    assert "slu_links" not in decision


def test_capture_flow_with_slu_links_commits_links(storage: Storage):
    """Propose a decision with slu_links → commit → links land on the entity."""
    project = storage.write("project", {"name": "host"})
    health = next(u for u in storage.list_active_slus() if u["name"] == "Physical health")
    prop = storage.create_draft(
        "decision",
        {
            "project_id": project["id"],
            "what": "morning runs",
            "rationale": "energy + headspace",
            "slu_links": [health["id"]],
        },
        session_id="cap-1",
    )
    entity = storage.commit_draft(prop["id"])
    links = storage.list_entity_slu_links(
        entity_type="decision", entity_id=entity["id"]
    )
    assert [l["life_unit_id"] for l in links] == [health["id"]]


# --- observations -----------------------------------------------------------


def test_write_observation_happy_path(storage: Storage):
    slu = storage.list_active_slus()[0]
    obs = storage.write_observation(
        slu["id"],
        signal="silence",
        severity="medium",
        evidence={
            "importance": 8,
            "tagged_activity_days": 0,
            "lookback_days": 90,
        },
        notes="nothing tagged in 90 days",
    )
    assert obs["signal"] == "silence"
    assert obs["severity"] == "medium"
    assert obs["evidence"]["importance"] == 8


def test_write_observation_validates(storage: Storage):
    slu = storage.list_active_slus()[0]
    with pytest.raises(ValueError, match="signal"):
        storage.write_observation(slu["id"], "vibes", "low", {})
    with pytest.raises(ValueError, match="severity"):
        storage.write_observation(slu["id"], "silence", "extreme", {})
    with pytest.raises(ValueError, match="evidence"):
        storage.write_observation(slu["id"], "silence", "low", "not-a-dict")
    with pytest.raises(ValueError, match="life_unit"):
        storage.write_observation("no-such-id", "silence", "low", {})


def test_list_observations_filters(storage: Storage):
    a, b = storage.list_active_slus()[:2]
    storage.write_observation(a["id"], "silence", "high", {"why": "test"})
    storage.write_observation(b["id"], "erosion", "medium", {"why": "test"})
    high = storage.list_observations(severity="high")
    assert len(high) == 1 and high[0]["signal"] == "silence"
    for_a = storage.list_observations(life_unit_id=a["id"])
    assert len(for_a) == 1 and for_a[0]["life_unit_id"] == a["id"]


# --- trajectory: quadrant_shift detection plumbing --------------------------


def test_trajectory_quadrant_shift_pattern(storage: Storage):
    """Two assessments showing upper-right → upper-left drift. We verify the
    raw data the scanner needs is queryable: prior assessment in upper_right,
    new one in upper_left for the same SLU. The pattern matching itself
    lives in the scan_life_units skill (LLM); this test confirms the
    storage layer makes the call easy."""
    family = next(u for u in storage.list_active_slus() if u["name"] == "Family")

    s1 = storage.start_assessment_session("slu_portfolio")
    storage.record_slu_rating(s1["session_id"], family["id"], 8, 8, 10.0)

    s2 = storage.start_assessment_session("slu_portfolio")
    storage.record_slu_rating(s2["session_id"], family["id"], 8, 4, 4.0)

    history = storage.list_assessments(life_unit_id=family["id"])
    assert len(history) == 2
    # newest first
    assert history[0]["quadrant"] == "upper_left"
    assert history[1]["quadrant"] == "upper_right"

    # the scanner would write this observation
    obs = storage.write_observation(
        family["id"],
        signal="quadrant_shift",
        severity="high",
        evidence={
            "from": "upper_right",
            "to": "upper_left",
            "prior_assessment_id": history[1]["id"],
            "current_assessment_id": history[0]["id"],
        },
        notes="moved between the last two assessments",
    )

    # severity=high promotes to an suggestion
    proposal = storage.write(
        "suggestion",
        {
            "kind": "blindspot",
            "content": (
                "Family has moved from upper-right to upper-left between "
                "your last two assessments. Worth checking what changed."
            ),
            "rationale": "quadrant_shift observation severity=high",
            "linked_evidence": {
                "internal_anchors": [
                    {
                        "entity_type": "life_unit",
                        "entity_id": family["id"],
                        "why_anchored": "the SLU whose quadrant shifted",
                    },
                    {
                        "entity_type": "slu_observation",
                        "entity_id": obs["id"],
                        "why_anchored": "the observation that triggered this",
                    },
                ],
                "external_sources": [],
            },
        },
    )
    assert proposal["status"] == "pending"
    assert proposal["kind"] == "blindspot"


# --- end-to-end: intake → tagged capture → scan plumbing --------------------


def test_e2e_intake_capture_scan(tmp_path):
    storage = Storage(tmp_path / "v3_e2e.db")

    # 1. PERMA-V intake
    gl = storage.start_assessment_session("great_life")
    for dim, imp, sat in [
        ("positive_emotions", 7, 6),
        ("engagement", 8, 7),
        ("relationships", 10, 4),  # large gap → priority signal
        ("meaning", 9, 6),
        ("achievement", 6, 7),
        ("vitality", 8, 5),
    ]:
        storage.record_perma_rating(gl["session_id"], dim, imp, sat)

    # 2. Portfolio assessment
    pa = storage.start_assessment_session("slu_portfolio")
    by_name = {u["name"]: u for u in storage.list_active_slus()}
    storage.record_slu_rating(pa["session_id"], by_name["Significant other"]["id"], 9, 3, 2.0)
    storage.record_slu_rating(pa["session_id"], by_name["Family"]["id"], 8, 8, 10.0)
    storage.record_slu_rating(pa["session_id"], by_name["Job and career"]["id"], 7, 7, 45.0)

    quadrants = storage.compute_portfolio_quadrants(pa["session_id"])
    assert {r["slu_name"] for r in quadrants["quadrants"]["upper_left"]} == {
        "Significant other"
    }

    # 3. A capture: decision tagged with the high-importance, low-sat SLU
    decision = storage.write(
        "decision",
        {
            "what": "Block Wednesday evenings for partner time",
            "rationale": "address the upper-left finding directly",
            "slu_links": [by_name["Significant other"]["id"]],
        },
    )

    # 4. Scanner: writes an observation. Here we simulate the scanner's
    #    pattern detection — the storage layer accepts and stores it.
    obs = storage.write_observation(
        by_name["Significant other"]["id"],
        signal="mismatch",
        severity="high",
        evidence={
            "importance": 9,
            "tagged_activity_share": 0.05,
            "decision_ids": [decision["id"]],
        },
        notes="high-importance SLU but only one tagged decision in the period",
    )

    # 5. high-severity observation gets promoted to a blindspot proposal
    proposal = storage.write(
        "suggestion",
        {
            "kind": "blindspot",
            "content": (
                "Significant other is your highest-importance SLU but tagged "
                "activity for it is in the bottom third. Worth a check-in."
            ),
            "rationale": "high-severity mismatch observation",
            "linked_evidence": {
                "internal_anchors": [
                    {
                        "entity_type": "life_unit",
                        "entity_id": by_name["Significant other"]["id"],
                        "why_anchored": "the SLU under examination",
                    },
                    {
                        "entity_type": "slu_observation",
                        "entity_id": obs["id"],
                        "why_anchored": "the observation that triggered this",
                    },
                ],
                "external_sources": [],
            },
            "session_id": "scan-1",
        },
    )

    # 6. Inbox flow disposition
    pending = storage.list_pending_suggestions()
    assert proposal["id"] in {p["id"] for p in pending}
    dispositioned = storage.disposition_suggestion(
        proposal["id"], "accepted", "yes, this is what the data is telling me"
    )
    assert dispositioned["status"] == "accepted"


# --- server smoke -----------------------------------------------------------


def test_server_registers_v3_tools_and_resources(tmp_path):
    from meta_assistant.server import build_server

    skills_dir = tmp_path / "skills"
    schemas_dir = tmp_path / "schemas"
    constitution = tmp_path / "constitution.md"
    skills_dir.mkdir()
    schemas_dir.mkdir()
    for name in ("define_great_life", "assess_life_portfolio", "scan_life_units"):
        (skills_dir / f"{name}.md").write_text(f"# {name}")
    for name in ("life_units", "slu_assessments", "great_life_dimensions"):
        (schemas_dir / f"{name}.md").write_text(f"# {name}")
    constitution.write_text("hi")

    mcp, _ = build_server(
        db_path=str(tmp_path / "v3.db"),
        skills_dir=skills_dir,
        schemas_dir=schemas_dir,
        constitution_path=constitution,
    )

    tool_names = {t.name for t in mcp._tool_manager.list_tools()}
    expected = {
        "state_list_active_slus",
        "state_start_assessment_session",
        "state_record_slu_rating",
        "state_record_perma_rating",
        "state_compute_portfolio_quadrants",
        "state_link_entity_to_slu",
        "state_list_assessments",
        "state_write_observation",
        "state_list_observations",
        "state_deactivate_slu",
        "state_activate_slu",
    }
    assert expected <= tool_names

    resource_uris = {str(r.uri) for r in mcp._resource_manager.list_resources()}
    assert "skill://define_great_life" in resource_uris
    assert "skill://assess_life_portfolio" in resource_uris
    assert "skill://scan_life_units" in resource_uris
    assert "schema://life_units" in resource_uris
    assert "schema://slu_assessments" in resource_uris
    assert "schema://great_life_dimensions" in resource_uris
