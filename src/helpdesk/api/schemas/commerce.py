from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import Field

from helpdesk.api.schemas.common import InModel, Money, OutModel
from helpdesk.domain import OrderStatus, RefundStatus


class OrderItemOut(OutModel):
    sku: str
    name: str
    quantity: int
    unit_price: Money
    line_total: Money


class OrderOut(OutModel):
    id: str = Field(examples=["O-5520"])
    customer_id: str
    status: OrderStatus
    currency: str
    total: Money
    placed_at: datetime
    shipped_at: datetime | None
    delivered_at: datetime | None
    cancelled_at: datetime | None


class OrderDetail(OrderOut):
    items: list[OrderItemOut] = Field(validation_alias="item_rows")


class OrderItemIn(InModel):
    sku: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=200)
    quantity: int = Field(ge=1, le=1000)
    unit_price: Decimal = Field(gt=0, max_digits=10, decimal_places=2)


class OrderCreate(InModel):
    customer_id: str
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    items: list[OrderItemIn] = Field(min_length=1, max_length=100)


class RefundOut(OutModel):
    id: str = Field(examples=["R-0042"])
    order_id: str
    customer_id: str
    ticket_id: str | None
    amount: Money
    reason: str
    status: RefundStatus
    requested_by_agent_id: str | None
    decided_by_agent_id: str | None
    decision_note: str | None
    created_at: datetime
    decided_at: datetime | None


class RefundCreate(InModel):
    order_id: str
    amount: Decimal = Field(gt=0, max_digits=10, decimal_places=2, description="Amount to refund.")
    reason: str = Field(min_length=1, max_length=2000)
    ticket_id: str | None = Field(default=None, description="Ticket this refund relates to.")


class RefundApprove(InModel):
    note: str | None = Field(default=None, max_length=2000)


class RefundReject(InModel):
    note: str = Field(min_length=1, max_length=2000, description="Why the refund is rejected.")
