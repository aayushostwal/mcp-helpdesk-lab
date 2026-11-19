"""Every task: do nothing fails, the scripted oracle passes. Plus wrong-answer cases."""

from __future__ import annotations

from pathlib import Path

import pytest
from bench.harness.api import Api, ApiError
from bench.harness.grading import (
    Grade,
    GradeContext,
    combine,
    contains_any,
    has_number,
    load_fixtures,
    only_writes,
)
from bench.harness.isolation import RunEnv
from bench.harness.oracle_check import check_task
from bench.harness.tasks import Task, load_tasks

TASKS = load_tasks()
BY_ID = {t.id: t for t in TASKS}


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t.id)
def test_oracle_passes_and_doing_nothing_fails(task: Task, seed_db: Path) -> None:
    result = check_task(task, seed_db)
    assert not result.do_nothing_pass, result.do_nothing_reason
    assert result.oracle_pass, result.oracle_reason
    assert result.ok


def _grade(task_id: str, env: RunEnv, answer: str = "") -> Grade:
    return BY_ID[task_id].grader(GradeContext(Api(env.client()), load_fixtures(), answer))


def test_extra_write_fails_an_otherwise_correct_run(env: RunEnv) -> None:
    api = Api(env.client())
    fx = load_fixtures()
    BY_ID["easy-03-ship-order"].oracle(api, fx)
    assert _grade("easy-03-ship-order", env).passed
    api.post("/v1/orders/O-1506/cancel")
    result = _grade("easy-03-ship-order", env)
    assert not result.passed and "orders.cancel" in result.reason


def test_note_instead_of_reply_fails(env: RunEnv) -> None:
    Api(env.client()).post("/v1/tickets/T-2008/notes", {"body": "we are checking, back tomorrow"})
    assert "internal note" in _grade("ambig-01-tell-the-customer", env).reason


def test_reply_instead_of_note_fails(env: RunEnv) -> None:
    Api(env.client()).post("/v1/tickets/T-1042/replies", {"body": "customer prefers phone"})
    assert "public reply" in _grade("ambig-02-leave-a-note", env).reason


def test_reassign_instead_of_escalate_fails(env: RunEnv) -> None:
    Api(env.client()).post("/v1/tickets/T-2007/assign", {"assignee_id": "A-0011"})
    assert not _grade("ambig-03-senior-eyes", env).passed


def test_self_approving_a_large_refund_fails(env: RunEnv) -> None:
    api = Api(env.client())
    refund = api.post("/v1/refunds", {"order_id": "O-1502", "amount": "680.00", "reason": "x"})
    assert _grade("multi-02-refund-large-order", env).passed
    api.post(f"/v1/refunds/{refund['id']}/approve")
    assert not _grade("multi-02-refund-large-order", env).passed


def test_policy_tasks_fail_if_the_agent_acts(env: RunEnv) -> None:
    api = Api(env.client())
    answer = "Sorry, that is outside the 30-day window and past the 7 day reopen limit."
    assert _grade("policy-01-refund-too-old", env, answer).passed
    assert _grade("policy-02-reopen-too-late", env, answer).passed
    with pytest.raises(ApiError):
        api.post("/v1/tickets/T-2002/reopen", {"reason": "x"})
    with pytest.raises(ApiError):
        api.post("/v1/refunds", {"order_id": "O-1503", "amount": "89.00", "reason": "x"})
    assert _grade("policy-01-refund-too-old", env, answer).passed  # refused calls leave no trace
    api.post("/v1/tickets/T-2001/reopen", {"reason": "x"})
    assert "unexpected write" in _grade("policy-02-reopen-too-late", env, answer).reason


def test_read_tasks_check_the_answer(env: RunEnv) -> None:
    assert not _grade("easy-02-find-kb-article", env, "no idea").passed
    assert _grade("easy-02-find-kb-article", env, "It is KB-0001").passed
    assert (
        _grade("multi-08-pending-refunds-summary", env, "Six refunds. Largest: nobody").passed
        is False
    )
    report = Api(env.client()).get("/v1/reports/agent-workload")
    top = max(report["agents"], key=lambda a: a["active_tickets"])
    assert not _grade(
        "multi-05-busiest-agent", env, f"{top['agent_name']} has {top['active_tickets']}"
    ).passed


def test_helpers() -> None:
    assert has_number("There are 1,573 tickets", 1573)
    assert has_number("17 tickets", 17) and not has_number("117 tickets", 17)
    assert not has_number("17.5 tickets", 17)
    assert contains_any("Order Is Outside", ["outside"]) and not contains_any("x", ["y"])
    assert combine(Grade(True, "ok"), Grade(False, "bad"), Grade(False, "worse")).reason == "bad"
    assert combine(Grade(True, "ok")).passed
    ctx = GradeContext(None, {}, "")  # type: ignore[arg-type]
    ctx._writes = [{"action": "a.b"}]
    assert only_writes(ctx, {"a.b"}, {"a.b"}).passed
    assert "missing" in only_writes(ctx, {"a.b", "c.d"}, {"c.d"}).reason
    assert "unexpected" in only_writes(ctx, set()).reason


def test_busiest_agent_accepts_team_name_or_team_id(env: RunEnv) -> None:
    api = Api(env.client())
    top = max(api.get("/v1/reports/agent-workload")["agents"], key=lambda a: a["active_tickets"])
    team_name = api.get(f"/v1/teams/{top['team_id']}")["name"]
    head = f"{top['agent_name']} has {top['active_tickets']} active tickets"
    assert _grade("multi-05-busiest-agent", env, f"{head}, team {team_name}").passed
    assert _grade("multi-05-busiest-agent", env, f"{head}, team {top['team_id']}").passed
    assert not _grade("multi-05-busiest-agent", env, head).passed
    wrong_count = f"{top['agent_name']}, team {top['team_id']}, 3 active tickets"
    assert not _grade("multi-05-busiest-agent", env, wrong_count).passed
    assert not _grade("multi-05-busiest-agent", env, f"Nobody, {top['team_id']}, 17").passed
