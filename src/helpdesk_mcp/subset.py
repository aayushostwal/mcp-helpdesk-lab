"""Choosing which operations a ``per-endpoint`` server exposes for ``--tool-count N``.

Rule: take the operations in :data:`ALWAYS_INCLUDED` first (in that order), then fill with the
remaining operations sorted by ``(tag, operationId)`` until N tools are chosen. The result only
depends on N and the OpenAPI document, so every run and every machine gets the same tools.
"""

from __future__ import annotations

from helpdesk_mcp.spec import Spec

# Operations the benchmark tasks need (see bench/tasks/fixtures.json and docs/DESIGN.md section 6),
# most important first. Phase 3 can extend this list. It is a priority order: a small N keeps
# only the first N entries, so the first ten cover the most common task steps.
ALWAYS_INCLUDED: tuple[str, ...] = (
    "tickets_get",  # read a ticket (almost every task starts here)
    "tickets_list",  # find tickets by status, customer, SLA breach
    "tickets_close",  # "close ticket T-1042 with a note"
    "customers_list_orders",  # "the most recent order of the customer who opened T-0217"
    "orders_get",  # order total, status, delivery date
    "refunds_create",  # refund tasks, including the policy cases
    "refunds_approve",  # lead approval of refunds above 500.00
    "comments_add_reply",  # public reply (ambiguous: reply vs note)
    "comments_add_note",  # internal note (ambiguous: reply vs note)
    "tickets_escalate",  # escalate (ambiguous: escalate vs assign)
    "tickets_assign",  # assign or reassign
    "tickets_reopen",  # reopen inside and outside the 7-day window
    "tickets_merge",  # merge duplicate tickets
    "customers_merge",  # merge duplicate customers
    "customers_get",
    "customers_list",
    "orders_ship",
    "orders_cancel",
    "refunds_get",
    "kb_articles_search",  # knowledge base lookup
    "agents_list",  # find an agent or team member
    "reports_agent_workload",  # busiest agent
    "sla_list_breaches",  # SLA breach questions
    "tickets_change_status",
    "customers_list_tickets",
)


class ToolCountError(ValueError):
    """``--tool-count`` is out of range."""


def select_operations(spec: Spec, count: int | None) -> list[str]:
    """Return the operation ids to expose, in a stable order. ``None`` means all of them."""
    everything = sorted(spec.operations.values(), key=lambda op: (op.tag, op.operation_id))
    if count is None or count == len(everything):
        return [op.operation_id for op in everything]
    if not 1 <= count <= len(everything):
        raise ToolCountError(f"--tool-count must be between 1 and {len(everything)}, got {count}.")
    core = [op_id for op_id in ALWAYS_INCLUDED if op_id in spec.operations]
    chosen = core[:count]
    for op in everything:
        if len(chosen) >= count:
            break
        if op.operation_id not in chosen:
            chosen.append(op.operation_id)
    return chosen
