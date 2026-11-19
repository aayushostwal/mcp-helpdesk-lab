"""Choosing which operations a ``per-endpoint`` server exposes for ``--tool-count N``.

Rule: take the operations in :data:`ALWAYS_INCLUDED` first (in that order), then fill with the
remaining operations sorted by ``(tag, operationId)`` until N tools are chosen. The result only
depends on N and the OpenAPI document, so every run and every machine gets the same tools.
"""

from __future__ import annotations

from helpdesk_mcp.spec import Spec

# Operations the benchmark tasks need (bench/tasks/*.yaml, ``needs_operations``), most important
# first. It is a priority order: a small N keeps only the first N entries. The first 23 entries are
# exactly the union of every task's ``needs_operations``, so every task can be solved at 25, 50 and
# 100 tools (a test in tests/bench checks this). The first ten were chosen to make as many tasks
# as possible solvable at N=10 (ten of the twenty); the rest are unsolvable at N=10 by design and
# the benchmark skips them there (docs/benchmark.md). The last two are filler to reach 25 tools.
ALWAYS_INCLUDED: tuple[str, ...] = (
    "tickets_get",  # read a ticket (many tasks start here)
    "customers_list_orders",  # "the most recent order of the customer who opened T-0217"
    "refunds_create",  # refund tasks, including the policy cases
    "comments_add_reply",  # public reply (ambiguous: reply vs note)
    "comments_add_note",  # internal note (ambiguous: reply vs note)
    "tickets_escalate",  # escalate (ambiguous: escalate vs assign)
    "orders_cancel",  # cancel (ambiguous: cancel vs refund)
    "orders_get",  # order total, status, delivery date
    "tickets_close",  # "close ticket T-1042 with a note"
    "kb_articles_search",  # knowledge base lookup
    # Needed only by tasks that are unsolvable at N=10:
    "orders_ship",
    "tickets_assign",
    "tickets_add_tag",
    "refunds_list",
    "refunds_approve",  # lead approval of refunds above 500.00
    "tickets_reopen",  # reopen inside and outside the 7-day window
    "tickets_merge",  # merge duplicate tickets
    "customers_merge",  # merge duplicate customers
    "customers_get",
    "teams_list",
    "teams_get",
    "teams_get_oncall",
    "reports_agent_workload",  # busiest agent
    # Filler: plausible neighbours that no task needs.
    "tickets_list",
    "customers_list",
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
