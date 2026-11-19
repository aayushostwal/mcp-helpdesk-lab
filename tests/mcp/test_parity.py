"""All three variants must return the same JSON for the same operation.

Each parametrised case runs the same scenario on its own fresh database (the root fixtures give
every test a clean SQLite file and the frozen clock) and records the results. The last test
compares the three recordings; it is skipped when the cases did not all run (``-k``).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from helpdesk_mcp.variants import VARIANTS
from tests.conftest import create_ticket
from tests.mcp.conftest import Connect, invoke, result_json

Recorded = list[tuple[str, bool, Any]]
RECORDINGS: dict[str, Recorded] = {}

SCENARIO: list[tuple[str, dict[str, Any]]] = [
    # reads
    ("tickets_get", {"ticket_id": "T-0001"}),
    ("tickets_list", {"status": "new", "limit": 5}),
    ("customers_list", {"q": "Cora"}),
    ("customers_get_summary", {"customer_id": "C-0001"}),
    ("orders_get", {"order_id": "O-0001"}),
    ("orders_list_items", {"order_id": "O-0001"}),
    ("sla_policies_list", {}),
    ("kb_articles_search", {"q": "password"}),
    ("reports_backlog", {}),
    # writes (each returns data that depends on the state the earlier steps left)
    ("comments_add_note", {"ticket_id": "T-0001", "body": "checked the logs"}),
    ("comments_add_reply", {"ticket_id": "T-0001", "body": "We are on it."}),
    ("refunds_create", {"order_id": "O-0001", "amount": "20.00", "reason": "damaged"}),
    ("refunds_create", {"order_id": "O-0003", "amount": "700.00", "reason": "defective"}),
    ("orders_ship", {"order_id": "O-0004"}),
    ("tickets_add_tag", {"ticket_id": "T-0001", "tag": "billing"}),
    ("tickets_close", {"ticket_id": "T-0001", "note": "resolved by phone"}),
    ("refunds_list", {}),
    ("tickets_get", {"ticket_id": "T-0001"}),
    # errors
    ("refunds_create", {"order_id": "O-0002", "amount": "10.00", "reason": "too late"}),
    ("tickets_get", {"ticket_id": "T-9999"}),
    ("tickets_reopen", {"ticket_id": "T-0001", "reason": "again"}),
]


def strip_request_ids(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: strip_request_ids(v) for k, v in node.items() if k != "request_id"}
    if isinstance(node, list):
        return [strip_request_ids(v) for v in node]
    return node


@pytest.mark.parametrize("variant", VARIANTS)
async def test_scenario(
    variant: str, connect: Connect, client: httpx.AsyncClient, admin: dict[str, str]
) -> None:
    await create_ticket(client, admin)
    recorded: Recorded = []
    async with connect(variant) as (session, _):
        for op_id, args in SCENARIO:
            result = await invoke(session, variant, op_id, args)
            recorded.append((op_id, bool(result.isError), strip_request_ids(result_json(result))))
    RECORDINGS[variant] = recorded
    # sanity: the scenario exercised both outcomes
    assert any(error for _, error, _ in recorded) and any(not error for _, error, _ in recorded)
    by_step = dict(enumerate(recorded))
    assert by_step[11][2]["status"] == "approved"
    assert by_step[12][2]["status"] == "pending_approval"
    assert by_step[18][2]["type"].endswith("refund-not-eligible")


def test_variants_return_identical_results() -> None:
    if set(RECORDINGS) != set(VARIANTS):
        pytest.skip("run the whole module to compare variants")
    reference = RECORDINGS["per-endpoint"]
    for variant in ("toolsets", "search-call"):
        other = RECORDINGS[variant]
        assert len(other) == len(reference)
        for (op, ref_err, ref_body), (_, err, body) in zip(reference, other, strict=True):
            assert (err, body) == (ref_err, ref_body), f"{variant} differs on {op}"
