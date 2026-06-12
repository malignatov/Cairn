"""Tests for v6: end-to-end logging substrate.

Covers the four new tables, the chat / operation / tool_call / resource_read
storage methods, the dispatch-layer wrapper that auto-logs every tool call,
auto-abandonment of forgotten in_progress operations, error-still-raises
behavior, the payload_size_warning flag, the trace-query surface
(list_recent_*, get_*_trace, list_errors, list_abandoned_operations), and
the manual purge path.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import json
import pytest

from meta_assistant.storage import Storage


SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


# --- schema --------------------------------------------------------------


def test_schema_creates_v6_tables(storage: Storage):
    import sqlite3
    with sqlite3.connect(storage.db_path) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    for t in ("chats", "operations", "tool_calls", "resource_reads"):
        assert t in tables, f"v6 table {t!r} missing"


def test_schema_creates_v6_indexes(storage: Storage):
    """Indexes are load-bearing for the trace queries against months of
    accumulated logs."""
    import sqlite3
    with sqlite3.connect(storage.db_path) as conn:
        indexes = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )}
    for ix in (
        "chats_started_at_idx",
        "operations_chat_idx", "operations_skill_idx",
        "operations_status_idx", "operations_started_idx",
        "tool_calls_chat_idx", "tool_calls_op_idx",
        "tool_calls_name_idx", "tool_calls_status_idx",
        "tool_calls_started_idx",
        "resource_reads_chat_idx", "resource_reads_op_idx",
    ):
        assert ix in indexes, f"index {ix!r} missing"


# --- chats / operations / tool_calls / resource_reads -- direct API ------


def test_begin_chat_returns_id_and_persists(storage: Storage):
    import sqlite3
    chat_id = storage.begin_chat(client_info="test-client")
    assert chat_id
    with sqlite3.connect(storage.db_path) as conn:
        row = conn.execute(
            "SELECT * FROM chats WHERE id = ?", (chat_id,)
        ).fetchone()
    assert row is not None
    assert row[0] == chat_id  # id
    assert row[3] == "test-client"  # client_info


def test_end_chat_sets_ended_at(storage: Storage):
    import sqlite3
    chat_id = storage.begin_chat()
    storage.end_chat(chat_id)
    with sqlite3.connect(storage.db_path) as conn:
        ended_at = conn.execute(
            "SELECT ended_at FROM chats WHERE id = ?", (chat_id,)
        ).fetchone()[0]
    assert ended_at is not None


def test_begin_operation_inserts_in_progress(storage: Storage):
    chat = storage.begin_chat()
    op_id = storage.begin_operation(chat, "capture", notes="test")
    assert op_id
    active = storage.get_active_operation(chat)
    assert active == op_id


def test_begin_operation_auto_abandons_prior(storage: Storage):
    """A second begin_operation in the same chat closes the first as
    abandoned with an explanatory note. Forgotten end_operation case."""
    chat = storage.begin_chat()
    first = storage.begin_operation(chat, "capture")
    second = storage.begin_operation(chat, "scan_life_units")

    import sqlite3
    with sqlite3.connect(storage.db_path) as conn:
        prior = conn.execute(
            "SELECT status, notes, completed_at FROM operations WHERE id = ?",
            (first,),
        ).fetchone()
    assert prior[0] == "abandoned"
    assert "auto-closed" in prior[1]
    assert "scan_life_units" in prior[1]
    assert prior[2] is not None  # completed_at set

    # The new one is the active one
    assert storage.get_active_operation(chat) == second


def test_begin_operation_does_not_cross_chat_boundaries(storage: Storage):
    """An in_progress operation in chat A should NOT be auto-abandoned
    when a new operation begins in chat B."""
    a = storage.begin_chat()
    b = storage.begin_chat()
    op_a = storage.begin_operation(a, "capture")
    storage.begin_operation(b, "find")
    assert storage.get_active_operation(a) == op_a  # still alive


def test_end_operation_completes(storage: Storage):
    chat = storage.begin_chat()
    op = storage.begin_operation(chat, "capture")
    result = storage.end_operation(op, "completed", notes="2 drafts committed")
    assert result["status"] == "completed"
    assert result["completed_at"]
    assert result["notes"] == "2 drafts committed"
    assert storage.get_active_operation(chat) is None  # no longer active


def test_end_operation_abandoned(storage: Storage):
    chat = storage.begin_chat()
    op = storage.begin_operation(chat, "capture")
    result = storage.end_operation(op, "abandoned", notes="user aborted")
    assert result["status"] == "abandoned"


def test_end_operation_invalid_status(storage: Storage):
    chat = storage.begin_chat()
    op = storage.begin_operation(chat, "capture")
    with pytest.raises(ValueError, match="status"):
        storage.end_operation(op, "in_progress")
    with pytest.raises(ValueError, match="status"):
        storage.end_operation(op, "garbage")


def test_end_operation_already_terminal(storage: Storage):
    chat = storage.begin_chat()
    op = storage.begin_operation(chat, "capture")
    storage.end_operation(op, "completed")
    with pytest.raises(ValueError, match="terminal"):
        storage.end_operation(op, "completed")


def test_end_operation_missing(storage: Storage):
    with pytest.raises(ValueError, match="not found"):
        storage.end_operation("no-such-op")


# --- log_tool_call -------------------------------------------------------


def _now():
    return datetime.now(timezone.utc).isoformat()


def test_log_tool_call_persists_all_fields(storage: Storage):
    import sqlite3
    chat = storage.begin_chat()
    op = storage.begin_operation(chat, "capture")
    args_json = '{"entity_type": "project"}'
    resp_json = '[{"id": "p1"}]'
    storage.log_tool_call(
        chat_id=chat,
        operation_id=op,
        tool_name="state_query",
        status="ok",
        started_at=_now(),
        completed_at=_now(),
        duration_ms=12,
        arguments_json=args_json,
        response_json=resp_json,
        arguments_size_bytes=len(args_json),
        response_size_bytes=len(resp_json),
        error_message=None,
        payload_size_warning=False,
    )
    with sqlite3.connect(storage.db_path) as conn:
        row = conn.execute(
            "SELECT tool_name, status, duration_ms, arguments_json, "
            "       response_json, error_message, payload_size_warning "
            "FROM tool_calls WHERE chat_id = ? AND operation_id = ?",
            (chat, op),
        ).fetchone()
    assert row[0] == "state_query"
    assert row[1] == "ok"
    assert row[2] == 12
    assert row[3] == args_json
    assert row[4] == resp_json
    assert row[5] is None
    assert row[6] == 0  # SQLite stores bool as int


def test_log_tool_call_error_status(storage: Storage):
    chat = storage.begin_chat()
    storage.log_tool_call(
        chat_id=chat,
        operation_id=None,
        tool_name="state_write",
        status="error",
        started_at=_now(),
        completed_at=_now(),
        duration_ms=3,
        arguments_json="{}",
        response_json=None,
        arguments_size_bytes=2,
        response_size_bytes=None,
        error_message="ValueError: rationale required",
        payload_size_warning=False,
    )
    errors = storage.list_errors()
    assert any(e["error_message"] == "ValueError: rationale required" for e in errors)


def test_log_resource_read_persists(storage: Storage):
    import sqlite3
    chat = storage.begin_chat()
    storage.log_resource_read(
        chat_id=chat,
        operation_id=None,
        resource_uri="skill://capture",
        content_size_bytes=4096,
        started_at=_now(),
        duration_ms=2,
    )
    with sqlite3.connect(storage.db_path) as conn:
        rows = conn.execute(
            "SELECT resource_uri, content_size_bytes "
            "FROM resource_reads WHERE chat_id = ?", (chat,)
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "skill://capture"
    assert rows[0][1] == 4096


# --- the dispatch wrapper auto-logs ---------------------------------------


def _build_test_server(tmp_path):
    """Build a Storage + a Mock'd-up FastMCP-wrapped tool surface for
    end-to-end wrapper testing without needing to spin up HTTP."""
    from meta_assistant.server import build_server
    skills_dir = tmp_path / "skills"
    schemas_dir = tmp_path / "schemas"
    guides_dir = tmp_path / "guides"
    constitution = tmp_path / "constitution.md"
    for d in (skills_dir, schemas_dir, guides_dir):
        d.mkdir()
    constitution.write_text("hi")
    mcp, storage = build_server(
        db_path=str(tmp_path / "v6.db"),
        skills_dir=skills_dir,
        schemas_dir=schemas_dir,
        guides_dir=guides_dir,
        constitution_path=constitution,
    )
    return mcp, storage


def _call_tool(mcp, name, **kwargs):
    """Call a registered FastMCP tool through its wrapper fn, the way
    the server dispatcher would."""
    tool = mcp._tool_manager._tools[name]
    return tool.fn(**kwargs)


def test_wrapper_logs_every_tool_call(tmp_path):
    """Calling any tool through the wrapped fn should produce exactly
    one tool_calls row with full fields."""
    mcp, storage = _build_test_server(tmp_path)
    # Use a no-side-effect tool: list_active_slus returns the 16 seeded SLUs
    result = _call_tool(mcp, "state_list_active_slus")
    assert len(result) == 16

    import sqlite3
    with sqlite3.connect(storage.db_path) as conn:
        rows = conn.execute(
            "SELECT tool_name, status, arguments_json, response_json, "
            "       duration_ms, payload_size_warning "
            "FROM tool_calls WHERE tool_name = 'state_list_active_slus'"
        ).fetchall()
    assert len(rows) == 1
    name, status, args, resp, dur, warn = rows[0]
    assert name == "state_list_active_slus"
    assert status == "ok"
    # arguments_json is some empty-args JSON
    assert json.loads(args) in ({}, [])
    # response_json should round-trip back to a list of 16 dicts
    parsed = json.loads(resp)
    assert isinstance(parsed, list) and len(parsed) == 16
    assert isinstance(dur, int) and dur >= 0
    assert warn == 0


def test_wrapper_logs_errors_and_reraises(tmp_path):
    """An errored tool call must (a) raise from the dispatcher, AND
    (b) leave a status='error' row behind with the error message."""
    mcp, storage = _build_test_server(tmp_path)
    with pytest.raises(ValueError):
        _call_tool(
            mcp, "state_query", entity_type="not-a-real-type",
        )

    import sqlite3
    with sqlite3.connect(storage.db_path) as conn:
        rows = conn.execute(
            "SELECT status, error_message FROM tool_calls "
            "WHERE tool_name = 'state_query'"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "error"
    assert "ValueError" in rows[0][1]
    assert "not-a-real-type" in rows[0][1] or "entity_type" in rows[0][1]


def test_wrapper_assigns_chat_id_on_first_call(tmp_path):
    """A tool call without prior context should create one chats row
    and attach the call to it. Subsequent calls in the same process
    reuse the same chat_id."""
    mcp, storage = _build_test_server(tmp_path)
    _call_tool(mcp, "state_list_active_slus")
    _call_tool(mcp, "state_list_active_slus")

    import sqlite3
    with sqlite3.connect(storage.db_path) as conn:
        n_chats = conn.execute("SELECT COUNT(*) FROM chats").fetchone()[0]
        n_calls = conn.execute(
            "SELECT COUNT(*) FROM tool_calls "
            "WHERE tool_name = 'state_list_active_slus'"
        ).fetchone()[0]
        distinct_chats = conn.execute(
            "SELECT COUNT(DISTINCT chat_id) FROM tool_calls "
            "WHERE tool_name = 'state_list_active_slus'"
        ).fetchone()[0]
    # One sentinel chat per test (process), reused across calls
    assert n_chats == 1
    assert n_calls == 2
    assert distinct_chats == 1


def test_wrapper_tags_calls_with_active_operation(tmp_path):
    """After begin_operation, subsequent tool calls in the same chat
    should have operation_id matching it. After end_operation, calls
    have operation_id = NULL again."""
    mcp, storage = _build_test_server(tmp_path)
    op_info = _call_tool(mcp, "state_begin_operation", skill_name="test_flow")
    op_id = op_info["operation_id"]

    # A tool call inside the operation
    _call_tool(mcp, "state_list_active_slus")

    _call_tool(mcp, "state_end_operation", operation_id=op_id)

    # A tool call outside any operation
    _call_tool(mcp, "state_list_active_slus")

    import sqlite3
    with sqlite3.connect(storage.db_path) as conn:
        # The two list_active_slus calls — one with operation_id set,
        # one without.
        rows = conn.execute(
            "SELECT operation_id FROM tool_calls "
            "WHERE tool_name = 'state_list_active_slus' "
            "ORDER BY started_at ASC"
        ).fetchall()
    assert len(rows) == 2
    assert rows[0][0] == op_id     # tagged
    assert rows[1][0] is None      # ad-hoc after end


def test_payload_size_warning_flags_large(tmp_path):
    """A tool call with arguments or response exceeding 100KB should
    set payload_size_warning=true. We synthesize a large response by
    seeding many entities and querying them."""
    mcp, storage = _build_test_server(tmp_path)
    # Build a large blob via repeated writes
    for i in range(200):
        storage.write("project", {
            "name": f"P{i}",
            "description": "x" * 600,  # ~120KB total across 200 projects
        })
    result = _call_tool(mcp, "state_query", entity_type="project")
    assert len(result) >= 200

    import sqlite3
    with sqlite3.connect(storage.db_path) as conn:
        row = conn.execute(
            "SELECT response_size_bytes, payload_size_warning "
            "FROM tool_calls WHERE tool_name = 'state_query' "
            "ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
    assert row[0] > 100_000
    assert row[1] == 1  # warning set


def test_purge_logs_does_not_log_itself(tmp_path):
    """state_purge_logs is excluded from the logged-tools wrap so it
    can't infinite-recurse. Calling it produces no tool_calls row for
    itself, even though the trace shows it executed (no row, no recursion)."""
    mcp, storage = _build_test_server(tmp_path)
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    counts = _call_tool(mcp, "state_purge_logs", before_date=future)
    # purge ran (returned a counts dict) but isn't itself logged
    assert set(counts.keys()) == {
        "tool_calls", "resource_reads", "operations", "chats",
    }
    import sqlite3
    with sqlite3.connect(storage.db_path) as conn:
        purge_rows = conn.execute(
            "SELECT COUNT(*) FROM tool_calls WHERE tool_name = 'state_purge_logs'"
        ).fetchone()[0]
    assert purge_rows == 0


# --- query surface -------------------------------------------------------


def test_get_operation_trace_returns_chronological_bundle(tmp_path):
    """get_operation_trace returns the operation plus all its tool_calls
    and resource_reads, ordered chronologically."""
    mcp, storage = _build_test_server(tmp_path)
    op_info = _call_tool(mcp, "state_begin_operation", skill_name="test_trace")
    op_id = op_info["operation_id"]
    _call_tool(mcp, "state_list_active_slus")
    _call_tool(mcp, "state_query", entity_type="project")
    _call_tool(mcp, "state_end_operation", operation_id=op_id,
               notes="trace test complete")

    trace = storage.get_operation_trace(op_id)
    assert trace is not None
    assert trace["operation"]["id"] == op_id
    assert trace["operation"]["status"] == "completed"
    assert trace["operation"]["notes"] == "trace test complete"
    # state_list_active_slus + state_query + state_end_operation; the
    # state_begin_operation that opened this is OUTSIDE the operation
    # by design (the operation_id is null on the call that created it).
    assert len(trace["tool_calls"]) >= 3
    # Chronological order
    times = [tc["started_at"] for tc in trace["tool_calls"]]
    assert times == sorted(times)


def test_get_chat_trace_includes_loose_calls(tmp_path):
    mcp, storage = _build_test_server(tmp_path)
    # Ad-hoc call (no operation)
    _call_tool(mcp, "state_list_active_slus")
    # Operation with two calls
    op = _call_tool(mcp, "state_begin_operation", skill_name="capture")
    _call_tool(mcp, "state_query", entity_type="project")
    _call_tool(mcp, "state_end_operation", operation_id=op["operation_id"])

    chats = storage.list_recent_chats(limit=10)
    assert len(chats) == 1
    chat_id = chats[0]["id"]
    trace = storage.get_chat_trace(chat_id)
    assert trace["chat"]["id"] == chat_id
    assert len(trace["operations"]) == 1
    # Operation-bound + ad-hoc + begin/end calls all under chat
    tool_calls = trace["tool_calls"]
    assert any(tc["operation_id"] is None for tc in tool_calls)
    assert any(tc["operation_id"] == op["operation_id"] for tc in tool_calls)


def test_list_recent_operations_filters(tmp_path):
    mcp, storage = _build_test_server(tmp_path)
    op1 = _call_tool(mcp, "state_begin_operation", skill_name="capture")
    _call_tool(mcp, "state_end_operation",
               operation_id=op1["operation_id"], notes="done")
    op2 = _call_tool(mcp, "state_begin_operation", skill_name="find")
    _call_tool(mcp, "state_end_operation",
               operation_id=op2["operation_id"], status="abandoned")

    by_skill = storage.list_recent_operations(skill_name="capture")
    assert all(o["skill_name"] == "capture" for o in by_skill)
    assert any(o["id"] == op1["operation_id"] for o in by_skill)

    by_status = storage.list_recent_operations(status="abandoned")
    assert all(o["status"] == "abandoned" for o in by_status)

    with pytest.raises(ValueError, match="status"):
        storage.list_recent_operations(status="ghost")


def test_list_abandoned_operations(tmp_path):
    """Operations explicitly marked 'abandoned' AND in_progress ones
    that are older than the stuck-for threshold."""
    mcp, storage = _build_test_server(tmp_path)
    # One explicitly abandoned via auto-close
    chat = storage.begin_chat()
    op1 = storage.begin_operation(chat, "capture")
    storage.begin_operation(chat, "find")  # auto-abandons op1

    # One that's in_progress but old (manually backdate)
    op2 = storage.begin_operation(chat, "scan")
    import sqlite3
    long_ago = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    with sqlite3.connect(storage.db_path) as conn:
        conn.execute(
            "UPDATE operations SET started_at = ? WHERE id = ?",
            (long_ago, op2),
        )

    stale = storage.list_abandoned_operations(stuck_for_hours=1)
    ids = {o["id"] for o in stale}
    assert op1 in ids
    assert op2 in ids


def test_list_errors_filtered_by_since(tmp_path):
    mcp, storage = _build_test_server(tmp_path)
    with pytest.raises(ValueError):
        _call_tool(mcp, "state_query", entity_type="bogus")
    errs = storage.list_errors()
    assert len(errs) >= 1
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    assert storage.list_errors(since=future) == []


def test_purge_logs_deletes_old_rows(tmp_path):
    """purge_logs(before_date) deletes rows older than before_date in
    each log table, returning per-table counts."""
    mcp, storage = _build_test_server(tmp_path)
    # Generate some log activity
    op = _call_tool(mcp, "state_begin_operation", skill_name="capture")
    _call_tool(mcp, "state_list_active_slus")
    _call_tool(mcp, "state_end_operation", operation_id=op["operation_id"])

    # Backdate everything via raw SQL so purging cleans it
    import sqlite3
    long_ago = "2020-01-01T00:00:00+00:00"
    with sqlite3.connect(storage.db_path) as conn:
        for tbl in ("tool_calls", "resource_reads", "operations", "chats"):
            conn.execute(f"UPDATE {tbl} SET started_at = ?", (long_ago,))

    cutoff = "2021-01-01T00:00:00+00:00"
    counts = storage.purge_logs(cutoff)
    assert counts["chats"] >= 1
    assert counts["operations"] >= 1
    assert counts["tool_calls"] >= 1

    # All log tables now empty (or at least no rows older than cutoff)
    with sqlite3.connect(storage.db_path) as conn:
        for tbl in ("tool_calls", "resource_reads", "operations", "chats"):
            n = conn.execute(
                f"SELECT COUNT(*) FROM {tbl} WHERE started_at < ?", (cutoff,)
            ).fetchone()[0]
            assert n == 0


def test_purge_logs_validates_input(storage: Storage):
    with pytest.raises(ValueError, match="before_date"):
        storage.purge_logs("")
    with pytest.raises(ValueError, match="before_date"):
        storage.purge_logs(None)  # type: ignore[arg-type]


# --- resource read logging ------------------------------------------------


def test_resource_read_is_logged(tmp_path):
    """Reading a resource (skill/schema/guide markdown) should produce
    one resource_reads row with the URI and content size."""
    mcp, storage = _build_test_server(tmp_path)
    # Drop a fake skill and re-read via the wrapped reader
    (tmp_path / "skills" / "demo.md").write_text(
        "---\nname: demo\ndescription: |\n  test\n---\n# Demo\nbody"
    )
    # We need to rebuild to pick up the new file. For this test, just
    # invoke the constitution reader which is already registered.
    res = mcp._resource_manager._resources["constitution://main"]
    content = res.fn()
    assert "hi" in content  # the test fixture wrote "hi" to constitution.md

    import sqlite3
    with sqlite3.connect(storage.db_path) as conn:
        rows = conn.execute(
            "SELECT resource_uri, content_size_bytes "
            "FROM resource_reads WHERE resource_uri = 'constitution://main'"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "constitution://main"
    assert rows[0][1] is not None and rows[0][1] > 0


# --- skill markdown updates ship -----------------------------------------


SKILLS_WITH_OPERATION_LIFECYCLE = [
    "capture", "inbox", "opportunity_research",
    "scan_life_units", "scan_inventory",
    "inventory_intake", "add_inventory_items",
    "find", "define_great_life", "assess_life_portfolio",
    "patch_portfolio", "quick_update_slu", "update_resources",
    "trace",
]


@pytest.mark.parametrize("skill", SKILLS_WITH_OPERATION_LIFECYCLE)
def test_every_skill_calls_begin_and_end_operation(skill):
    body = (SKILLS_DIR / f"{skill}.md").read_text()
    assert "state_begin_operation" in body, f"{skill}.md missing begin_operation"
    assert "state_end_operation" in body, f"{skill}.md missing end_operation"


@pytest.mark.parametrize("skill", ["opportunity_research", "scan_life_units", "scan_inventory"])
def test_delegating_skills_end_operation_before_inbox_handoff(skill):
    """Skills that hand off to inbox must close their own operation
    first so the trace doesn't conflate the producing flow with the
    reviewing flow."""
    body = (SKILLS_DIR / f"{skill}.md").read_text()
    # The end_operation call should appear before the inbox skill is
    # invoked in the handoff section.
    handoff_idx = body.find("skill://inbox")
    end_op_in_handoff = body.find("state_end_operation", 0, handoff_idx)
    assert end_op_in_handoff >= 0, (
        f"{skill}.md: end_operation not called before skill://inbox handoff"
    )


def test_trace_skill_ships_with_actionable_frontmatter():
    from meta_assistant.server import _parse_skill_description
    path = SKILLS_DIR / "trace.md"
    assert path.exists()
    desc = _parse_skill_description(path)
    assert desc is not None
    assert "Triggers" in desc
    assert "Not for" in desc


# --- server wiring smoke -------------------------------------------------


def test_server_registers_v6_tools(tmp_path):
    mcp, _ = _build_test_server(tmp_path)
    tool_names = {t.name for t in mcp._tool_manager.list_tools()}
    expected = {
        "state_begin_operation",
        "state_end_operation",
        "state_list_recent_chats",
        "state_list_recent_operations",
        "state_get_operation_trace",
        "state_get_chat_trace",
        "state_list_abandoned_operations",
        "state_list_errors",
        "state_purge_logs",
    }
    assert expected <= tool_names
