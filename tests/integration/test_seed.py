"""The seed is part of the product: the benchmark depends on the facts it records."""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from alembic import command
from fastapi import FastAPI

from helpdesk.api.app import create_app
from helpdesk.cli import alembic_config
from helpdesk.clock import FrozenClock
from helpdesk.config import Settings
from helpdesk.db import make_engine, make_session_factory
from helpdesk.seeding import (
    DEFAULT_AS_OF,
    build_dataset,
    clear_database,
    fingerprint,
    is_empty,
    seed_database,
)
from helpdesk.services.admin import ApiKeyService
from helpdesk.services.base import Context

H = dict[str, str]


def _settings(path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite+aiosqlite:///{path}",
        log_level="WARNING",
        log_json=False,
        webhook_delivery="simulated",
        frozen_time=DEFAULT_AS_OF,
    )


async def _seed(path: Path, seed: int = 42) -> tuple[dict[str, Any], str]:
    engine = make_engine(_settings(path))
    async with make_session_factory(engine)() as session:
        fixtures = await seed_database(session, seed=seed)
        digest = await fingerprint(session)
    await engine.dispose()
    return fixtures, digest


@pytest.fixture(scope="module")
def seeded(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    path = tmp_path_factory.mktemp("seed") / "template.db"
    command.upgrade(alembic_config(_settings(path)), "head")
    fixtures, digest = asyncio.run(_seed(path))
    return {"path": path, "fixtures": fixtures, "fingerprint": digest}


@pytest.fixture
async def seeded_app(
    seeded: dict[str, Any], tmp_path: Path
) -> AsyncIterator[tuple[FastAPI, H, H, dict[str, Any]]]:
    db = tmp_path / "work.db"
    shutil.copy(seeded["path"], db)
    settings = _settings(db)
    app = create_app(settings)
    fx = seeded["fixtures"]
    agents = fx["scenarios"]["agents"]
    async with app.state.session_factory() as session:
        ctx = Context(session=session, clock=FrozenClock(DEFAULT_AS_OF), settings=settings)
        service = ApiKeyService(ctx)
        _, admin_token = await service.create("admin", ["admin"], agents["admin"], None)
        _, lead_token = await service.create("lead", ["admin"], agents["lead"], None)
    yield (
        app,
        {"Authorization": f"Bearer {admin_token}"},
        {"Authorization": f"Bearer {lead_token}"},
        fx,
    )
    await app.state.engine.dispose()


@pytest.fixture
async def api(seeded_app: tuple[FastAPI, H, H, dict[str, Any]]) -> AsyncIterator[httpx.AsyncClient]:
    app = seeded_app[0]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def test_dataset_size_matches_the_design(seeded: dict[str, Any]) -> None:
    counts = seeded["fixtures"]["counts"]
    assert counts["customers"] == 500 and counts["agents"] == 40 and counts["teams"] == 5
    assert 1900 <= counts["tickets"] <= 2100 and 1400 <= counts["orders"] <= 1600
    assert counts["refunds"] > 50 and counts["kb_articles"] >= 10 and counts["macros"] >= 10


async def test_seeding_twice_gives_identical_content(
    seeded: dict[str, Any], tmp_path: Path
) -> None:
    path = tmp_path / "second.db"
    await asyncio.to_thread(command.upgrade, alembic_config(_settings(path)), "head")
    fixtures, digest = await _seed(path)
    assert digest == seeded["fingerprint"]
    assert fixtures == seeded["fixtures"]


async def test_different_seed_gives_different_data(seeded: dict[str, Any], tmp_path: Path) -> None:
    path = tmp_path / "other.db"
    await asyncio.to_thread(command.upgrade, alembic_config(_settings(path)), "head")
    _, digest = await _seed(path, seed=7)
    assert digest != seeded["fingerprint"]


def test_build_dataset_is_pure() -> None:
    a, b = build_dataset(42), build_dataset(42)
    assert a.fixtures == b.fixtures
    assert [t.subject for t in a.tickets] == [t.subject for t in b.tickets]
    assert all(t.created_at < DEFAULT_AS_OF for t in a.tickets)
    emails = [c.email for c in a.customers]
    assert len(emails) == len(set(emails)) and all(e.endswith("@example.com") for e in emails)


async def test_clear_and_is_empty(seeded: dict[str, Any], tmp_path: Path) -> None:
    db = tmp_path / "c.db"
    shutil.copy(seeded["path"], db)
    engine = make_engine(_settings(db))
    async with make_session_factory(engine)() as session:
        assert not await is_empty(session)
        await clear_database(session)
        assert await is_empty(session)
    await engine.dispose()


async def test_fixture_counts_match_api_reports(api: httpx.AsyncClient, seeded_app: Any) -> None:
    _, admin, _, fx = seeded_app
    volume = (
        await api.get("/v1/reports/ticket-volume", params={"group_by": "status"}, headers=admin)
    ).json()
    assert {b["key"]: b["count"] for b in volume["buckets"]} == fx["tickets_by_status"]
    prio = (
        await api.get("/v1/reports/ticket-volume", params={"group_by": "priority"}, headers=admin)
    ).json()
    assert {b["key"]: b["count"] for b in prio["buckets"]} == fx["tickets_by_priority"]
    load = (await api.get("/v1/reports/agent-workload", headers=admin)).json()["agents"][0]
    assert (load["agent_id"], load["active_tickets"]) == (
        fx["busiest_agent"]["agent_id"],
        fx["busiest_agent"]["active_tickets"],
    )
    pending = (
        await api.get(
            "/v1/refunds", params={"status": "pending_approval", "limit": 100}, headers=admin
        )
    ).json()["items"]
    assert sorted(r["id"] for r in pending) == fx["refunds_pending_approval"]


async def test_sql_breach_filter_agrees_with_python_evaluation(
    api: httpx.AsyncClient, seeded_app: Any
) -> None:
    _, admin, _, fx = seeded_app
    total, cursor = 0, None
    while True:
        params: dict[str, Any] = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        page = (await api.get("/v1/sla/breaches", params=params, headers=admin)).json()
        total += len(page["items"])
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert total == fx["sla_breached_ticket_count"]


async def test_scenario_close_ticket(api: httpx.AsyncClient, seeded_app: Any) -> None:
    _, admin, _, fx = seeded_app
    sc = fx["scenarios"]["close_ticket"]
    t = (await api.get(f"/v1/tickets/{sc['ticket_id']}", headers=admin)).json()
    assert t["status"] == "open" and t["id"] == "T-1042"
    closed = await api.post(
        f"/v1/tickets/{sc['ticket_id']}/close", json={"note": "resolved by phone"}, headers=admin
    )
    assert closed.json()["status"] == "closed"


async def test_scenario_refund_most_recent_order_of_ticket_customer(
    api: httpx.AsyncClient, seeded_app: Any
) -> None:
    _, admin, _, fx = seeded_app
    sc = fx["scenarios"]["refund_most_recent_order"]
    ticket = (await api.get(f"/v1/tickets/{sc['ticket_id']}", headers=admin)).json()
    assert ticket["customer_id"] == sc["customer_id"]
    orders = (await api.get(f"/v1/customers/{sc['customer_id']}/orders", headers=admin)).json()[
        "items"
    ]
    assert orders[0]["id"] == sc["order_id"] and orders[0]["total"] == sc["order_total"]
    r = await api.post(
        "/v1/refunds",
        json={"order_id": sc["order_id"], "amount": sc["order_total"], "reason": "damaged"},
        headers=admin,
    )
    assert r.status_code == 201 and r.json()["status"] == sc["expected_refund_status"]


async def test_scenario_refund_requires_approval_then_lead_approves(
    api: httpx.AsyncClient, seeded_app: Any
) -> None:
    _, admin, lead, fx = seeded_app
    sc = fx["scenarios"]["refund_requires_approval"]
    r = await api.post(
        "/v1/refunds",
        json={"order_id": sc["order_id"], "amount": sc["amount"], "reason": "defective"},
        headers=admin,
    )
    assert r.status_code == 201 and r.json()["status"] == "pending_approval"
    assert (
        await api.post(f"/v1/refunds/{r.json()['id']}/approve", headers=admin)
    ).status_code == 403
    assert (await api.post(f"/v1/refunds/{r.json()['id']}/approve", headers=lead)).json()[
        "status"
    ] == "approved"


async def test_scenario_refund_policy_refusals(api: httpx.AsyncClient, seeded_app: Any) -> None:
    _, admin, _, fx = seeded_app
    old = fx["scenarios"]["refund_outside_window"]
    r = await api.post(
        "/v1/refunds",
        json={"order_id": old["order_id"], "amount": "10.00", "reason": "x"},
        headers=admin,
    )
    assert r.status_code == 422 and r.json()["type"].endswith("/refund-not-eligible")
    over = fx["scenarios"]["refund_exceeds_balance"]
    r = await api.post(
        "/v1/refunds",
        json={"order_id": over["order_id"], "amount": "60.00", "reason": "x"},
        headers=admin,
    )
    assert r.status_code == 422 and r.json()["refundable"] == over["refundable"]
    ok = await api.post(
        "/v1/refunds",
        json={"order_id": over["order_id"], "amount": over["refundable"], "reason": "x"},
        headers=admin,
    )
    assert ok.status_code == 201


async def test_scenario_existing_pending_refund(api: httpx.AsyncClient, seeded_app: Any) -> None:
    _, _, lead, fx = seeded_app
    sc = fx["scenarios"]["pending_refund"]
    got = (await api.get(f"/v1/refunds/{sc['refund_id']}", headers=lead)).json()
    assert got["status"] == "pending_approval" and got["amount"] == sc["amount"]
    assert (
        await api.post(f"/v1/refunds/{sc['refund_id']}/reject", json={"note": "no"}, headers=lead)
    ).json()["status"] == "rejected"


async def test_scenario_orders_for_ship_and_cancel(api: httpx.AsyncClient, seeded_app: Any) -> None:
    _, admin, _, fx = seeded_app
    ship = fx["scenarios"]["shippable_order"]["order_id"]
    cancel = fx["scenarios"]["cancellable_order"]["order_id"]
    assert (await api.post(f"/v1/orders/{ship}/ship", headers=admin)).json()["status"] == "shipped"
    assert (await api.post(f"/v1/orders/{cancel}/cancel", headers=admin)).json()[
        "status"
    ] == "cancelled"


async def test_scenario_reopen_windows(api: httpx.AsyncClient, seeded_app: Any) -> None:
    _, admin, _, fx = seeded_app
    ok = fx["scenarios"]["reopen_within_window"]["ticket_id"]
    late = fx["scenarios"]["reopen_after_window"]["ticket_id"]
    assert (
        await api.post(f"/v1/tickets/{ok}/reopen", json={"reason": "again"}, headers=admin)
    ).json()["status"] == "open"
    r = await api.post(f"/v1/tickets/{late}/reopen", json={"reason": "again"}, headers=admin)
    assert r.status_code == 409 and r.json()["type"].endswith("/reopen-window-expired")


async def test_scenario_merges_escalation_and_assignment(
    api: httpx.AsyncClient, seeded_app: Any
) -> None:
    _, admin, _, fx = seeded_app
    sc = fx["scenarios"]
    m = sc["merge_tickets"]
    r = await api.post(
        f"/v1/tickets/{m['source_ticket_id']}/merge",
        json={"target_ticket_id": m["target_ticket_id"]},
        headers=admin,
    )
    assert r.status_code == 200
    c = sc["merge_customers"]
    r = await api.post(
        f"/v1/customers/{c['source_customer_id']}/merge",
        json={"target_customer_id": c["target_customer_id"]},
        headers=admin,
    )
    assert r.status_code == 200
    moved = (await api.get(f"/v1/tickets/{c['source_ticket_id']}", headers=admin)).json()
    assert moved["customer_id"] == c["target_customer_id"]
    e = sc["escalate_ticket"]
    esc = await api.post(
        f"/v1/tickets/{e['ticket_id']}/escalate", json={"reason": "outage"}, headers=admin
    )
    assert esc.json()["priority"] == e["expected_priority_after"]
    new = sc["unassigned_new_ticket"]["ticket_id"]
    assert (await api.get(f"/v1/tickets/{new}", headers=admin)).json()["assignee_id"] is None
    assign = await api.post(
        f"/v1/tickets/{new}/assign", json={"assignee_id": sc["agents"]["regular"]}, headers=admin
    )
    assert assign.json()["status"] == "open"
    inactive = await api.post(
        f"/v1/tickets/{new}/assign", json={"assignee_id": sc["agents"]["inactive"]}, headers=admin
    )
    assert inactive.status_code == 422


async def test_scenario_kb_lookup(api: httpx.AsyncClient, seeded_app: Any) -> None:
    _, admin, _, fx = seeded_app
    kb = fx["scenarios"]["kb_lookup"]
    hits = (
        await api.get("/v1/kb/articles/search", params={"q": kb["query"]}, headers=admin)
    ).json()
    assert hits[0]["id"] == kb["expected_article_id"]


async def test_new_ids_continue_after_the_seed(api: httpx.AsyncClient, seeded_app: Any) -> None:
    _, admin, _, fx = seeded_app
    n = fx["counts"]["tickets"]
    r = await api.post(
        "/v1/tickets",
        json={"subject": "s", "description": "d", "customer_id": "C-0001"},
        headers=admin,
    )
    assert r.json()["id"] == f"T-{n + 1:04d}"
