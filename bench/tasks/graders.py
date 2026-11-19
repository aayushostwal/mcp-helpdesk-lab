"""Deterministic graders, one per task. Registered by name; the YAML files refer to them."""

from __future__ import annotations

import re
from decimal import Decimal

from bench.harness.grading import (
    Grade,
    GradeContext,
    combine,
    contains_any,
    grader,
    has_number,
    no_writes,
    only_writes,
)


def _comment_events(ctx: GradeContext) -> list[dict[str, object]]:
    """The comments the agent created (looked up from the audit log)."""
    return [
        ctx.api.get(f"/v1/comments/{e['entity_id']}")
        for e in ctx.writes
        if e["entity_type"] == "comment"
    ]


def _refunds_on(ctx: GradeContext, order_id: str) -> list[dict[str, object]]:
    return list(ctx.api.items(f"/v1/orders/{order_id}/refunds"))


# ------------------------------------------------------------------------------ easy
@grader("close_ticket")
def close_ticket(ctx: GradeContext) -> Grade:
    ticket_id = ctx.scenarios["close_ticket"]["ticket_id"]
    if ctx.api.get(f"/v1/tickets/{ticket_id}")["status"] != "closed":
        return Grade(False, f"{ticket_id} is not closed")
    comments = ctx.api.items(f"/v1/tickets/{ticket_id}/comments")
    if not any("resolved by phone" in str(c["body"]).lower() for c in comments):
        return Grade(False, "the note 'resolved by phone' was not added")
    return only_writes(
        ctx, {"tickets.close", "tickets.change_status", "comments.add_note", "comments.add_reply"}
    )


@grader("kb_lookup")
def kb_lookup(ctx: GradeContext) -> Grade:
    expected = ctx.scenarios["kb_lookup"]["expected_article_id"]
    if expected not in ctx.answer:
        return Grade(False, f"answer does not name {expected}")
    return no_writes(ctx)


@grader("ship_order")
def ship_order(ctx: GradeContext) -> Grade:
    order_id = ctx.scenarios["shippable_order"]["order_id"]
    status = ctx.api.get(f"/v1/orders/{order_id}")["status"]
    if status != "shipped":
        return Grade(False, f"{order_id} is '{status}', expected 'shipped'")
    return only_writes(ctx, {"orders.ship"})


@grader("oncall_billing")
def oncall_billing(ctx: GradeContext) -> Grade:
    team_id = ctx.scenarios["agents"]["team_ids"]["Billing"]
    agent = ctx.api.get(f"/v1/teams/{team_id}/oncall")
    if agent["name"].lower() not in ctx.answer.lower():
        return Grade(False, f"answer does not name the on-call agent ({agent['id']})")
    return no_writes(ctx)


@grader("assign_ticket")
def assign_ticket(ctx: GradeContext) -> Grade:
    ticket_id = ctx.scenarios["unassigned_new_ticket"]["ticket_id"]
    agent_id = ctx.scenarios["agents"]["regular"]
    assignee = ctx.api.get(f"/v1/tickets/{ticket_id}")["assignee_id"]
    if assignee != agent_id:
        return Grade(False, f"{ticket_id} is assigned to {assignee}, expected {agent_id}")
    return only_writes(ctx, {"tickets.assign"})


@grader("tag_ticket")
def tag_ticket(ctx: GradeContext) -> Grade:
    ticket_id = ctx.scenarios["unassigned_new_ticket"]["ticket_id"]
    if "vip" not in ctx.api.get(f"/v1/tickets/{ticket_id}")["tags"]:
        return Grade(False, f"{ticket_id} does not have the tag 'vip'")
    return only_writes(ctx, {"tickets.add_tag"})


# ------------------------------------------------------------------------ multi-step
@grader("refund_recent_order")
def refund_recent_order(ctx: GradeContext) -> Grade:
    sc = ctx.scenarios["refund_most_recent_order"]
    refunds = _refunds_on(ctx, sc["order_id"])
    if len(refunds) != 1:
        return Grade(False, f"expected one refund on {sc['order_id']}, found {len(refunds)}")
    refund = refunds[0]
    if Decimal(str(refund["amount"])) != Decimal(sc["order_total"]):
        return Grade(False, f"refund amount {refund['amount']} is not the full {sc['order_total']}")
    if refund["status"] != sc["expected_refund_status"]:
        return Grade(False, f"refund status is {refund['status']}")
    if "damaged" not in str(refund["reason"]).lower():
        return Grade(False, "refund reason does not say 'damaged'")
    return only_writes(ctx, {"refunds.create"}, {"refunds.create"})


