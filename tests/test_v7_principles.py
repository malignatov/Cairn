"""v7: ranked principles, revisions, and the three-case decision↔principle
evaluation model.

Heaviest coverage on the two conceptually-tricky pieces called out in the
handoff: the rank-reordering logic (insert/move/retire edge cases) and the
relationship-specific validation of evaluations.
"""

import sqlite3

import pytest

from meta_assistant.storage import Storage


# --- fixtures / helpers -----------------------------------------------------


def _mk(storage, name, rank, **extra):
    data = {"name": name, "description": f"{name} description", "rank": rank}
    data.update(extra)
    return storage.write("principle", data)


def _ranked(storage, status="active"):
    return [(p["rank"], p["name"]) for p in storage.list_principles(status=status)]


@pytest.fixture
def five(storage: Storage):
    """Five active principles at ranks 1..5, appended in order."""
    for i, nm in enumerate(["A", "B", "C", "D", "E"], start=1):
        p = _mk(storage, nm, i)
        assert p["rank"] == i
    return storage


# --- table creation (acceptance #2) -----------------------------------------


def test_v7_tables_created(storage: Storage):
    with sqlite3.connect(storage.db_path) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    assert {"principles", "principle_revisions",
            "decision_principle_evaluations"} <= tables


# --- create + unique rank + insert-shift (acceptance #3) --------------------


def test_create_requires_fields(storage: Storage):
    for bad in (
        {"description": "d", "rank": 1},          # no name
        {"name": "X", "rank": 1},                  # no description
        {"name": "X", "description": "d"},         # no rank
        {"name": "X", "description": "d", "rank": 0},     # rank < 1
        {"name": "X", "description": "d", "rank": -2},
    ):
        with pytest.raises(ValueError):
            storage.write("principle", bad)


def test_append_keeps_contiguous(five):
    assert _ranked(five) == [(1, "A"), (2, "B"), (3, "C"), (4, "D"), (5, "E")]


def test_insert_at_top_shifts_all_down(five):
    _mk(five, "TOP", 1)
    assert _ranked(five) == [
        (1, "TOP"), (2, "A"), (3, "B"), (4, "C"), (5, "D"), (6, "E")
    ]


def test_insert_in_middle_shifts_tail(five):
    _mk(five, "MID", 3)
    assert _ranked(five) == [
        (1, "A"), (2, "B"), (3, "MID"), (4, "C"), (5, "D"), (6, "E")
    ]


def test_insert_at_bottom_appends(five):
    _mk(five, "BOT", 6)
    assert _ranked(five)[-1] == (6, "BOT")
    assert [r for r, _ in _ranked(five)] == [1, 2, 3, 4, 5, 6]


def test_insert_beyond_end_is_clamped(five):
    p = _mk(five, "FAR", 99)
    assert p["rank"] == 6  # clamped to append slot
    assert [r for r, _ in _ranked(five)] == [1, 2, 3, 4, 5, 6]


def test_no_duplicate_active_ranks_ever(five):
    _mk(five, "X", 2)
    _mk(five, "Y", 2)
    _mk(five, "Z", 7)
    ranks = [r for r, _ in _ranked(five)]
    assert ranks == sorted(ranks)
    assert len(ranks) == len(set(ranks))  # all distinct


# --- rerank (acceptance #4) -------------------------------------------------


def test_rerank_move_up(five):
    e = [p for p in five.list_principles() if p["name"] == "E"][0]
    five.rerank_principle(e["id"], 2, rationale="more central now")
    assert _ranked(five) == [
        (1, "A"), (2, "E"), (3, "B"), (4, "C"), (5, "D")
    ]


def test_rerank_move_down(five):
    a = [p for p in five.list_principles() if p["name"] == "A"][0]
    five.rerank_principle(a["id"], 4, rationale="deprioritized")
    assert _ranked(five) == [
        (1, "B"), (2, "C"), (3, "D"), (4, "A"), (5, "E")
    ]


def test_rerank_to_same_rank_is_noop(five):
    c = [p for p in five.list_principles() if p["name"] == "C"][0]
    before = _ranked(five)
    five.rerank_principle(c["id"], 3, rationale="no change")
    assert _ranked(five) == before


def test_rerank_beyond_end_clamps_to_bottom(five):
    a = [p for p in five.list_principles() if p["name"] == "A"][0]
    five.rerank_principle(a["id"], 99, rationale="to the bottom")
    assert _ranked(five)[-1] == (5, "A")
    assert [r for r, _ in _ranked(five)] == [1, 2, 3, 4, 5]


def test_rerank_requires_rationale(five):
    a = [p for p in five.list_principles() if p["name"] == "A"][0]
    with pytest.raises(ValueError):
        five.rerank_principle(a["id"], 3, rationale="")


