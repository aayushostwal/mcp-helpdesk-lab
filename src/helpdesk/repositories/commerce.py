"""Queries for orders and refunds."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from helpdesk.models import Order, Refund
from helpdesk.repositories.base import Page, paginate

BALANCE_HOLDING_STATUSES = ("pending_approval", "approved")


class OrderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, order_id: str) -> Order | None:
        return await self.session.get(Order, order_id)

    def add(self, order: Order) -> None:
        self.session.add(order)

    async def list(
        self,
        *,
        customer_id: str | None,
        status: str | None,
        placed_after: datetime | None,
        placed_before: datetime | None,
        limit: int,
        cursor: str | None,
    ) -> Page[Order]:
        stmt = select(Order)
        if customer_id:
            stmt = stmt.where(Order.customer_id == customer_id)
        if status:
            stmt = stmt.where(Order.status == status)
        if placed_after:
            stmt = stmt.where(Order.placed_at >= placed_after)
        if placed_before:
            stmt = stmt.where(Order.placed_at < placed_before)
        return await paginate(
            self.session, stmt, sort=Order.placed_at, id_col=Order.id, limit=limit, cursor=cursor
        )

    async def for_customer(self, customer_id: str) -> Sequence[Order]:
        stmt = select(Order).where(Order.customer_id == customer_id)
        return (await self.session.scalars(stmt)).all()


class RefundRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, refund_id: str) -> Refund | None:
        return await self.session.get(Refund, refund_id)

    def add(self, refund: Refund) -> None:
        self.session.add(refund)

    async def list(
        self,
        *,
        status: str | None,
        order_id: str | None,
        customer_id: str | None,
        limit: int,
        cursor: str | None,
    ) -> Page[Refund]:
        stmt = select(Refund)
        if status:
            stmt = stmt.where(Refund.status == status)
        if order_id:
            stmt = stmt.where(Refund.order_id == order_id)
        if customer_id:
            stmt = stmt.where(Refund.customer_id == customer_id)
        return await paginate(
            self.session,
            stmt,
            sort=Refund.created_at,
            id_col=Refund.id,
            limit=limit,
            cursor=cursor,
        )

    async def for_order(self, order_id: str) -> Sequence[Refund]:
        stmt = select(Refund).where(Refund.order_id == order_id).order_by(Refund.created_at)
        return (await self.session.scalars(stmt)).all()

    async def committed_cents(self, order_id: str) -> int:
        """Cents already refunded or reserved by a pending refund for this order."""
        stmt = select(func.coalesce(func.sum(Refund.amount_cents), 0)).where(
            Refund.order_id == order_id, Refund.status.in_(BALANCE_HOLDING_STATUSES)
        )
        return int(await self.session.scalar(stmt) or 0)