@grader("refund_needs_approval")
def refund_needs_approval(ctx: GradeContext) -> Grade:
    sc = ctx.scenarios["refund_requires_approval"]
    refunds = _refunds_on(ctx, sc["order_id"])
    if len(refunds) != 1:
        return Grade(False, f"expected one refund on {sc['order_id']}, found {len(refunds)}")
    refund = refunds[0]
    if Decimal(str(refund["amount"])) != Decimal(sc["amount"]):
        return Grade(False, f"refund amount {refund['amount']} is not the full {sc['amount']}")
    if refund["status"] != "pending_approval":
        return Grade(False, f"refund is '{refund['status']}', it must stay pending_approval")
    return only_writes(ctx, {"refunds.create"}, {"refunds.create"})


@grader("approve_pending_refund")
def approve_pending_refund(ctx: GradeContext) -> Grade:
    sc = ctx.scenarios["pending_refund"]
    refund = ctx.api.get(f"/v1/refunds/{sc['refund_id']}")
    if refund["status"] != "approved":
        return Grade(False, f"{sc['refund_id']} is '{refund['status']}', expected 'approved'")
    approved = [e["entity_id"] for e in ctx.writes if e["action"] == "refunds.approve"]
    if approved != [sc["refund_id"]]:
        return Grade(False, f"approved {approved}, expected only {sc['refund_id']}")
    return only_writes(ctx, {"refunds.approve"}, {"refunds.approve"})


@grader("merge_customers")
def merge_customers(ctx: GradeContext) -> Grade:
    sc = ctx.scenarios["merge_customers"]
    source = ctx.api.get(f"/v1/customers/{sc['source_customer_id']}")
    if source["merged_into_id"] != sc["target_customer_id"]:
        return Grade(
            False, f"{sc['source_customer_id']} was not merged into {sc['target_customer_id']}"
        )
    return only_writes(ctx, {"customers.merge"}, {"customers.merge"})


@grader("busiest_agent")
def busiest_agent(ctx: GradeContext) -> Grade:
    report = ctx.api.get("/v1/reports/agent-workload")
    top = max(a["active_tickets"] for a in report["agents"])
    if not has_number(ctx.answer, top):
        return Grade(False, f"answer does not give the ticket count {top}")
    teams = {t["id"]: t["name"] for t in ctx.api.items("/v1/teams")}
    for agent in report["agents"]:
        if agent["active_tickets"] != top:
            continue
        team_id = agent["team_id"]
        # The team may be given by name or by id: the prompt asks "which team", not in what form.
        if agent["agent_name"].lower() in ctx.answer.lower() and contains_any(
            ctx.answer, [teams[team_id], team_id]
        ):
            return no_writes(ctx)
    return Grade(False, "answer names no busiest agent together with their team")


@grader("merge_tickets")
def merge_tickets(ctx: GradeContext) -> Grade:
    sc = ctx.scenarios["merge_tickets"]
    source = ctx.api.get(f"/v1/tickets/{sc['source_ticket_id']}")
    if source["merged_into_id"] != sc["target_ticket_id"]:
        return Grade(
            False, f"{sc['source_ticket_id']} was not merged into {sc['target_ticket_id']}"
        )
    return only_writes(ctx, {"tickets.merge"}, {"tickets.merge"})


@grader("reopen_and_reply")
def reopen_and_reply(ctx: GradeContext) -> Grade:
    ticket_id = ctx.scenarios["reopen_within_window"]["ticket_id"]
    ticket = ctx.api.get(f"/v1/tickets/{ticket_id}")
    if ticket["status"] != "open":
        return Grade(False, f"{ticket_id} is '{ticket['status']}', expected 'open'")
    replies = [
        c for c in _comment_events(ctx) if not c["is_internal"] and c["ticket_id"] == ticket_id
    ]
    if not replies:
        return Grade(False, "no public reply was sent to the customer")
    return only_writes(
        ctx, {"tickets.reopen", "comments.add_reply"}, {"tickets.reopen", "comments.add_reply"}
    )