def test_rerank_logs_revision(five):
    a = [p for p in five.list_principles() if p["name"] == "A"][0]
    five.rerank_principle(a["id"], 3, rationale="why")
    with sqlite3.connect(five.db_path) as conn:
        rows = conn.execute(
            "SELECT change_type, rationale FROM principle_revisions "
            "WHERE principle_id=? AND change_type='reranked'", (a["id"],)
        ).fetchall()
    assert len(rows) == 1 and rows[0][1] == "why"


# --- retire (acceptance #5) -------------------------------------------------


def test_retire_leaves_gap_and_logs(five):
    b = [p for p in five.list_principles() if p["name"] == "B"][0]
    five.retire_principle(b["id"], rationale="outgrew it")
    active = _ranked(five, status="active")
    assert "B" not in [n for _, n in active]
    assert len(active) == 4
    # gap left at rank 2 (no auto-compaction)
    assert 2 not in [r for r, _ in active]
    with sqlite3.connect(five.db_path) as conn:
        ct = conn.execute(
            "SELECT change_type FROM principle_revisions "
            "WHERE principle_id=? ORDER BY revised_at DESC LIMIT 1", (b["id"],)
        ).fetchone()[0]
    assert ct == "retired"


def test_retire_twice_errors(five):
    b = [p for p in five.list_principles() if p["name"] == "B"][0]
    five.retire_principle(b["id"], rationale="x")
    with pytest.raises(ValueError):
        five.retire_principle(b["id"], rationale="again")


def test_cannot_rerank_retired(five):
    b = [p for p in five.list_principles() if p["name"] == "B"][0]
    five.retire_principle(b["id"], rationale="x")
    with pytest.raises(ValueError):
        five.rerank_principle(b["id"], 1, rationale="nope")


def test_retired_rank_freed_for_reuse(five):
    b = [p for p in five.list_principles() if p["name"] == "B"][0]
    five.retire_principle(b["id"], rationale="x")  # frees rank 2 among active
    _mk(five, "NEW", 2)
    active = {n: r for r, n in _ranked(five, status="active")}
    assert active["NEW"] == 2
    # exactly one ACTIVE principle at rank 2
    ranks = [r for r, _ in _ranked(five, status="active")]
    assert ranks.count(2) == 1


# --- update logs a 'rewritten' revision; rank/status off-limits -------------


def test_update_principle_logs_rewritten(five):
    a = [p for p in five.list_principles() if p["name"] == "A"][0]
    five.update("principle", a["id"],
                {"description": "sharper wording", "rationale": "clarified"})
    with sqlite3.connect(five.db_path) as conn:
        ct = conn.execute(
            "SELECT change_type FROM principle_revisions "
            "WHERE principle_id=? AND change_type='rewritten'", (a["id"],)
        ).fetchall()
    assert len(ct) == 1


def test_update_requires_rationale(five):
    a = [p for p in five.list_principles() if p["name"] == "A"][0]
    with pytest.raises(ValueError):
        five.update("principle", a["id"], {"description": "x"})


def test_update_rejects_rank_and_status(five):
    a = [p for p in five.list_principles() if p["name"] == "A"][0]
    with pytest.raises(ValueError):
        five.update("principle", a["id"], {"rank": 2, "rationale": "r"})
    with pytest.raises(ValueError):
        five.update("principle", a["id"], {"status": "retired", "rationale": "r"})


# --- evaluation validation (acceptance #6) ----------------------------------


@pytest.fixture
def nine_and_decision(storage: Storage):
    P = {i: _mk(storage, f"P{i}", i)["id"] for i in range(1, 10)}
    dec = storage.write("decision", {"what": "do X", "rationale": "y"})["id"]
    return storage, P, dec


def test_aligned_clears_severity_and_override(nine_and_decision):
    s, P, dec = nine_and_decision
    r = s.record_principle_evaluation(dec, P[4], "aligned",
                                      overrides_principle_id=P[1], severity="high")
    assert r["severity"] is None
    assert r["overrides_principle_id"] is None


def test_justified_override_requires_target_and_rationale(nine_and_decision):
    s, P, dec = nine_and_decision
    with pytest.raises(ValueError):
        s.record_principle_evaluation(dec, P[5], "justified_override")
    with pytest.raises(ValueError):
        s.record_principle_evaluation(dec, P[5], "justified_override",
                                      overrides_principle_id=P[2])  # no rationale


def test_justified_override_target_must_outrank(nine_and_decision):
    s, P, dec = nine_and_decision
    # P7 cannot override P3 (7 is lower-ranked than 3)
    with pytest.raises(ValueError):
        s.record_principle_evaluation(dec, P[3], "justified_override",
                                      overrides_principle_id=P[7], rationale="r")
    # equal rank (same principle) also invalid
    with pytest.raises(ValueError):
        s.record_principle_evaluation(dec, P[3], "justified_override",
                                      overrides_principle_id=P[3], rationale="r")
    # valid: depart P7 to serve higher P2
    r = s.record_principle_evaluation(dec, P[7], "justified_override",
                                      overrides_principle_id=P[2], rationale="served P2")
    assert r["overrides_principle_id"] == P[2]
    assert r["severity"] is None  # healthy, not an alarm


