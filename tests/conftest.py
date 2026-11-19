"""Shared fixtures.

Integration tests run against a temp SQLite file by default. Set ``TEST_DATABASE_URL`` (for
example ``postgresql+asyncpg://user:pass@localhost/db``) to run the same suite on PostgreSQL.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable, Coroutine
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from helpdesk.api.app import create_app
from helpdesk.clock import FrozenClock
from helpdesk.config import Settings
from helpdesk.integrations.webhook_sender import DeliveryResult
from helpdesk.models import (
    Agent,
    Base,
    Customer,
    Order,
    OrderItem,
    SlaPolicy,
    Team,
)
from helpdesk.repositories.base import set_sequence
from helpdesk.services.admin import ApiKeyService
from helpdesk.services.base import Context

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


class FakeSender:
    """Records webhook deliveries instead of calling out."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.result = DeliveryResult(True, 200, None, 12)

    async def send(
        self, url: str, headers: dict[str, str], body: bytes, timeout_seconds: float
    ) -> DeliveryResult:
        self.calls.append({"url": url, "headers": headers, "body": body})
        return self.result


def make_settings(url: str) -> Settings:
    return Settings(
        database_url=url,
        environment="test",
        log_level="WARNING",
        log_json=False,
        webhook_delivery="simulated",
    )


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    return os.environ.get("TEST_DATABASE_URL") or f"sqlite+aiosqlite:///{tmp_path}/test.db"


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(NOW)


@pytest.fixture
def sender() -> FakeSender:
    return FakeSender()


@pytest.fixture
async def app(database_url: str, clock: FrozenClock, sender: FakeSender) -> AsyncIterator[FastAPI]:
    application = create_app(make_settings(database_url), clock=clock, webhook_sender=sender)
    engine: AsyncEngine = application.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield application
    await engine.dispose()


@pytest.fixture
def session_factory(app: FastAPI) -> async_sessionmaker[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = app.state.session_factory
    return factory


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


KeyFactory = Callable[..., Coroutine[Any, Any, str]]


@pytest.fixture
def make_key(
    app: FastAPI, session_factory: async_sessionmaker[AsyncSession], clock: FrozenClock
) -> KeyFactory:
    async def factory(
        scopes: list[str] | None = None,
        agent_id: str | None = None,
        expires_in_days: int | None = None,
        name: str = "test",
    ) -> str:
        async with session_factory() as session:
            ctx = Context(session=session, clock=clock, settings=app.state.settings)
            _, token = await ApiKeyService(ctx).create(
                name, scopes or ["admin"], agent_id, expires_in_days
            )
            return token

    return factory


def money_items(*prices: str) -> list[OrderItem]:
    return [
        OrderItem(
            sku=f"SKU-{i}", name=f"Item {i}", quantity=1, unit_price_cents=int(Decimal(p) * 100)
        )
        for i, p in enumerate(prices, start=1)
    ]


@pytest.fixture
async def world(session_factory: async_sessionmaker[AsyncSession]) -> dict[str, str]:
    """A small fixed world. Returns the ids tests refer to."""
    async with session_factory() as s:
        s.add_all(
            [
                Team(id="TM-001", name="Billing", created_at=NOW),
                Team(id="TM-002", name="Support", created_at=NOW),
            ]
        )
        await s.flush()
        s.add_all(
            [
                Agent(
                    id="A-0001",
                    name="Ada Admin",
                    email="ada@x.example",
                    role="admin",
                    team_id="TM-001",
                    active=True,
                    created_at=NOW,
                ),
                Agent(
                    id="A-0002",
                    name="Lee Lead",
                    email="lee@x.example",
                    role="lead",
                    team_id="TM-001",
                    active=True,
                    created_at=NOW,
                ),
                Agent(
                    id="A-0003",
                    name="Rae Agent",
                    email="rae@x.example",
                    role="agent",
                    team_id="TM-001",
                    active=True,
                    created_at=NOW,
                ),
                Agent(
                    id="A-0004",
                    name="Sam Agent",
                    email="sam@x.example",
                    role="agent",
                    team_id="TM-002",
                    active=True,
                    created_at=NOW,
                ),
            ]
        )
        for i, (prio, fr, res) in enumerate(
            [("urgent", 30, 240), ("high", 60, 480), ("normal", 240, 1440), ("low", 480, 4320)],
            start=1,
        ):
            s.add(
                SlaPolicy(
                    id=f"SLA-{i:03d}",
                    name=prio,
                    priority=prio,
                    first_response_minutes=fr,
                    resolution_minutes=res,
                    active=True,
                    created_at=NOW,
                )
            )
        s.add_all(
            [
                Customer(
                    id="C-0001",
                    name="Cora Customer",
                    email="cora@example.com",
                    tier="standard",
                    status="active",
                    created_at=NOW,
                    updated_at=NOW,
                ),
                Customer(
                    id="C-0002",
                    name="Dan Dup",
                    email="dan@example.com",
                    tier="plus",
                    status="active",
                    created_at=NOW,
                    updated_at=NOW,
                ),
            ]
        )
        await s.flush()

        def order(
            oid: str, cust: str, status: str, total: list[str], delivered_days: float | None = None
        ) -> Order:
            o = Order(
                id=oid,
                customer_id=cust,
                status=status,
                currency="USD",
                total_cents=sum(int(Decimal(p) * 100) for p in total),
                placed_at=NOW - timedelta(days=60),
            )
            o.item_rows = money_items(*total)
            if status in ("shipped", "delivered"):
                o.shipped_at = NOW - timedelta(days=(delivered_days or 3) + 1)
            if delivered_days is not None:
                o.delivered_at = NOW - timedelta(days=delivered_days)
            return o

        s.add_all(
            [
                order("O-0001", "C-0001", "delivered", ["60.00", "40.00"], 5),
                order("O-0002", "C-0001", "delivered", ["89.00"], 40),
                order("O-0003", "C-0001", "delivered", ["400.00", "400.00"], 2),
                order("O-0004", "C-0001", "placed", ["25.00"]),
                order("O-0005", "C-0001", "shipped", ["120.00"]),
                order("O-0006", "C-0002", "delivered", ["10.00"], 1),
            ]
        )
        await s.flush()
        for name, value in {"agent": 4, "team": 2, "customer": 2, "order": 6, "sla": 4}.items():
            await set_sequence(s, name, value)
        await s.commit()
    return {
        "customer": "C-0001",
        "dup_customer": "C-0002",
        "lead": "A-0002",
        "agent": "A-0003",
        "admin": "A-0001",
        "other_agent": "A-0004",
    }


@pytest.fixture
async def admin(make_key: KeyFactory, world: dict[str, str]) -> dict[str, str]:
    """Headers for an all-powerful key linked to the admin agent."""
    token = await make_key(["admin"], agent_id=world["admin"])
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def lead(make_key: KeyFactory, world: dict[str, str]) -> dict[str, str]:
    token = await make_key(["admin"], agent_id=world["lead"], name="lead")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def agent(make_key: KeyFactory, world: dict[str, str]) -> dict[str, str]:
    token = await make_key(["admin"], agent_id=world["agent"], name="agent")
    return {"Authorization": f"Bearer {token}"}


async def create_ticket(
    client: httpx.AsyncClient, headers: dict[str, str], **overrides: Any
) -> dict[str, Any]:
    body = {"subject": "Printer on fire", "description": "It is smoking.", "customer_id": "C-0001"}
    body.update(overrides)
    r = await client.post("/v1/tickets", json=body, headers=headers)
    assert r.status_code == 201, r.text
    data: dict[str, Any] = r.json()
    return data
