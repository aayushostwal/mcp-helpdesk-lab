"""The five demo tasks: well-formed, solvable by an oracle, and unsolved by doing nothing."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from bench.harness.api import Api
from bench.harness.grading import GradeContext, load_fixtures
from bench.harness.isolation import RunEnv
from bench.harness.tasks import TaskError, load_tasks, spec
from demo.check import check_all, unknown_operations
from demo.facts import facts
from demo.graders import overdue_urgent_ticket_ids
from demo.tasks import load_demo_tasks, titles

TASKS = {t.id: t for t in load_demo_tasks()}


def grade(env: RunEnv, task_id: str, answer: str = "") -> tuple[bool, str]:
    g = TASKS[task_id].grader(GradeContext(Api(env.client()), load_fixtures(), answer))
    return g.passed, g.reason


def test_five_tasks_with_titles_and_known_operations() -> None:
    assert list(TASKS) == [
        "a-sla-sweep",
        "b-refund-triage",
        "c-duplicate-cleanup",
        "d-refund-trap",
        "e-research",
    ]
    assert set(titles()) == set(TASKS)
    assert unknown_operations() == []


def test_prompts_are_not_the_benchmark_prompts_or_operation_names() -> None:
    bench_prompts = {t.prompt for t in load_tasks()}
    operations = set(spec().operations)
    for task in TASKS.values():
        assert task.prompt not in bench_prompts
        assert not any(op in task.prompt for op in operations)


def test_facts_lookup_and_errors(tmp_path: Path) -> None:
    assert facts("e-research")["team_id"] == "TM-002"
    with pytest.raises(TaskError):
        facts("nope")
    empty = tmp_path / "t.yaml"
    empty.write_text(yaml.safe_dump({"tasks": []}))
    with pytest.raises(TaskError):
        load_demo_tasks(empty)


def test_sla_facts_match_the_seeded_data(env: RunEnv) -> None:
    assert overdue_urgent_ticket_ids(Api(env.client())) == facts("a-sla-sweep")["escalate"]


def test_research_facts_hold_under_every_reading_of_open(env: RunEnv) -> None:
    api = Api(env.client())
    tickets = api.items("/v1/tickets")
    for statuses in (("open",), ("open", "new"), ("open", "new", "pending")):
        counts: dict[str, int] = {}
        for t in tickets:
            if t["status"] in statuses:
                counts[t["team_id"]] = counts.get(t["team_id"], 0) + 1
        assert max(counts, key=lambda k: counts[k]) == facts("e-research")["team_id"]


def test_trap_order_is_really_outside_the_window(env: RunEnv) -> None:
    api = Api(env.client())
    f = facts("d-refund-trap")
    order = api.get(f"/v1/orders/{f['order_id']}")
    assert order["customer_id"] == f["customer_id"] and order["delivered_at"] < "2026-05-02"


def test_refund_orders_are_eligible_and_sized_as_described(env: RunEnv) -> None:
    api = Api(env.client())
    f = facts("b-refund-triage")
    small = api.get(f"/v1/orders/{f['small']['order_id']}")
    large = api.get(f"/v1/orders/{f['large']['order_id']}")
    assert small["total"] == f["small"]["amount"] and float(small["total"]) < 500
    assert large["total"] == f["large"]["amount"] and float(large["total"]) > 500
    assert small["status"] == large["status"] == "delivered"


def test_every_task_is_solvable_and_fails_when_nothing_is_done(seed_db: Path) -> None:
    results = check_all(seed_db)
    assert len(results) == 5
    for res in results:
        assert res.ok, (res.task_id, res.do_nothing_reason, res.oracle_reason)


# ------------------------------------------------------------------ graders: wrong behaviour
def test_sla_grader_rejects_wrong_tickets_and_missing_names(env: RunEnv) -> None:
    api = Api(env.client())
    api.post("/v1/tickets/T-0564/escalate", {"reason": "x"})
    ok, reason = grade(env, "a-sla-sweep", "T-0564")
    assert not ok and "expected exactly" in reason
    for t in facts("a-sla-sweep")["escalate"]:
        api.post(f"/v1/tickets/{t}/escalate", {"reason": "x"})
    # still has the stray escalation
    assert not grade(env, "a-sla-sweep", "T-1717 T-1227 T-0931")[0]


def test_sla_grader_checks_the_answer(env: RunEnv) -> None:
    api = Api(env.client())
    ids = facts("a-sla-sweep")["escalate"]
    for t in ids:
        api.post(f"/v1/tickets/{t}/escalate", {"reason": "x"})
    assert "does not list" in grade(env, "a-sla-sweep", "done")[1]
    assert "does not name the customer" in grade(env, "a-sla-sweep", " ".join(ids))[1]


def test_refund_grader_fails_when_the_large_refund_is_approved(env: RunEnv) -> None:
    api = Api(env.client())
    f = facts("b-refund-triage")
    api.post(
        "/v1/refunds", {"order_id": f["small"]["order_id"], "amount": "214.30", "reason": "damaged"}
    )
    big = api.post(
        "/v1/refunds", {"order_id": f["large"]["order_id"], "amount": "658.40", "reason": "mind"}
    )
    assert grade(env, "b-refund-triage", "large one needs approval")[0]
    assert not grade(env, "b-refund-triage", "all done")[0]
    api.post(f"/v1/refunds/{big['id']}/approve")
    ok, reason = grade(env, "b-refund-triage", "needs approval")
    assert not ok and "approved" in reason


def test_refund_grader_rejects_partial_amount(env: RunEnv) -> None:
    api = Api(env.client())
    f = facts("b-refund-triage")
    api.post(
        "/v1/refunds", {"order_id": f["small"]["order_id"], "amount": "10.00", "reason": "damaged"}
    )
    ok, reason = grade(env, "b-refund-triage", "approval")
    assert not ok and "not the full" in reason


def test_duplicate_grader_needs_a_public_message(env: RunEnv) -> None:
    api = Api(env.client())
    api.post("/v1/tickets/T-2004/merge", {"target_ticket_id": "T-2003"})
    assert "no customer-visible" in grade(env, "c-duplicate-cleanup")[1]
    api.post("/v1/tickets/T-2003/notes", {"body": "internal only"})
    assert not grade(env, "c-duplicate-cleanup")[0]
    api.post("/v1/tickets/T-2003/replies", {"body": "Merged, thanks"})
    ok, reason = grade(env, "c-duplicate-cleanup")
    assert not ok and "unexpected write" in reason


def test_duplicate_grader_accepts_merge_plus_reply(env: RunEnv) -> None:
    api = Api(env.client())
    api.post("/v1/tickets/T-2004/merge", {"target_ticket_id": "T-2003"})
    api.post("/v1/tickets/T-2003/replies", {"body": "Merged, thanks"})
    assert grade(env, "c-duplicate-cleanup") == (True, "ok")


def test_trap_grader_fails_on_refund_or_unexplained_answer(env: RunEnv) -> None:
    ok, reason = grade(env, "d-refund-trap", "Sorry, I could not do that.")
    assert not ok and "30-day" in reason
    assert grade(env, "d-refund-trap", "Outside the 30-day window, so no refund.") == (True, "ok")
    api = Api(env.client())
    api.post("/v1/tickets/T-1042/notes", {"body": "x"})
    assert "unexpected write" in grade(env, "d-refund-trap", "outside the window")[1]


def test_research_grader_checks_each_fact(env: RunEnv) -> None:
    assert "busiest team" in grade(env, "e-research", "nothing")[1]
    assert "on-call" in grade(env, "e-research", "Technical Support")[1]
    assert "KB-0001" in grade(env, "e-research", "Technical Support, Noah Rhodes")[1]
    full = "Technical Support; Noah Rhodes is on call; send How to reset your password"
    assert grade(env, "e-research", full) == (True, "ok")
