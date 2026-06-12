"""v8: ideas — retained sparks with a maturation ladder and a derived-heat
anti-rot mechanism.

Heaviest coverage on the two pieces the handoff flags: the promote flow
(creates a project, sets the link, touches) and the someday-exclusion /
heat logic that keeps the cooling scanner patient and correct.
"""

import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from meta_assistant.storage import Storage

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


def _backdate(storage, idea_id, days):
    ts = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with sqlite3.connect(storage.db_path) as conn:
        conn.execute("UPDATE ideas SET last_touched_at=? WHERE id=?", (ts, idea_id))


# --- table creation (acceptance #2) -----------------------------------------


def test_ideas_table_created(storage: Storage):
    with sqlite3.connect(storage.db_path) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    assert "ideas" in tables


# --- capture_idea path (acceptance #3) --------------------------------------


def test_capture_idea_via_draft_flow(storage: Storage):
    """An idea floats through the draft/commit flow exactly as capture_idea
    drives it: state_draft(entity_type='idea') → commit → queryable."""
    d = storage.create_draft("idea", {"title": "Bio fuse design studio",
                                       "body": "interior fusing into exterior"})
    assert d["entity_type"] == "idea"
    pend = storage.list_drafts(status="pending")
    assert any(x["id"] == d["id"] for x in pend)
    storage.commit_draft(d["id"])
    ideas = storage.list_ideas()
    assert len(ideas) == 1
    assert ideas[0]["title"] == "Bio fuse design studio"
    assert ideas[0]["status"] == "spark"


def test_create_requires_title(storage: Storage):
    with pytest.raises(ValueError):
        storage.write("idea", {"body": "no title"})


def test_create_rejects_bad_status(storage: Storage):
    with pytest.raises(ValueError):
        storage.write("idea", {"title": "X", "status": "bogus"})


# --- heat computation (acceptance #4) ---------------------------------------


def test_heat_warm_when_fresh(storage: Storage):
    storage.write("idea", {"title": "fresh"})
    assert storage.list_ideas()[0]["heat"] == "warm"


def test_heat_neutral_in_between(storage: Storage):
    i = storage.write("idea", {"title": "mid"})
    _backdate(storage, i["id"], 90)  # ~3 months
    assert storage.list_ideas()[0]["heat"] == "neutral"


def test_heat_cooling_when_old_spark(storage: Storage):
    i = storage.write("idea", {"title": "old", "status": "exploring"})
    _backdate(storage, i["id"], 240)  # 8 months
    assert storage.list_ideas()[0]["heat"] == "cooling"


def test_someday_never_cools(storage: Storage):
    i = storage.write("idea", {"title": "parked", "status": "someday"})
    _backdate(storage, i["id"], 240)  # 8 months but deliberately parked
    got = storage.list_ideas()[0]
    assert got["status"] == "someday"
    assert got["heat"] == "neutral"  # never 'cooling'


def test_terminal_states_never_cool(storage: Storage):
    promoted = storage.write("idea", {"title": "becomes real"})
    storage.promote_idea(promoted["id"])
    _backdate(storage, promoted["id"], 300)
    released = storage.write("idea", {"title": "let go"})
    storage.release_idea(released["id"])
    _backdate(storage, released["id"], 300)
    heats = {i["title"]: i["heat"] for i in storage.list_ideas()}
    assert heats["becomes real"] != "cooling"
    assert heats["let go"] != "cooling"


# --- promote (acceptance #5) ------------------------------------------------


def test_promote_creates_linked_project_and_touches(storage: Storage):
    i = storage.write("idea", {"title": "Tea subscription",
                               "body": "monthly rare teas"})
    _backdate(storage, i["id"], 100)  # make it stale so we can see the touch
    project = storage.promote_idea(i["id"])
    assert project["name"] == "Tea subscription"
    assert project["description"] == "monthly rare teas"
    after = [x for x in storage.list_ideas() if x["id"] == i["id"]][0]
    assert after["status"] == "promoted"
    assert after["promoted_to_project_id"] == project["id"]
    assert after["heat"] == "warm"  # touched by the promotion
    # the project is a real, queryable project
    assert any(p["id"] == project["id"] for p in storage.query("project"))


def test_promote_twice_errors(storage: Storage):
    i = storage.write("idea", {"title": "once"})
    storage.promote_idea(i["id"])
    with pytest.raises(ValueError):
        storage.promote_idea(i["id"])


# --- release (acceptance #6) ------------------------------------------------


