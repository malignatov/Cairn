"""Regression tests for the pre-publication hardening pass:
- update() validates patch keys against real columns (SQLi + enum-bypass guard)
- the HTTP server binds loopback by default
- promote_idea / commit_draft are crash-safe (single-txn / compensating-delete)
"""

import os

import pytest

import meta_assistant.storage as S
from meta_assistant.storage import Storage


# --- update() patch-key validation ------------------------------------------


def test_update_rejects_unknown_patch_key(storage: Storage):
    p = storage.write("project", {"name": "P"})
    with pytest.raises(ValueError, match="no column"):
        storage.update("project", p["id"], {"nonexistent_col": "x"})


def test_update_rejects_injection_style_key(storage: Storage):
    """A crafted key that tries to smuggle SQL is not a real column → rejected,
    so it can't reach the SET clause."""
    p = storage.write("project", {"name": "P"})
    with pytest.raises(ValueError, match="no column"):
        storage.update("project", p["id"], {"status = 'killed', name": "pwned"})
    # the legitimate status validator is still in force for the real column
    with pytest.raises(ValueError):
        storage.update("project", p["id"], {"status": "bogus"})


def test_update_still_works_for_real_columns(storage: Storage):
    p = storage.write("project", {"name": "P"})
    out = storage.update("project", p["id"], {"description": "now described"})
    assert out["description"] == "now described"


# --- server binds loopback by default ---------------------------------------


def test_server_binds_loopback_by_default():
    import meta_assistant.server as srv
    if not os.environ.get("META_HOST"):
        assert srv.HOST == "127.0.0.1", (
            "the source default must be loopback so `python -m meta_assistant` "
            "is not exposed to the network"
        )


# --- transaction safety -----------------------------------------------------


def _fail_on_nth_now_iso(monkeypatch, n):
    """Make storage.now_iso raise on its n-th call (1-indexed), passing through
    otherwise — lets us inject a failure at a precise step."""
    real = S.now_iso
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == n:
            raise RuntimeError("injected failure")
        return real()

    monkeypatch.setattr(S, "now_iso", flaky)


def test_promote_idea_rolls_back_on_failure(storage: Storage, monkeypatch):
    """promote_idea does the project INSERT and the idea UPDATE in one
    transaction: if the second step fails, the project must not leak and the
    idea must remain promotable."""
    idea = storage.write("idea", {"title": "X", "body": "y"})
    n_before = len(storage.query("project"))
    # call 1 = project row's now_iso (inside _create_project), call 2 = idea UPDATE
    _fail_on_nth_now_iso(monkeypatch, 2)
    with pytest.raises(RuntimeError):
        storage.promote_idea(idea["id"])
    monkeypatch.undo()
    assert len(storage.query("project")) == n_before, "orphan project leaked"
    after = [i for i in storage.query("idea") if i["id"] == idea["id"]][0]
    assert after["status"] == "spark"
    assert after["promoted_to_project_id"] is None


def test_commit_draft_compensates_on_failure(storage: Storage, monkeypatch):
    """If marking the draft committed fails, the just-created entity is rolled
    back so a retry can't produce a duplicate; the draft stays pending."""
    d = storage.create_draft("project", {"name": "Z"})
    n_before = len(storage.query("project"))
    # call 1 = project row's now_iso (in write→_create_project), call 2 = draft UPDATE
    _fail_on_nth_now_iso(monkeypatch, 2)
    with pytest.raises(RuntimeError):
        storage.commit_draft(d["id"])
    monkeypatch.undo()
    assert len(storage.query("project")) == n_before, "entity not compensated"
    still = [x for x in storage.list_drafts() if x["id"] == d["id"]][0]
    assert still["status"] == "pending"
