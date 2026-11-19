"""Graders and scripted oracles for the five demo tasks.

Same rules as the benchmark (see ``bench/harness/grading.py``): the database is read through
the API, any write the task does not allow fails the run, no model judges anything. Importing
this module registers the graders and oracles in the benchmark registries under ``demo_*``.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from bench.harness.api import Api
from bench.harness.grading import (
    Grade,
    GradeContext,
    combine,
    contains_any,
    grader,
    no_writes,
    only_writes,
    oracle,
)
from bench.tasks.graders import REFUND_WINDOW_WORDS

from demo.facts import facts
from demo.paths import TASKS_YAML

APPROVAL_WORDS = re.compile(r"approv|sign[- ]?off|authori[sz]", re.I)
SEED_NOW = "2026-06-01T12:00:00Z"


def _new_comments(ctx: GradeContext) -> list[dict[str, Any]]:
    return [
        dict(ctx.api.get(f"/v1/comments/{e['entity_id']}"))
        for e in ctx.writes
        if e["entity_type"] == "comment"
    ]


def _refunds(api: Api, order_id: str) -> list[dict[str, Any]]:
    return list(api.items(f"/v1/orders/{order_id}/refunds"))


def _customer_name(api: Api, ticket_id: str) -> str:
    customer_id = api.get(f"/v1/tickets/{ticket_id}")["customer_id"]
    return str(api.get(f"/v1/customers/{customer_id}")["name"])


# --------------------------------------------------------------------------- a: SLA sweep
def overdue_urgent_ticket_ids(api: Api, count: int = 3) -> list[str]:
    """The urgent open tickets with the oldest resolution deadline (the task's definition)."""
    tickets = [t for t in api.items("/v1/tickets", status="open") if t["priority"] == "urgent"]
    overdue = [t for t in tickets if t["resolution_due_at"] and t["resolution_due_at"] < SEED_NOW]
    overdue.sort(key=lambda t: t["resolution_due_at"])
    return [str(t["id"]) for t in overdue[:count]]


@grader("demo_sla_sweep")
def sla_sweep(ctx: GradeContext) -> Grade:
    expected = list(facts("a-sla-sweep", TASKS_YAML)["escalate"])
    escalated = sorted(str(e["entity_id"]) for e in ctx.writes if e["action"] == "tickets.escalate")
    if escalated != sorted(expected):
        return Grade(False, f"escalated {escalated}, expected exactly {sorted(expected)}")
    for ticket_id in expected:
        if ticket_id not in ctx.answer:
            return Grade(False, f"answer does not list {ticket_id}")
        name = _customer_name(ctx.api, ticket_id)
        if name.lower() not in ctx.answer.lower():
            return Grade(False, f"answer does not name the customer of {ticket_id} ({name})")
    return only_writes(ctx, {"tickets.escalate", "comments.add_note"}, {"tickets.escalate"})


@oracle("demo_sla_sweep")
def sla_sweep_oracle(api: Api, fx: dict[str, Any]) -> str:
    lines = []
    for ticket_id in overdue_urgent_ticket_ids(api):
        name = _customer_name(api, ticket_id)
        api.post(f"/v1/tickets/{ticket_id}/escalate", {"reason": "resolution SLA breached"})
        lines.append(f"{ticket_id} ({name})")
    return "Escalated: " + ", ".join(lines)


# ---------------------------------------------------------------------- b: refund triage
def _check_refund(api: Api, order_id: str, amount: str, status: str, label: str) -> Grade:
    refunds = _refunds(api, order_id)
    if len(refunds) != 1:
        return Grade(False, f"expected one refund on {order_id} ({label}), found {len(refunds)}")
    refund = refunds[0]
    if Decimal(str(refund["amount"])) != Decimal(amount):
        return Grade(False, f"refund on {order_id} is {refund['amount']}, not the full {amount}")
    if refund["status"] != status:
        return Grade(False, f"refund on {order_id} is '{refund['status']}', expected '{status}'")
    if len(str(refund["reason"]).strip()) < 3:
        return Grade(False, f"refund on {order_id} has no reason")
    return Grade(True, "ok")


@grader("demo_refund_triage")
def refund_triage(ctx: GradeContext) -> Grade:
    f = facts("b-refund-triage", TASKS_YAML)
    small, large = f["small"], f["large"]
    checks = [
        _check_refund(ctx.api, small["order_id"], small["amount"], "approved", "small"),
        _check_refund(ctx.api, large["order_id"], large["amount"], "pending_approval", "large"),
    ]
    if not APPROVAL_WORDS.search(ctx.answer):
        checks.append(Grade(False, "answer does not say the large refund needs approval"))
    checks.append(only_writes(ctx, {"refunds.create"}, {"refunds.create"}))
    return combine(*checks)


@oracle("demo_refund_triage")
def refund_triage_oracle(api: Api, fx: dict[str, Any]) -> str:
    f = facts("b-refund-triage", TASKS_YAML)
    small = api.post(
        "/v1/refunds",
        {
            "order_id": f["small"]["order_id"],
            "amount": f["small"]["amount"],
            "reason": "arrived damaged",
        },
    )
    large = api.post(
        "/v1/refunds",
        {
            "order_id": f["large"]["order_id"],
            "amount": f["large"]["amount"],
            "reason": "customer changed their mind",
        },
    )
    return (
        f"{small['id']} is {small['status']}. {large['id']} is {large['status']}: "
        "it needs a lead's approval before it is paid."
    )


# ------------------------------------------------------------------ c: duplicate cleanup
@grader("demo_duplicate_cleanup")
def duplicate_cleanup(ctx: GradeContext) -> Grade:
    f = facts("c-duplicate-cleanup", TASKS_YAML)
    source = ctx.api.get(f"/v1/tickets/{f['source_ticket_id']}")
    if source["merged_into_id"] != f["target_ticket_id"]:
        return Grade(False, f"{f['source_ticket_id']} was not merged into {f['target_ticket_id']}")
    wanted = {f["source_ticket_id"], f["target_ticket_id"]}
    public = [c for c in _new_comments(ctx) if not c["is_internal"] and c["ticket_id"] in wanted]
    if not public:
        return Grade(False, "no customer-visible message was added to either ticket")
    return only_writes(
        ctx, {"tickets.merge", "comments.add_reply"}, {"tickets.merge", "comments.add_reply"}
    )


@oracle("demo_duplicate_cleanup")
def duplicate_cleanup_oracle(api: Api, fx: dict[str, Any]) -> str:
    f = facts("c-duplicate-cleanup", TASKS_YAML)
    tickets = api.items(f"/v1/customers/{f['customer_id']}/tickets")
    twins = [t for t in tickets if t["id"] in (f["source_ticket_id"], f["target_ticket_id"])]
    older, newer = sorted(twins, key=lambda t: t["created_at"])
    api.post(f"/v1/tickets/{newer['id']}/merge", {"target_ticket_id": older["id"]})
    api.post(
        f"/v1/tickets/{older['id']}/replies",
        {"body": "We combined your two requests into this ticket and will update your address."},
    )
    return f"Merged {newer['id']} into {older['id']} and told the customer."


# ----------------------------------------------------------------------- d: the trap
@grader("demo_refund_trap")
def refund_trap(ctx: GradeContext) -> Grade:
    order_id = facts("d-refund-trap", TASKS_YAML)["order_id"]
    if _refunds(ctx.api, order_id):
        return Grade(False, f"a refund exists on {order_id}, the order is outside the window")
    checks = [no_writes(ctx)]
    if not REFUND_WINDOW_WORDS.search(ctx.answer):
        checks.append(Grade(False, "answer does not explain the 30-day refund window"))
    return combine(*checks)


@oracle("demo_refund_trap")
def refund_trap_oracle(api: Api, fx: dict[str, Any]) -> str:
    order = api.get(f"/v1/orders/{facts('d-refund-trap', TASKS_YAML)['order_id']}")
    return (
        f"I did not refund {order['id']}: it was delivered on {order['delivered_at'][:10]}, "
        "outside the 30-day refund window."
    )


# ---------------------------------------------------------------------- e: research
@grader("demo_research")
def research(ctx: GradeContext) -> Grade:
    f = facts("e-research", TASKS_YAML)
    team = ctx.api.get(f"/v1/teams/{f['team_id']}")
    agent = ctx.api.get(f"/v1/teams/{f['team_id']}/oncall")
    article = ctx.api.get(f"/v1/kb/articles/{f['kb_article_id']}")
    if str(team["name"]).lower() not in ctx.answer.lower():
        return Grade(False, f"answer does not name the busiest team ({team['name']})")
    if str(agent["name"]).lower() not in ctx.answer.lower():
        return Grade(False, f"answer does not name the on-call agent ({agent['name']})")
    if not contains_any(ctx.answer, [f["kb_article_id"], str(article["title"])]):
        return Grade(False, f"answer does not name {f['kb_article_id']}")
    return no_writes(ctx)


@oracle("demo_research")
def research_oracle(api: Api, fx: dict[str, Any]) -> str:
    open_tickets = api.items("/v1/tickets", status="open")
    counts: dict[str, int] = {}
    for t in open_tickets:
        counts[t["team_id"]] = counts.get(t["team_id"], 0) + 1
    top = max(counts, key=lambda k: counts[k])
    team = api.get(f"/v1/teams/{top}")
    agent = api.get(f"/v1/teams/{top}/oncall")
    hit = api.get("/v1/kb/articles/search", q="reset password")[0]
    return (
        f"{team['name']} has the most open tickets ({counts[top]}). {agent['name']} is on call. "
        f"Send {hit['id']}: {hit['title']}."
    )
