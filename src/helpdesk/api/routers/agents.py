from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from helpdesk.api.deps import Pagination, scoped, service
from helpdesk.api.routers._common import ERROR_RESPONSES, to_page
from helpdesk.api.schemas.common import Paginated
from helpdesk.api.schemas.people import (
    AgentCreate,
    AgentOut,
    AgentUpdate,
    OnCallSet,
    TeamCreate,
    TeamMemberAdd,
    TeamOut,
)
from helpdesk.api.schemas.support import TicketOut
from helpdesk.domain import AgentRole
from helpdesk.services.people import AgentService, TeamService

agents_router = APIRouter(prefix="/agents", tags=["agents"], responses=ERROR_RESPONSES)
teams_router = APIRouter(prefix="/teams", tags=["teams"], responses=ERROR_RESPONSES)
Agents = Annotated[AgentService, Depends(service(AgentService))]
Teams = Annotated[TeamService, Depends(service(TeamService))]


@agents_router.get(
    "",
    operation_id="agents_list",
    summary="List or search agents (staff users)",
    description=(
        "Lists support agents ordered by id. Filter by `team_id`, `role` (agent, lead, admin), "
        "`active`, or search name/email with `q`. Use it to find an agent id before assigning a "
        "ticket or to find a `lead` who can approve refunds."
    ),
    response_model=Paginated[AgentOut],
    dependencies=scoped("agents:read"),
)
async def agents_list(
    svc: Agents,
    page: Pagination,
    team_id: str | None = None,
    role: AgentRole | None = None,
    active: bool | None = None,
    q: Annotated[str | None, Query(description="Search name or email.")] = None,
) -> Paginated[AgentOut]:
    result = await svc.list(
        team_id=team_id, role=role, active=active, q=q, limit=page.limit, cursor=page.cursor
    )
    return to_page(result, AgentOut)


@agents_router.post(
    "",
    operation_id="agents_create",
    summary="Create an agent",
    description=(
        "Adds a support agent. Required: `name`, `email` (unique). Optional: `role` "
        "(agent by default; lead can approve refunds) and `team_id`. Supports Idempotency-Key."
    ),
    response_model=AgentOut,
    status_code=201,
    dependencies=scoped("agents:write"),
)
async def agents_create(body: AgentCreate, svc: Agents) -> AgentOut:
    return AgentOut.model_validate(await svc.create(body.model_dump()))


@agents_router.get(
    "/{agent_id}",
    operation_id="agents_get",
    summary="Get an agent by id",
    description="Returns one agent (for example `A-0007`) with role (agent, lead, admin), team and active flag. Use it to check whether someone is a lead.",
    response_model=AgentOut,
    dependencies=scoped("agents:read"),
)
async def agents_get(agent_id: str, svc: Agents) -> AgentOut:
    return AgentOut.model_validate(await svc.get(agent_id))


@agents_router.patch(
    "/{agent_id}",
    operation_id="agents_update",
    summary="Update an agent",
    description=(
        "Partially updates an agent's `name`, `role` or `team_id`. Moving an agent to another "
        "team removes any on-call duty they held on the old team."
    ),
    response_model=AgentOut,
    dependencies=scoped("agents:write"),
)
async def agents_update(agent_id: str, body: AgentUpdate, svc: Agents) -> AgentOut:
    return AgentOut.model_validate(await svc.update(agent_id, body.model_dump(exclude_unset=True)))


@agents_router.post(
    "/{agent_id}/deactivate",
    operation_id="agents_deactivate",
    summary="Deactivate an agent",
    description=(
        "Marks the agent inactive (for example when they leave). Their new/open/pending tickets "
        "become unassigned and any on-call duty is cleared. Safe to repeat. Deactivated agents "
        "cannot be assigned tickets and their API keys lose agent powers."
    ),
    response_model=AgentOut,
    dependencies=scoped("agents:write"),
)
async def agents_deactivate(agent_id: str, svc: Agents) -> AgentOut:
    return AgentOut.model_validate(await svc.deactivate(agent_id))


@agents_router.post(
    "/{agent_id}/reactivate",
    operation_id="agents_reactivate",
    summary="Reactivate an agent",
    description="Marks a previously deactivated agent active again so they can be assigned tickets. Tickets they lost on deactivation stay unassigned. Safe to repeat.",
    response_model=AgentOut,
    dependencies=scoped("agents:write"),
)
async def agents_reactivate(agent_id: str, svc: Agents) -> AgentOut:
    return AgentOut.model_validate(await svc.reactivate(agent_id))


