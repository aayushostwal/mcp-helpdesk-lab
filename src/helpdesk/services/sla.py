"""SLA policies and SLA evaluation. Breach status is always computed from timestamps."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from helpdesk.errors import ConflictError, NotFoundError, ValidationFailedError
from helpdesk.models import SlaPolicy, Ticket
from helpdesk.repositories.base import Page, next_id
from helpdesk.repositories.config import SlaPolicyRepository
from helpdesk.repositories.support import TicketFilters, TicketRepository
from helpdesk.services.base import BaseService, Context, snapshot

AT_RISK_FRACTION = 0.2


@dataclass(frozen=True)
class TargetStatus:
    due_at: datetime | None
    achieved_at: datetime | None
    state: str  # untracked | met | breached | pending | at_risk
    minutes_remaining: int | None


@dataclass(frozen=True)
class SlaEvaluation:
    ticket_id: str
    priority: str
    policy_id: str | None
    evaluated_at: datetime
    first_response: TargetStatus
    resolution: TargetStatus
    breached: bool


def compute_due_dates(
    created_at: datetime, policy: SlaPolicy | None
) -> tuple[datetime | None, datetime | None]:
    if policy is None:
        return None, None
    return (
        created_at + timedelta(minutes=policy.first_response_minutes),
        created_at + timedelta(minutes=policy.resolution_minutes),
    )


def evaluate_target(
    due_at: datetime | None,
    achieved_at: datetime | None,
    now: datetime,
    total_minutes: int | None,
) -> TargetStatus:
    """Classify one SLA target.

    ``met``: achieved on time. ``breached``: achieved late, or still open past the due time.
    ``at_risk``: still open with less than 20% of the target window left. ``pending``: otherwise.
    """
    if due_at is None:
        return TargetStatus(None, achieved_at, "untracked", None)
    if achieved_at is not None:
        state = "met" if achieved_at <= due_at else "breached"
        return TargetStatus(due_at, achieved_at, state, None)
    remaining = int((due_at - now).total_seconds() // 60)
    if now > due_at:
        return TargetStatus(due_at, None, "breached", remaining)
    if total_minutes and remaining < total_minutes * AT_RISK_FRACTION:
        return TargetStatus(due_at, None, "at_risk", remaining)
    return TargetStatus(due_at, None, "pending", remaining)


def evaluate_ticket(ticket: Ticket, policy: SlaPolicy | None, now: datetime) -> SlaEvaluation:
    first = evaluate_target(
        ticket.first_response_due_at,
        ticket.first_response_at,
        now,
        policy.first_response_minutes if policy else None,
    )
    resolution = evaluate_target(
        ticket.resolution_due_at,
        ticket.solved_at,
        now,
        policy.resolution_minutes if policy else None,
    )
    return SlaEvaluation(
        ticket_id=ticket.id,
        priority=ticket.priority,
        policy_id=policy.id if policy else None,
        evaluated_at=now,
        first_response=first,
        resolution=resolution,
        breached="breached" in (first.state, resolution.state),
    )


class SlaService(BaseService):
    def __init__(self, ctx: Context) -> None:
        super().__init__(ctx)
        self.policies = SlaPolicyRepository(ctx.session)
        self.tickets = TicketRepository(ctx.session)

    async def list(self) -> Sequence[SlaPolicy]:
        return await self.policies.list_all()

    async def get(self, policy_id: str) -> SlaPolicy:
        policy = await self.policies.get(policy_id)
        if policy is None:
            raise NotFoundError("SLA policy", policy_id)
        return policy

    async def create(self, data: dict[str, Any]) -> SlaPolicy:
        self._check_targets(data["first_response_minutes"], data["resolution_minutes"])
        if await self.policies.get_by_priority(data["priority"], active_only=False):
            raise ConflictError(f"An SLA policy for priority '{data['priority']}' already exists.")
        policy = SlaPolicy(
            id=await next_id(self.session, "sla", "SLA", 3),
            created_at=self.now,
            active=True,
            **data,
        )
        self.policies.add(policy)
        await self.session.flush()
        self.audit("sla_policies.create", "sla_policy", policy.id, None, snapshot(policy))
        await self.commit()
        return policy

    async def update(self, policy_id: str, changes: dict[str, Any]) -> SlaPolicy:
        policy = await self.get(policy_id)
        before = snapshot(policy)
        for key, value in changes.items():
            setattr(policy, key, value)
        self._check_targets(policy.first_response_minutes, policy.resolution_minutes)
        self.audit("sla_policies.update", "sla_policy", policy.id, before, snapshot(policy))
        await self.commit()
        return policy

    @staticmethod
    def _check_targets(first_response: int, resolution: int) -> None:
        if resolution < first_response:
            raise ValidationFailedError(
                "resolution_minutes must be greater than or equal to first_response_minutes."
            )

    async def evaluate(self, ticket_id: str) -> SlaEvaluation:
        ticket = await self.tickets.get(ticket_id)
        if ticket is None:
            raise NotFoundError("Ticket", ticket_id)
        policy = await self.policies.get_by_priority(ticket.priority, active_only=False)
        return evaluate_ticket(ticket, policy, self.now)

    async def list_breaches(
        self, *, priority: str | None, team_id: str | None, limit: int, cursor: str | None
    ) -> Page[Ticket]:
        return await self.tickets.list(
            TicketFilters(sla_breached=True, priority=priority, team_id=team_id),
            now=self.now,
            limit=limit,
            cursor=cursor,
        )
