"""Orders and their fulfilment status."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from helpdesk.errors import ConflictError, NotFoundError
from helpdesk.models import Order, OrderItem, Refund
from helpdesk.models.base import cents
from helpdesk.repositories.base import Page, next_id
from helpdesk.repositories.commerce import OrderRepository, RefundRepository
from helpdesk.repositories.people import CustomerRepository
from helpdesk.services.base import BaseService, Context, snapshot


class OrderService(BaseService):
    def __init__(self, ctx: Context) -> None:
        super().__init__(ctx)
        self.orders = OrderRepository(ctx.session)
        self.refunds = RefundRepository(ctx.session)
        self.customers = CustomerRepository(ctx.session)

    async def get(self, order_id: str) -> Order:
        order = await self.orders.get(order_id)
        if order is None:
            raise NotFoundError("Order", order_id)
        return order

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
        return await self.orders.list(
            customer_id=customer_id,
            status=status,
            placed_after=placed_after,
            placed_before=placed_before,
            limit=limit,
            cursor=cursor,
        )

    async def list_refunds(self, order_id: str) -> Sequence[Refund]:
        await self.get(order_id)
        return await self.refunds.for_order(order_id)

    async def create(
        self, customer_id: str, currency: str, items: Sequence[dict[str, Any]]
    ) -> Order:
        customer = await self.customers.get(customer_id)
        if customer is None:
            raise NotFoundError("Customer", customer_id)
        if customer.status == "merged":
            raise ConflictError(f"Customer '{customer_id}' was merged into another customer.")
        rows = [
            OrderItem(
                sku=item["sku"],
                name=item["name"],
                quantity=item["quantity"],
                unit_price_cents=cents(item["unit_price"]),
            )
            for item in items
        ]
        order = Order(
            id=await next_id(self.session, "order", "O"),
            customer_id=customer_id,
            status="placed",
            currency=currency,
            total_cents=sum(r.unit_price_cents * r.quantity for r in rows),
            placed_at=self.now,
        )
        order.item_rows = rows
        self.orders.add(order)
        await self.session.flush()
        self.audit("orders.create", "order", order.id, None, snapshot(order))
        await self.commit()
        return order

    async def _transition(
        self, order_id: str, allowed_from: str, target: str, action: str
    ) -> Order:
        order = await self.get(order_id)
        if order.status != allowed_from:
            raise ConflictError(
                f"Order '{order_id}' is '{order.status}'; only '{allowed_from}' orders can be "
                f"{action}.",
                current=order.status,
            )
        before = snapshot(order)
        order.status = target
        setattr(
            order,
            {"shipped": "shipped_at", "delivered": "delivered_at"}.get(target, "cancelled_at"),
            self.now,
        )
        self.audit(f"orders.{action}", "order", order.id, before, snapshot(order))
        await self.commit()
        return order

    async def ship(self, order_id: str) -> Order:
        return await self._transition(order_id, "placed", "shipped", "ship")

    async def deliver(self, order_id: str) -> Order:
        return await self._transition(order_id, "shipped", "delivered", "deliver")

    async def cancel(self, order_id: str) -> Order:
        return await self._transition(order_id, "placed", "cancelled", "cancel")
