"""Orders, order items and refunds. Money is stored as integer cents."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from helpdesk.models.base import Base, UTCDateTime, to_decimal


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="placed", index=True)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    total_cents: Mapped[int] = mapped_column(Integer)
    placed_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    shipped_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    item_rows: Mapped[list[OrderItem]] = relationship(
        lazy="selectin", order_by="OrderItem.id", cascade="all, delete-orphan"
    )

    @property
    def total(self) -> Decimal:
        return to_decimal(self.total_cents)


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    sku: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price_cents: Mapped[int] = mapped_column(Integer)

    @property
    def unit_price(self) -> Decimal:
        return to_decimal(self.unit_price_cents)

    @property
    def line_total(self) -> Decimal:
        return to_decimal(self.unit_price_cents * self.quantity)


class Refund(Base):
    __tablename__ = "refunds"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), index=True)
    ticket_id: Mapped[str | None] = mapped_column(ForeignKey("tickets.id"))
    amount_cents: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), index=True)
    requested_by_agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"))
    decided_by_agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"))
    decision_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    @property
    def amount(self) -> Decimal:
        return to_decimal(self.amount_cents)
