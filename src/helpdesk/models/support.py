"""Tickets, comments and tags."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, ForeignKey, Index, Integer, String, Table, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from helpdesk.models.base import Base, UTCDateTime

ticket_tags = Table(
    "ticket_tags",
    Base.metadata,
    Column("ticket_id", String(32), ForeignKey("tickets.id"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("tags.id"), primary_key=True),
)


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = (Index("ix_tickets_status_priority", "status", "priority"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    subject: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="new", index=True)
    priority: Mapped[str] = mapped_column(String(20), default="normal", index=True)
    channel: Mapped[str] = mapped_column(String(20), default="email")
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), index=True)
    assignee_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"), index=True)
    team_id: Mapped[str | None] = mapped_column(ForeignKey("teams.id"), index=True)
    order_id: Mapped[str | None] = mapped_column(ForeignKey("orders.id"), index=True)
    escalation_level: Mapped[int] = mapped_column(Integer, default=0)
    reopen_count: Mapped[int] = mapped_column(Integer, default=0)
    merged_into_id: Mapped[str | None] = mapped_column(ForeignKey("tickets.id"))
    csat_score: Mapped[int | None] = mapped_column(Integer)
    csat_comment: Mapped[str | None] = mapped_column(Text)
    csat_submitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)
    first_response_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    solved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    first_response_due_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    resolution_due_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    tag_rows: Mapped[list[Tag]] = relationship(secondary=ticket_tags, lazy="selectin")

    @property
    def tags(self) -> list[str]:
        return sorted(tag.name for tag in self.tag_rows)


class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    ticket_id: Mapped[str] = mapped_column(ForeignKey("tickets.id"), index=True)
    author_type: Mapped[str] = mapped_column(String(20))
    author_id: Mapped[str | None] = mapped_column(String(32))
    body: Mapped[str] = mapped_column(Text)
    is_internal: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