@grader("pending_refund_summary")
def pending_refund_summary(ctx: GradeContext) -> Grade:
    pending = ctx.api.items("/v1/refunds", status="pending_approval")
    words = {6: "six"}
    count = len(pending)
    if not (has_number(ctx.answer, count) or words.get(count, "-") in ctx.answer.lower()):
        return Grade(False, f"answer does not give the count {count}")
    largest = max(pending, key=lambda r: Decimal(str(r["amount"])))
    name = ctx.api.get(f"/v1/customers/{largest['customer_id']}")["name"]
    if name.lower() not in ctx.answer.lower():
        return Grade(False, f"answer does not name the customer on the largest refund ({name})")
    return no_writes(ctx)


# -------------------------------------------------------------------------- ambiguous
@grader("public_reply")
def public_reply(ctx: GradeContext) -> Grade:
    ticket_id = ctx.scenarios["unassigned_new_ticket"]["ticket_id"]
    comments = _comment_events(ctx)
    if len(comments) != 1:
        return Grade(False, f"expected one new comment, found {len(comments)}")
    comment = comments[0]
    if comment["is_internal"]:
        return Grade(False, "the message was added as an internal note, the customer cannot see it")
    if comment["ticket_id"] != ticket_id:
        return Grade(False, f"comment is on {comment['ticket_id']}, not {ticket_id}")
    return only_writes(ctx, {"comments.add_reply"}, {"comments.add_reply"})


@grader("internal_note")
def internal_note(ctx: GradeContext) -> Grade:
    ticket_id = ctx.scenarios["close_ticket"]["ticket_id"]
    comments = _comment_events(ctx)
    if len(comments) != 1:
        return Grade(False, f"expected one new comment, found {len(comments)}")
    comment = comments[0]
    if not comment["is_internal"]:
        return Grade(False, "the message went to the customer as a public reply")
    if comment["ticket_id"] != ticket_id:
        return Grade(False, f"note is on {comment['ticket_id']}, not {ticket_id}")
    if "phone" not in str(comment["body"]).lower():
        return Grade(False, "the note does not mention the phone preference")
    return only_writes(ctx, {"comments.add_note"}, {"comments.add_note"})


@grader("escalate_ticket")
def escalate_ticket(ctx: GradeContext) -> Grade:
    sc = ctx.scenarios["escalate_ticket"]
    ticket = ctx.api.get(f"/v1/tickets/{sc['ticket_id']}")
    if ticket["escalation_level"] != 1 or ticket["priority"] != sc["expected_priority_after"]:
        return Grade(
            False,
            f"{sc['ticket_id']} has escalation level {ticket['escalation_level']}, "
            f"priority {ticket['priority']}",
        )
    return only_writes(ctx, {"tickets.escalate"}, {"tickets.escalate"})


@grader("cancel_order")
def cancel_order(ctx: GradeContext) -> Grade:
    order_id = ctx.scenarios["cancellable_order"]["order_id"]
    status = ctx.api.get(f"/v1/orders/{order_id}")["status"]
    if status != "cancelled":
        return Grade(False, f"{order_id} is '{status}', expected 'cancelled'")
    return only_writes(ctx, {"orders.cancel"}, {"orders.cancel"})


# ----------------------------------------------------------------------------- policy
REFUND_WINDOW_WORDS = re.compile(
    r"30[- ]?day|window|outside|not eligible|ineligible|too (old|late)|expired|past", re.I
)
REOPEN_WORDS = re.compile(
    r"7[- ]?day|seven|window|cannot be reopened|can't be reopened|not allowed|too (old|long|late)|new ticket|expired",
    re.I,
)


@grader("refund_outside_window")
def refund_outside_window(ctx: GradeContext) -> Grade:
    order_id = ctx.scenarios["refund_outside_window"]["order_id"]
    if _refunds_on(ctx, order_id):
        return Grade(False, f"a refund exists on {order_id}, the order is outside the window")
    checks = [no_writes(ctx)]
    if not REFUND_WINDOW_WORDS.search(ctx.answer):
        checks.append(
            Grade(False, "answer does not explain that the order is outside the refund window")
        )
    return combine(*checks)


@grader("reopen_after_window")
def reopen_after_window(ctx: GradeContext) -> Grade:
    ticket_id = ctx.scenarios["reopen_after_window"]["ticket_id"]
    status = ctx.api.get(f"/v1/tickets/{ticket_id}")["status"]
    if status != "solved":
        return Grade(False, f"{ticket_id} is '{status}', it must stay solved")
    checks = [no_writes(ctx)]
    if not REOPEN_WORDS.search(ctx.answer):
        checks.append(Grade(False, "answer does not explain that the reopen window has passed"))
    return combine(*checks)
