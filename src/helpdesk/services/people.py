"""Agents (staff) and teams."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from helpdesk.errors import ConflictError, NotFoundError, ValidationFailedError
from helpdesk.models import Agent, Team, Ticket
from helpdesk.repositories.base import Page, next_id
from helpdesk.repositories.people import AgentRepository, TeamRepository
from helpdesk.repositories.support import TicketFilters, TicketRepository
from helpdesk.services.base import BaseService, Context, snapshot

AGENT_ROLES = ("agent", "lead", "admin")


class AgentService(BaseService):
    def __init__(self, ctx: Context) -> None:
        super().__init__(ctx)
        self.agents = AgentRepository(ctx.session)
        self.teams = TeamRepository(ctx.session)
        self.tickets = TicketRepository(ctx.session)

    async def get(self, agent_id: str) -> Agent:
        agent = await self.agents.get(agent_id)
        if agent is None:
            raise NotFoundError("Agent", agent_id)
        return agent

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
        return await self.agents.list(
            team_id=team_id, role=role, active=active, q=q, limit=limit, cursor=cursor
        )

    async def _require_team(self, team_id: str | None) -> None:
        if team_id and await self.teams.get(team_id) is None:
            raise NotFoundError("Team", team_id)

    async def create(self, data: dict[str, Any]) -> Agent:
        if await self.agents.get_by_email(data["email"]):
            raise ConflictError(f"An agent with email '{data['email']}' already exists.")
        await self._require_team(data.get("team_id"))
        agent = Agent(
            id=await next_id(self.session, "agent", "A"),
            created_at=self.now,
            active=True,
            **data,
        )
        self.agents.add(agent)
        await self.session.flush()
        self.audit("agents.create", "agent", agent.id, None, snapshot(agent))
        await self.commit()
        return agent

    async def update(self, agent_id: str, changes: dict[str, Any]) -> Agent:
        agent = await self.get(agent_id)
        if "team_id" in changes:
            await self._require_team(changes["team_id"])
        before = snapshot(agent)
        for key, value in changes.items():
            setattr(agent, key, value)
        if "team_id" in changes:
            await self._clear_oncall_if_left(agent, before["team_id"])
        self.audit("agents.update", "agent", agent.id, before, snapshot(agent))
        await self.commit()
        return agent

    async def _clear_oncall_if_left(self, agent: Agent, old_team_id: str | None) -> None:
        if old_team_id and old_team_id != agent.team_id:
            team = await self.teams.get(old_team_id)
            if team and team.oncall_agent_id == agent.id:
                team.oncall_agent_id = None

    async def deactivate(self, agent_id: str) -> Agent:
        """Deactivate an agent: unassigns their active tickets and clears on-call duty."""
        agent = await self.get(agent_id)
        if not agent.active:
            return agent
        before = snapshot(agent)
        released: list[str] = []
        page = await self.tickets.list(
            TicketFilters(assignee_id=agent_id), now=self.now, limit=1000, cursor=None
        )
        for ticket in page.items:
            if ticket.status in ("new", "open", "pending"):
                ticket.assignee_id = None
                released.append(ticket.id)
        for team in await self.teams.list_all():
            if team.oncall_agent_id == agent_id:
                team.oncall_agent_id = None
        agent.active = False
        self.audit(
            "agents.deactivate",
            "agent",
            agent.id,
            before,
            snapshot(agent, extra={"unassigned_tickets": released}),
        )
        await self.commit()
        return agent

    async def reactivate(self, agent_id: str) -> Agent:
        agent = await self.get(agent_id)
        if agent.active:
            return agent
        before = snapshot(agent)
        agent.active = True
        self.audit("agents.reactivate", "agent", agent.id, before, snapshot(agent))
        await self.commit()
        return agent

    async def list_tickets(
        self, agent_id: str, *, status: str | None, limit: int, cursor: str | None
    ) -> Page[Ticket]:
        await self.get(agent_id)
        return await self.tickets.list(
            TicketFilters(assignee_id=agent_id, status=status),
            now=self.now,
            limit=limit,
            cursor=cursor,
        )


class TeamService(BaseService):
    def __init__(self, ctx: Context) -> None:
        super().__init__(ctx)
        self.agents = AgentRepository(ctx.session)
        self.teams = TeamRepository(ctx.session)

    async def get(self, team_id: str) -> Team:
        team = await self.teams.get(team_id)
        if team is None:
            raise NotFoundError("Team", team_id)
        return team

    async def list(self) -> Sequence[Team]:
        return await self.teams.list_all()

    async def create(self, name: str, description: str | None) -> Team:
        if await self.teams.get_by_name(name):
            raise ConflictError(f"A team named '{name}' already exists.")
        team = Team(
            id=await next_id(self.session, "team", "TM", 3),
            name=name,
            description=description,
            created_at=self.now,
        )
        self.teams.add(team)
        await self.session.flush()
        self.audit("teams.create", "team", team.id, None, snapshot(team))
        await self.commit()
        return team

    async def members(self, team_id: str) -> Sequence[Agent]:
        await self.get(team_id)
        return await self.teams.members(team_id)

    async def add_member(self, team_id: str, agent_id: str) -> Agent:
        await self.get(team_id)
        agent = await self.agents.get(agent_id)
        if agent is None:
            raise NotFoundError("Agent", agent_id)
        if agent.team_id == team_id:
            return agent
        before = snapshot(agent)
        old_team = agent.team_id
        agent.team_id = team_id
        if old_team:
            old = await self.teams.get(old_team)
            if old and old.oncall_agent_id == agent.id:
                old.oncall_agent_id = None
        self.audit("teams.add_member", "agent", agent.id, before, snapshot(agent))
        await self.commit()
        return agent

    async def remove_member(self, team_id: str, agent_id: str) -> Agent:
        team = await self.get(team_id)
        agent = await self.agents.get(agent_id)
        if agent is None:
            raise NotFoundError("Agent", agent_id)
        if agent.team_id != team_id:
            raise ConflictError(f"Agent '{agent_id}' is not a member of team '{team_id}'.")
        before = snapshot(agent)
        agent.team_id = None
        if team.oncall_agent_id == agent_id:
            team.oncall_agent_id = None
        self.audit("teams.remove_member", "agent", agent.id, before, snapshot(agent))
        await self.commit()
        return agent

    async def get_oncall(self, team_id: str) -> Agent | None:
        team = await self.get(team_id)
        return await self.agents.get(team.oncall_agent_id) if team.oncall_agent_id else None

    async def set_oncall(self, team_id: str, agent_id: str | None) -> Team:
        team = await self.get(team_id)
        if agent_id is not None:
            agent = await self.agents.get(agent_id)
            if agent is None:
                raise NotFoundError("Agent", agent_id)
            if agent.team_id != team_id:
                raise ValidationFailedError(f"Agent '{agent_id}' is not a member of '{team_id}'.")
            if not agent.active:
                raise ValidationFailedError(f"Agent '{agent_id}' is deactivated.")
        before = snapshot(team)
        team.oncall_agent_id = agent_id
        self.audit("teams.set_oncall", "team", team.id, before, snapshot(team))
        await self.commit()
        return team
