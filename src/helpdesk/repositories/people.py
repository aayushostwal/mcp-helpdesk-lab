"""Queries for customers, agents and teams."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from helpdesk.models import Agent, Customer, Order, Team, Ticket
from helpdesk.repositories.base import Page, like_pattern, paginate


class CustomerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, customer_id: str) -> Customer | None:
        return await self.session.get(Customer, customer_id)

    async def get_by_email(self, email: str) -> Customer | None:
        return await self.session.scalar(select(Customer).where(Customer.email == email))

    def add(self, customer: Customer) -> None:
        self.session.add(customer)

    async def list(
        self,
        *,
        q: str | None,
        tier: str | None,
        status: str | None,
        limit: int,
        cursor: str | None,
    ) -> Page[Customer]:
        stmt = select(Customer)
        if q:
            pattern = like_pattern(q)
            stmt = stmt.where(
                or_(
                    Customer.name.ilike(pattern, escape="\\"),
                    Customer.email.ilike(pattern, escape="\\"),
                    Customer.company.ilike(pattern, escape="\\"),
                    Customer.id.ilike(pattern, escape="\\"),
                )
            )
        if tier:
            stmt = stmt.where(Customer.tier == tier)
        if status:
            stmt = stmt.where(Customer.status == status)
        return await paginate(
            self.session,
            stmt,
            sort=Customer.created_at,
            id_col=Customer.id,
            limit=limit,
            cursor=cursor,
        )

    async def counts(self, customer_id: str) -> dict[str, int]:
        tickets = await self.session.scalar(
            select(func.count()).select_from(Ticket).where(Ticket.customer_id == customer_id)
        )
        open_tickets = await self.session.scalar(
            select(func.count())
            .select_from(Ticket)
            .where(Ticket.customer_id == customer_id, Ticket.status.in_(("new", "open", "pending")))
        )
        orders = await self.session.scalar(
            select(func.count()).select_from(Order).where(Order.customer_id == customer_id)
        )
        return {
            "tickets": tickets or 0,
            "open_tickets": open_tickets or 0,
            "orders": orders or 0,
        }


class AgentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, agent_id: str) -> Agent | None:
        return await self.session.get(Agent, agent_id)

    async def get_by_email(self, email: str) -> Agent | None:
        return await self.session.scalar(select(Agent).where(Agent.email == email))

    def add(self, agent: Agent) -> None:
        self.session.add(agent)

    async def list(
        self,
        *,
        team_id: str | None,
        role: str | None,
        active: bool | None,
        q: str | None,
        limit: int,
        cursor: str | None,
    ) -> Page[Agent]:
        stmt = select(Agent)
        if team_id:
            stmt = stmt.where(Agent.team_id == team_id)
        if role:
            stmt = stmt.where(Agent.role == role)
        if active is not None:
            stmt = stmt.where(Agent.active == active)
        if q:
            pattern = like_pattern(q)
            stmt = stmt.where(
                or_(Agent.name.ilike(pattern, escape="\\"), Agent.email.ilike(pattern, escape="\\"))
            )
        return await paginate(
            self.session,
            stmt,
            sort=Agent.id,
            id_col=Agent.id,
            limit=limit,
            cursor=cursor,
            descending=False,
        )


class TeamRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, team_id: str) -> Team | None:
        return await self.session.get(Team, team_id)

    async def get_by_name(self, name: str) -> Team | None:
        return await self.session.scalar(select(Team).where(Team.name == name))

    def add(self, team: Team) -> None:
        self.session.add(team)

    async def list_all(self) -> Sequence[Team]:
        return (await self.session.scalars(select(Team).order_by(Team.id))).all()

    async def members(self, team_id: str) -> Sequence[Agent]:
        stmt = select(Agent).where(Agent.team_id == team_id).order_by(Agent.id)
        return (await self.session.scalars(stmt)).all()