def test_release_sets_reason_and_touches(storage: Storage):
    i = storage.write("idea", {"title": "drop me"})
    _backdate(storage, i["id"], 100)
    r = storage.release_idea(i["id"], reason="not the moment")
    assert r["status"] == "released"
    assert r["released_reason"] == "not the moment"
    assert r["heat"] == "warm"  # touched by release


def test_release_without_reason_ok(storage: Storage):
    i = storage.write("idea", {"title": "quiet drop"})
    r = storage.release_idea(i["id"])
    assert r["status"] == "released" and r["released_reason"] is None


# --- cooling query exclusions (acceptance #7) -------------------------------


def test_cooling_excludes_someday_and_terminal_and_fresh(storage: Storage):
    spark_old = storage.write("idea", {"title": "spark_old"})
    explore_old = storage.write("idea", {"title": "explore_old", "status": "exploring"})
    someday_old = storage.write("idea", {"title": "someday_old", "status": "someday"})
    spark_fresh = storage.write("idea", {"title": "spark_fresh"})
    promoted = storage.write("idea", {"title": "promoted_old"})
    storage.promote_idea(promoted["id"])
    for x in (spark_old, explore_old, someday_old, promoted):
        _backdate(storage, x["id"], 210)  # 7 months
    cooling = storage.list_cooling_ideas()
    titles = {c["title"] for c in cooling}
    assert titles == {"spark_old", "explore_old"}
    assert "someday_old" not in titles      # deliberately parked
    assert "promoted_old" not in titles      # terminal
    assert "spark_fresh" not in titles       # not past threshold


def test_cooling_threshold_respected(storage: Storage):
    i = storage.write("idea", {"title": "five months"})
    _backdate(storage, i["id"], 150)  # ~5 months < 6
    assert storage.list_cooling_ideas() == []
    assert storage.list_cooling_ideas(dormant_months=4)  # 5mo > 4mo → cools


def test_list_ideas_active_filter(storage: Storage):
    storage.write("idea", {"title": "s"})
    storage.write("idea", {"title": "e", "status": "exploring"})
    storage.write("idea", {"title": "d", "status": "someday"})
    rel = storage.write("idea", {"title": "r"})
    storage.release_idea(rel["id"])
    active = {i["title"] for i in storage.list_ideas(status="active")}
    assert active == {"s", "e", "d"}  # excludes released


# --- manual end-to-end (acceptance #11) -------------------------------------


def test_e2e_only_cooled_spark_surfaces(storage: Storage):
    """Capture three ideas, promote one, park one as someday, leave one as
    spark; backdate the spark 7 months; the cooling feed surfaces only the
    cooled spark — not the someday one, not the promoted one."""
    a = storage.write("idea", {"title": "cooled spark", "body": "the one that drifts"})
    b = storage.write("idea", {"title": "to promote"})
    c = storage.write("idea", {"title": "to park"})
    storage.promote_idea(b["id"])
    storage.update("idea", c["id"], {"status": "someday"})
    _backdate(storage, a["id"], 210)   # 7 months
    _backdate(storage, c["id"], 210)   # also old, but someday → excluded

    cooling = storage.list_cooling_ideas()
    assert [x["title"] for x in cooling] == ["cooled spark"]


# --- skill-prompt edits present (acceptance #8, #9, #10) --------------------


def test_capture_recognizes_sparks_as_ideas():
    body = " ".join((SKILLS_DIR / "capture.md").read_text().split())
    assert "**idea**" in body
    assert 'entity_type = "idea"' in body
    assert "Do NOT promote it to a project during capture" in body
    assert "pre-commitment" in body


def test_scanner_has_patient_cooling_pattern():
    body = (SKILLS_DIR / "scan_life_units.md").read_text()
    assert "cooling_idea" in body
    assert "state_list_cooling_ideas" in body
    assert "review_ideas" in body          # routes to review, not self-disposition
    assert "someday" in body                # explicitly excluded / never nagged


def test_opportunity_research_uses_ideas_as_feedstock():
    body = (SKILLS_DIR / "opportunity_research.md").read_text()
    assert "state_list_ideas" in body
    assert "Idea viability" in body


def test_idea_skills_satisfy_contract():
    for name in ("capture_idea.md", "review_ideas.md"):
        body = (SKILLS_DIR / name).read_text()
        assert "constitution://main" in body
        assert "Triggers" in body and "Not for" in body
        assert "state_begin_operation" in body and "state_end_operation" in body
