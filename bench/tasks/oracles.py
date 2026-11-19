"""Scripted solutions, one per grader. They use the API client directly (no model).

An oracle proves a task is solvable and that its grader passes on a correct solution. It returns
the final answer text that an agent would give.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from bench.harness.api import Api
from bench.harness.grading import oracle


def _sc(fx: dict[str, Any], name: str) -> dict[str, Any]:
    return dict(fx["scenarios"][name])


@oracle("close_ticket")
def close_ticket(api: Api, fx: dict[str, Any]) -> str:
    api.post(
        f"/v1/tickets/{_sc(fx, 'close_ticket')['ticket_id']}/close", {"note": "resolved by phone"}
    )
    return "Closed."


@oracle("kb_lookup")
def kb_lookup(api: Api, fx: dict[str, Any]) -> str:
    hits = api.get("/v1/kb/articles/search", q="reset password")
    return f"{hits[0]['id']}: {hits[0]['title']}"


@oracle("ship_order")
def ship_order(api: Api, fx: dict[str, Any]) -> str:
    api.post(f"/v1/orders/{_sc(fx, 'shippable_order')['order_id']}/ship")
    return "Shipped."


@oracle("oncall_billing")
def oncall_billing(api: Api, fx: dict[str, Any]) -> str:
    teams = {t["name"]: t["id"] for t in api.items("/v1/teams")}
    agent = api.get(f"/v1/teams/{teams['Billing']}/oncall")
    return f"{agent['name']} is on call for Billing."


@oracle("assign_ticket")
def assign_ticket(api: Api, fx: dict[str, Any]) -> str:
    ticket = _sc(fx, "unassigned_new_ticket")["ticket_id"]
    api.post(f"/v1/tickets/{ticket}/assign", {"assignee_id": _sc(fx, "agents")["regular"]})
    return "Assigned."


@oracle("tag_ticket")
def tag_ticket(api: Api, fx: dict[str, Any]) -> str:
    api.post(f"/v1/tickets/{_sc(fx, 'unassigned_new_ticket')['ticket_id']}/tags", {"tag": "vip"})
    return "Tagged."


@oracle("refund_recent_order")
def refund_recent_order(api: Api, fx: dict[str, Any]) -> str:
    sc = _sc(fx, "refund_most_recent_order")
    ticket = api.get(f"/v1/tickets/{sc['ticket_id']}")
    orders = api.items(f"/v1/customers/{ticket['customer_id']}/orders")
    latest = max(orders, key=lambda o: o["placed_at"])
    api.post(
        "/v1/refunds",
        {
            "order_id": latest["id"],
            "amount": latest["total"],
            "reason": "damaged",
            "ticket_id": sc["ticket_id"],
        },
    )
    return f"Refunded {latest['total']} on {latest['id']}."


@oracle("refund_needs_approval")
def refund_needs_approval(api: Api, fx: dict[str, Any]) -> str:
    sc = _sc(fx, "refund_requires_approval")
    latest = max(
        api.items(f"/v1/customers/{sc['customer_id']}/orders"), key=lambda o: o["placed_at"]
    )
    refund = api.post(
        "/v1/refunds",
        {"order_id": latest["id"], "amount": latest["total"], "reason": "changed mind"},
    )
    return f"Filed {refund['id']}; it is {refund['status']} and needs a lead to approve it."


@oracle("approve_pending_refund")
def approve_pending_refund(api: Api, fx: dict[str, Any]) -> str:
    sc = _sc(fx, "pending_refund")
    refund = next(
        r
        for r in api.items("/v1/refunds", order_id=sc["order_id"])
        if r["status"] == "pending_approval"
    )
    api.post(f"/v1/refunds/{refund['id']}/approve")
    return f"Approved {refund['id']}."


@oracle("merge_customers")
def merge_customers(api: Api, fx: dict[str, Any]) -> str:
    sc = _sc(fx, "merge_customers")
    source = api.get(f"/v1/tickets/{sc['source_ticket_id']}")["customer_id"]
    target = api.get(f"/v1/tickets/{sc['target_ticket_id']}")["customer_id"]
    api.post(f"/v1/customers/{source}/merge", {"target_customer_id": target})
    return f"Merged {source} into {target}."


@oracle("busiest_agent")
def busiest_agent(api: Api, fx: dict[str, Any]) -> str:
    agents = api.get("/v1/reports/agent-workload")["agents"]
    top = max(agents, key=lambda a: a["active_tickets"])
    team = api.get(f"/v1/teams/{top['team_id']}")["name"]
    return f"{top['agent_name']} ({team}) has {top['active_tickets']} active tickets."


@oracle("merge_tickets")
def merge_tickets(api: Api, fx: dict[str, Any]) -> str:
    sc = _sc(fx, "merge_tickets")
    a, b = (api.get(f"/v1/tickets/{t}") for t in (sc["source_ticket_id"], sc["target_ticket_id"]))
    older, newer = sorted((a, b), key=lambda t: t["created_at"])
    api.post(f"/v1/tickets/{newer['id']}/merge", {"target_ticket_id": older["id"]})
    return f"Merged {newer['id']} into {older['id']}."


@oracle("reopen_and_reply")
def reopen_and_reply(api: Api, fx: dict[str, Any]) -> str:
    ticket = _sc(fx, "reopen_within_window")["ticket_id"]
    api.post(f"/v1/tickets/{ticket}/reopen", {"reason": "fix did not hold"})
    api.post(f"/v1/tickets/{ticket}/replies", {"body": "Sorry about that, we are looking again."})
    return "Reopened and replied."


@oracle("pending_refund_summary")
def pending_refund_summary(api: Api, fx: dict[str, Any]) -> str:
    pending = api.items("/v1/refunds", status="pending_approval")
    largest = max(pending, key=lambda r: Decimal(str(r["amount"])))
    name = api.get(f"/v1/customers/{largest['customer_id']}")["name"]
    return f"{len(pending)} refunds are waiting. The largest is for {name}."


@oracle("public_reply")
def public_reply(api: Api, fx: dict[str, Any]) -> str:
    ticket = _sc(fx, "unassigned_new_ticket")["ticket_id"]
    api.post(
        f"/v1/tickets/{ticket}/replies",
        {"body": "We are checking and will get back to you by tomorrow."},
    )
    return "Replied."


@oracle("internal_note")
def internal_note(api: Api, fx: dict[str, Any]) -> str:
    ticket = _sc(fx, "close_ticket")["ticket_id"]
    api.post(f"/v1/tickets/{ticket}/notes", {"body": "Customer prefers phone calls over email."})
    return "Note added."


@oracle("escalate_ticket")
def escalate_ticket(api: Api, fx: dict[str, Any]) -> str:
    api.post(
        f"/v1/tickets/{_sc(fx, 'escalate_ticket')['ticket_id']}/escalate",
        {"reason": "outage, angry customer"},
    )
    return "Escalated."


@oracle("cancel_order")
def cancel_order(api: Api, fx: dict[str, Any]) -> str:
    api.post(f"/v1/orders/{_sc(fx, 'cancellable_order')['order_id']}/cancel")
    return "Cancelled."


@oracle("refund_outside_window")
def refund_outside_window(api: Api, fx: dict[str, Any]) -> str:
    order = api.get(f"/v1/orders/{_sc(fx, 'refund_outside_window')['order_id']}")
    return f"I did not refund {order['id']}: it was delivered more than 30 days ago, outside the refund window."


@oracle("reopen_after_window")
def reopen_after_window(api: Api, fx: dict[str, Any]) -> str:
    ticket = api.get(f"/v1/tickets/{_sc(fx, 'reopen_after_window')['ticket_id']}")
    return f"{ticket['id']} was solved more than 7 days ago, so it cannot be reopened. Open a new ticket."
