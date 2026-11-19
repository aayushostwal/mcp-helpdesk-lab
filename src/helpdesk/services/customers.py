"""Customer business rules."""

from __future__ import annotations

from typing import Any

from helpdesk.errors import ConflictError, NotFoundError, ValidationFailedError
from helpdesk.models import Customer, Order, Refund, Ticket
from helpdesk.repositories.base import Page, next_id
from helpdesk.repositories.commerce import OrderRepository, RefundRepository
from helpdesk.repositories.people import CustomerRepository
from helpdesk.repositories.support import TicketFilters, TicketRepository
from helpdesk.services.base import BaseService, Context, snapshot


class CustomerService(BaseService):
    def __init__(self, ctx: Context) -> None:
        super().__init__(ctx)
        self.customers = CustomerRepository(ctx.session)
        self.tickets = TicketRepository(ctx.session)
        self.orders = OrderRepository(ctx.session)
        self.refunds = RefundRepository(ctx.session)

    async def get(self, customer_id: str) -> Customer:
        customer = await self.customers.get(customer_id)
        if customer is None:
            raise NotFoundError("Customer", customer_id)
        return customer

    async def list(
        self, *, q: str | None, tier: str | None, status: str | None, limit: int, cursor: str | None
    ) -> Page[Customer]:
        return await self.customers.list(q=q, tier=tier, status=status, limit=limit, cursor=cursor)

    async def create(self, data: dict[str, Any]) -> Customer:
        if await self.customers.get_by_email(data["email"]):
            raise ConflictError(
                f"A customer with email '{data['email']}' already exists.", field="email"
            )
        now = self.now
        customer = Customer(
            id=await next_id(self.session, "customer", "C"),
            created_at=now,
            updated_at=now,
            status="active",
            **data,
        )
        self.customers.add(customer)
        await self.session.flush()
        self.audit("customers.create", "customer", customer.id, None, snapshot(customer))
        await self.commit()
        return customer

    async def update(self, customer_id: str, changes: dict[str, Any]) -> Customer:
        customer = await self.get(customer_id)
        if customer.status == "merged":
            raise ConflictError(
                f"Customer '{customer_id}' was merged into '{customer.merged_into_id}' "
                "and can no longer be edited."
            )
        email = changes.get("email")
        if email and email != customer.email:
            clash = await self.customers.get_by_email(email)
            if clash:
                raise ConflictError(f"A customer with email '{email}' already exists.")
        before = snapshot(customer)
        for key, value in changes.items():
            setattr(customer, key, value)
        customer.updated_at = self.now
        self.audit("customers.update", "customer", customer.id, before, snapshot(customer))
        await self.commit()
        return customer

    async def merge(self, source_id: str, target_id: str) -> Customer:
        """Move tickets, orders and refunds from source to target. Safe to repeat."""
        if source_id == target_id:
            raise ValidationFailedError("A customer cannot be merged into itself.")
        source = await self.get(source_id)
        target = await self.get(target_id)
        if source.status == "merged":
            if source.merged_into_id == target_id:
                return target  # idempotent replay
            raise ConflictError(
                f"Customer '{source_id}' was already merged into '{source.merged_into_id}'."
            )
        if target.status == "merged":
            raise ConflictError(f"Target customer '{target_id}' was itself merged away.")
        before = snapshot(source)
        moved = {"tickets": 0, "orders": 0, "refunds": 0}
        for ticket in await self.tickets.for_customer(source_id):
            ticket.customer_id = target_id
            moved["tickets"] += 1
        for order in await self.orders.for_customer(source_id):
            order.customer_id = target_id
            moved["orders"] += 1
        refunds = await self.refunds.list(
            status=None, order_id=None, customer_id=source_id, limit=1000, cursor=None
        )
        for refund in refunds.items:
            refund.customer_id = target_id
            moved["refunds"] += 1
        source.status = "merged"
        source.merged_into_id = target_id
        source.updated_at = self.now
        self.audit(
            "customers.merge",
            "customer",
            source_id,
            before,
            snapshot(source, extra={"moved": moved, "target": target_id}),
        )
        await self.commit()
        return target

    async def list_tickets(
        self, customer_id: str, *, status: str | None, limit: int, cursor: str | None
    ) -> Page[Ticket]:
        await self.get(customer_id)
        return await self.tickets.list(
            TicketFilters(customer_id=customer_id, status=status),
            now=self.now,
            limit=limit,
            cursor=cursor,
        )

    async def list_orders(
        self, customer_id: str, *, status: str | None, limit: int, cursor: str | None
    ) -> Page[Order]:
        await self.get(customer_id)
        return await self.orders.list(
            customer_id=customer_id,
            status=status,
            placed_after=None,
            placed_before=None,
            limit=limit,
            cursor=cursor,
        )

    async def list_refunds(
        self, customer_id: str, *, status: str | None, limit: int, cursor: str | None
    ) -> Page[Refund]:
        await self.get(customer_id)
        return await self.refunds.list(
            status=status, order_id=None, customer_id=customer_id, limit=limit, cursor=cursor
        )

    async def summary(self, customer_id: str) -> tuple[Customer, dict[str, int]]:
        customer = await self.get(customer_id)
        return customer, await self.customers.counts(customer_id)