def test_unjustified_departure_requires_rationale(nine_and_decision):
    s, P, dec = nine_and_decision
    with pytest.raises(ValueError):
        s.record_principle_evaluation(dec, P[2], "unjustified_departure")


def test_unjustified_departure_severity_scales_with_rank(nine_and_decision):
    s, P, dec = nine_and_decision
    # 9 active → tertiles: 1-3 high, 4-6 medium, 7-9 low
    hi = s.record_principle_evaluation(dec, P[2], "unjustified_departure", rationale="x")
    md = s.record_principle_evaluation(dec, P[5], "unjustified_departure", rationale="x")
    lo = s.record_principle_evaluation(dec, P[9], "unjustified_departure", rationale="x")
    assert (hi["severity"], md["severity"], lo["severity"]) == ("high", "medium", "low")


def test_explicit_severity_respected(nine_and_decision):
    s, P, dec = nine_and_decision
    r = s.record_principle_evaluation(dec, P[9], "unjustified_departure",
                                      rationale="x", severity="high")
    assert r["severity"] == "high"


def test_evaluate_helper_returns_ranked_principles(nine_and_decision):
    s, P, dec = nine_and_decision
    out = s.evaluate_decision_against_principles(dec)
    assert out["decision_id"] == dec
    assert [p["rank"] for p in out["principles"]] == list(range(1, 10))


def test_bad_relationship_or_severity_rejected(nine_and_decision):
    s, P, dec = nine_and_decision
    with pytest.raises(ValueError):
        s.record_principle_evaluation(dec, P[1], "nonsense")
    with pytest.raises(ValueError):
        s.record_principle_evaluation(dec, P[1], "aligned", severity="critical")


# --- list_principle_departures (scanner feed, acceptance #9 support) --------


def test_list_departures_filters(nine_and_decision):
    s, P, dec = nine_and_decision
    s.record_principle_evaluation(dec, P[2], "unjustified_departure", rationale="a")
    s.record_principle_evaluation(dec, P[2], "unjustified_departure", rationale="b")
    s.record_principle_evaluation(dec, P[3], "unjustified_departure", rationale="c")
    s.record_principle_evaluation(dec, P[7], "justified_override",
                                  overrides_principle_id=P[1], rationale="d")
    # all unjustified departures
    deps = s.list_principle_departures(relationship="unjustified_departure")
    assert len(deps) == 3
    # repeated departure of the SAME principle (the scanner's signal)
    p2 = s.list_principle_departures(principle_id=P[2],
                                     relationship="unjustified_departure")
    assert len(p2) == 2
    # overrides are not departures
    overrides = s.list_principle_departures(relationship="justified_override")
    assert len(overrides) == 1


def test_scanner_signal_only_on_repeat_not_single_or_override(nine_and_decision):
    """Acceptance #9: the repeated_unjustified_departure signal is a count of
    2+ unjustified departures for the SAME principle. Single departures and
    justified overrides must not contribute to that count."""
    s, P, dec = nine_and_decision
    # P1: two unjustified departures → fires
    s.record_principle_evaluation(dec, P[1], "unjustified_departure", rationale="1")
    s.record_principle_evaluation(dec, P[1], "unjustified_departure", rationale="2")
    # P4: one unjustified departure → does not fire
    s.record_principle_evaluation(dec, P[4], "unjustified_departure", rationale="3")
    # P5: two justified overrides → never fires (healthy)
    s.record_principle_evaluation(dec, P[5], "justified_override",
                                  overrides_principle_id=P[1], rationale="o1")
    s.record_principle_evaluation(dec, P[5], "justified_override",
                                  overrides_principle_id=P[2], rationale="o2")

    def count(pid):
        return len(s.list_principle_departures(
            principle_id=pid, relationship="unjustified_departure"))

    fires = {pid for pid in P.values() if count(pid) >= 2}
    assert fires == {P[1]}  # only P1


# --- principle is SLU-taggable and searchable -------------------------------


def test_principle_is_slu_taggable(storage: Storage):
    slus = storage.list_active_slus()
    p = storage.write("principle", {
        "name": "Depth", "description": "depth over breadth", "rank": 1,
        "slu_links": [slus[0]["id"]],
    })
    links = storage.list_entity_slu_links(entity_id=p["id"], entity_type="principle")
    assert len(links) == 1


def test_principle_is_searchable(storage: Storage):
    storage.write("principle", {
        "name": "Rectitude", "description": "act rightly toward all", "rank": 1,
    })
    hits = storage.search("Rectitude")
    assert any(h["entity_type"] == "principle" for h in hits)
