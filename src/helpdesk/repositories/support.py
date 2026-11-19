"""Queries for tickets, comments and tags."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ColumnElement, and_, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from helpdesk.models import Comment, Tag, Ticket, ticket_tags
from helpdesk.models.base import UTCDateTime
from helpdesk.repositories.base import Page, like_pattern, paginate


def sla_breached_clause(now: datetime) -> ColumnElement[bool]:
    """SQL predicate: the ticket has missed (or is currently missing) an SLA target."""
    now_lit = literal(now, UTCDateTime())
    return or_(
        and_(
            Ticket.first_response_due_at.is_not(None),
            Ticket.first_response_due_at < func.coalesce(Ticket.first_response_at, now_lit),
        ),
        and_(
            Ticket.resolution_due_at.is_not(None),
            Ticket.resolution_due_at < func.coalesce(Ticket.solved_at, now_lit),
        ),
    )


@dataclass
class TicketFilters:
    status: str | None = None
    priority: str | None = None
    assignee_id: str | None = None
    unassigned: bool = False
    team_id: str | None = None
    customer_id: str | None = None
    tag: str | None = None
    sla_breached: bool | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None
    q: str | None = None


class TicketRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, ticket_id: str) -> Ticket | None:
        return await self.session.get(Ticket, ticket_id)

    def add(self, ticket: Ticket) -> None:
        self.session.add(ticket)

    async def list(
        self, filters: TicketFilters, *, now: datetime, limit: int, cursor: str | None
    ) -> Page[Ticket]:
        stmt = select(Ticket)
        if filters.status:
            stmt = stmt.where(Ticket.status == filters.status)
        if filters.priority:
            stmt = stmt.where(Ticket.priority == filters.priority)
        if filters.assignee_id:
            stmt = stmt.where(Ticket.assignee_id == filters.assignee_id)
        if filters.unassigned:
            stmt = stmt.where(Ticket.assignee_id.is_(None))
        if filters.team_id:
            stmt = stmt.where(Ticket.team_id == filters.team_id)
        if filters.customer_id:
            stmt = stmt.where(Ticket.customer_id == filters.customer_id)
        if filters.tag:
            stmt = stmt.where(
                Ticket.id.in_(
                    select(ticket_tags.c.ticket_id)
                    .join(Tag, Tag.id == ticket_tags.c.tag_id)
                    .where(Tag.name == filters.tag)
                )
            )
        if filters.sla_breached is True:
            stmt = stmt.where(sla_breached_clause(now))
        elif filters.sla_breached is False:
            stmt = stmt.where(~sla_breached_clause(now))
        if filters.created_after:
            stmt = stmt.where(Ticket.created_at >= filters.created_after)
        if filters.created_before:
            stmt = stmt.where(Ticket.created_at < filters.created_before)
        if filters.q:
            pattern = like_pattern(filters.q)
            stmt = stmt.where(
                or_(
                    Ticket.subject.ilike(pattern, escape="\\"),
                    Ticket.description.ilike(pattern, escape="\\"),
                    Ticket.id.ilike(pattern, escape="\\"),
                )
            )
        return await paginate(
            self.session, stmt, sort=Ticket.created_at, id_col=Ticket.id, limit=limit, cursor=cursor
        )

    async def for_customer(self, customer_id: str) -> Sequence[Ticket]:
        stmt = select(Ticket).where(Ticket.customer_id == customer_id)
        return (await self.session.scalars(stmt)).all()


class CommentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, comment_id: str) -> Comment | None:
        return await self.session.get(Comment, comment_id)

    def add(self, comment: Comment) -> None:
        self.session.add(comment)

    async def list(
        self,
        ticket_id: str,
        *,
        include_internal: bool,
        limit: int,
        cursor: str | None,
    ) -> Page[Comment]:
        stmt = select(Comment).where(Comment.ticket_id == ticket_id)
        if not include_internal:
            stmt = stmt.where(Comment.is_internal.is_(False))
        return await paginate(
            self.session,
            stmt,
            sort=Comment.created_at,
            id_col=Comment.id,
            limit=limit,
            cursor=cursor,
            descending=False,
        )

    async def for_ticket(self, ticket_id: str) -> Sequence[Comment]:
        stmt = select(Comment).where(Comment.ticket_id == ticket_id)
        return (await self.session.scalars(stmt)).all()


class TagRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_name(self, name: str) -> Tag | None:
        return await self.session.scalar(select(Tag).where(Tag.name == name))

    def add(self, tag: Tag) -> None:
        self.session.add(tag)

    async def delete(self, tag: Tag) -> None:
        await self.session.execute(ticket_tags.delete().where(ticket_tags.c.tag_id == tag.id))
        await self.session.delete(tag)

    async def list_with_counts(self) -> Sequence[tuple[Tag, int]]:
        stmt = (
            select(Tag, func.count(ticket_tags.c.ticket_id))
            .outerjoin(ticket_tags, ticket_tags.c.tag_id == Tag.id)
            .group_by(Tag.id)
            .order_by(Tag.name)
        )
        return [(tag, count) for tag, count in (await self.session.execute(stmt)).all()]