@agents_router.get(
    "/{agent_id}/tickets",
    operation_id="agents_list_tickets",
    summary="List tickets assigned to an agent",
    description=(
        "Lists tickets currently assigned to the agent, newest first. Optional `status` filter. "
        "Use it to see an agent's workload or hand over work."
    ),
    response_model=Paginated[TicketOut],
    dependencies=scoped("agents:read", "tickets:read"),
)
async def agents_list_tickets(
    agent_id: str, svc: Agents, page: Pagination, status: str | None = None
) -> Paginated[TicketOut]:
    result = await svc.list_tickets(agent_id, status=status, limit=page.limit, cursor=page.cursor)
    return to_page(result, TicketOut)


@teams_router.get(
    "",
    operation_id="teams_list",
    summary="List teams",
    description="Lists all teams (there are only a handful) with their on-call agent id. Use it to find a team id for routing tickets or listing members.",
    response_model=list[TeamOut],
    dependencies=scoped("agents:read"),
)
async def teams_list(svc: Teams) -> list[TeamOut]:
    return [TeamOut.model_validate(t) for t in await svc.list()]


@teams_router.post(
    "",
    operation_id="teams_create",
    summary="Create a team",
    description="Creates a team. `name` must be unique. Add members afterwards with `teams_add_member`.",
    response_model=TeamOut,
    status_code=201,
    dependencies=scoped("agents:write"),
)
async def teams_create(body: TeamCreate, svc: Teams) -> TeamOut:
    return TeamOut.model_validate(await svc.create(body.name, body.description))


@teams_router.get(
    "/{team_id}",
    operation_id="teams_get",
    summary="Get a team by id",
    description="Returns one team (for example `TM-002`) including its current on-call agent id.",
    response_model=TeamOut,
    dependencies=scoped("agents:read"),
)
async def teams_get(team_id: str, svc: Teams) -> TeamOut:
    return TeamOut.model_validate(await svc.get(team_id))


@teams_router.get(
    "/{team_id}/members",
    operation_id="teams_list_members",
    summary="List team members",
    description="Lists the agents that belong to the team, ordered by id. Use it to see who can be assigned the team's tickets or put on call.",
    response_model=list[AgentOut],
    dependencies=scoped("agents:read"),
)
async def teams_list_members(team_id: str, svc: Teams) -> list[AgentOut]:
    return [AgentOut.model_validate(a) for a in await svc.members(team_id)]


@teams_router.post(
    "/{team_id}/members",
    operation_id="teams_add_member",
    summary="Add an agent to a team",
    description=(
        "Makes `agent_id` a member of the team. An agent belongs to at most one team, so this "
        "moves them if they were elsewhere. Safe to repeat."
    ),
    response_model=AgentOut,
    dependencies=scoped("agents:write"),
)
async def teams_add_member(team_id: str, body: TeamMemberAdd, svc: Teams) -> AgentOut:
    return AgentOut.model_validate(await svc.add_member(team_id, body.agent_id))


@teams_router.delete(
    "/{team_id}/members/{agent_id}",
    operation_id="teams_remove_member",
    summary="Remove an agent from a team",
    description=(
        "Removes the agent from the team (409 if they are not a member). Clears on-call duty if "
        "they held it. Returns the updated agent."
    ),
    response_model=AgentOut,
    dependencies=scoped("agents:write"),
)
async def teams_remove_member(team_id: str, agent_id: str, svc: Teams) -> AgentOut:
    return AgentOut.model_validate(await svc.remove_member(team_id, agent_id))


@teams_router.get(
    "/{team_id}/oncall",
    operation_id="teams_get_oncall",
    summary="Get the on-call agent for a team",
    description="Returns the agent currently on call for the team, or null if nobody is. Use it to decide who to escalate to right now.",
    response_model=AgentOut | None,
    dependencies=scoped("agents:read"),
)
async def teams_get_oncall(team_id: str, svc: Teams) -> AgentOut | None:
    agent = await svc.get_oncall(team_id)
    return AgentOut.model_validate(agent) if agent else None


@teams_router.put(
    "/{team_id}/oncall",
    operation_id="teams_set_oncall",
    summary="Set or clear the on-call agent for a team",
    description=(
        "Sets `agent_id` as on call for the team (must be an active member), or clears it with "
        "null. Returns the team."
    ),
    response_model=TeamOut,
    dependencies=scoped("agents:write"),
)
async def teams_set_oncall(team_id: str, body: OnCallSet, svc: Teams) -> TeamOut:
    return TeamOut.model_validate(await svc.set_oncall(team_id, body.agent_id))
