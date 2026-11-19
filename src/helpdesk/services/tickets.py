"""Ticket business rules: lifecycle, assignment, escalation, merge and CSAT."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from helpdesk.domain import MAX_ESCALATION_LEVEL
from helpdesk.errors import (
    ConflictError,
    NotFoundError,
    PolicyViolationError,
    ValidationFailedError,
)
from helpdesk.models import AuditEvent, Comment, Tag, Ticket
from helpdesk.repositories.base import Page, next_id
from helpdesk.repositories.commerce import OrderRepository
from helpdesk.repositories.config import SlaPolicyRepository
from helpdesk.repositories.people import AgentRepository, CustomerRepository, TeamRepository
from helpdesk.repositories.platform import AuditRepository
from helpdesk.repositories.support import (
    CommentRepository,
    TagRepository,
    TicketFilters,
    TicketRepository,
)
from helpdesk.services.base import BaseService, Context, snapshot
from helpdesk.services.runtime_settings import load_effective
from helpdesk.services.sla import compute_due_dates
from helpdesk.services.workflow import CLOSEABLE, check_reopen, check_transition, next_priority


class TicketService(BaseService):
    def __init__(self, ctx: Context) -> None:
        super().__init__(ctx)
        self.tickets = TicketRepository(ctx.session)
        self.comments = CommentRepository(ctx.session)
        self.tags = TagRepository(ctx.session)
        self.customers = CustomerRepository(ctx.session)
        self.agents = AgentRepository(ctx.session)
        self.teams = TeamRepository(ctx.session)
        self.orders = OrderRepository(ctx.session)
        self.policies = SlaPolicyRepository(ctx.session)
        self.audit_repo = AuditRepository(ctx.session)

    # ------------------------------------------------------------------ reads

    async def get(self, ticket_id: str) -> Ticket:
        ticket = await self.tickets.get(ticket_id)
        if ticket is None:
            raise NotFoundError("Ticket", ticket_id)
        return ticket

    async def list(self, filters: TicketFilters, *, limit: int, cursor: str | None) -> Page[Ticket]:
        return await self.tickets.list(filters, now=self.now, limit=limit, cursor=cursor)

    async def history(self, ticket_id: str, *, limit: int, cursor: str | None) -> Page[AuditEvent]:
        await self.get(ticket_id)
        return await self.audit_repo.list(
            actor=None,
            entity_type="ticket",
            entity_id=ticket_id,
            action=None,
            request_id=None,
            since=None,
            until=None,
            limit=limit,
            cursor=cursor,
        )

    # ----------------------------------------------------------- staging helpers
    # "stage_*" methods change state in the open transaction without committing, so that
    # other services (macros) can compose them into one atomic operation.

    async def _require_agent(self, agent_id: str) -> None:
        agent = await self.agents.get(agent_id)
        if agent is None:
            raise NotFoundError("Agent", agent_id)
        if not agent.active:
            raise ValidationFailedError(
                f"Agent '{agent_id}' is deactivated and cannot be assigned."
            )

    async def _require_team(self, team_id: str) -> None:
        if await self.teams.get(team_id) is None:
            raise NotFoundError("Team", team_id)

    def require_mutable(self, ticket: Ticket) -> None:
        if ticket.status == "closed":
            raise ConflictError(
                f"Ticket '{ticket.id}' is closed and cannot be modified.", status="closed"
            )

    async def _refresh_sla(self, ticket: Ticket) -> None:
        policy = await self.policies.get_by_priority(ticket.priority)
        ticket.first_response_due_at, ticket.resolution_due_at = compute_due_dates(
            ticket.created_at, policy
        )

    async def stage_priority(self, ticket: Ticket, priority: str) -> None:
        if priority != ticket.priority:
            ticket.priority = priority
            await self._refresh_sla(ticket)

    def stage_status(self, ticket: Ticket, target: str) -> None:
        check_transition(ticket.status, target)
        ticket.status = target
        if target == "solved":
            ticket.solved_at = self.now
        elif target == "closed":
            ticket.closed_at = self.now

    async def stage_tags(self, ticket: Ticket, names: Sequence[str]) -> Sequence[str]:
        added: list[str] = []
        for name in names:
            if name in ticket.tags:
                continue
            tag = await self.tags.get_by_name(name)
            if tag is None:
                tag = Tag(name=name, created_at=self.now)
                self.tags.add(tag)
                await self.session.flush()
            ticket.tag_rows.append(tag)
            added.append(name)
        return added

    async def stage_comment(
        self,
        ticket: Ticket,
        body: str,
        *,
        internal: bool,
        author_type: str,
        author_id: str | None,
    ) -> Comment:
        comment = Comment(
            id=await next_id(self.session, "comment", "CM", 5),
            ticket_id=ticket.id,
            author_type=author_type,
            author_id=author_id,
            body=body,
            is_internal=internal,
            created_at=self.now,
        )
        self.comments.add(comment)
        if not internal and author_type != "customer" and ticket.first_response_at is None:
            ticket.first_response_at = self.now
        ticket.updated_at = self.now
        await self.session.flush()
        return comment

    def _system_actor(self) -> tuple[str, str | None]:
        principal = self.ctx.principal
        if principal and principal.agent_id:
            return "agent", principal.agent_id
        return "system", None

    async def _note(self, ticket: Ticket, body: str) -> None:
        author_type, author_id = self._system_actor()
        await self.stage_comment(
            ticket, body, internal=True, author_type=author_type, author_id=author_id
        )

    # ----------------------------------------------------------------- writes

    async def create(self, data: dict[str, Any]) -> Ticket:
        tags: list[str] = data.pop("tags", [])
        customer = await self.customers.get(data["customer_id"])
        if customer is None:
            raise NotFoundError("Customer", data["customer_id"])
        if customer.status == "merged":
            raise ConflictError(
                f"Customer '{customer.id}' was merged into '{customer.merged_into_id}'; "
                "create the ticket on the surviving customer."
            )
        if data.get("assignee_id"):
            await self._require_agent(data["assignee_id"])
        if data.get("team_id"):
            await self._require_team(data["team_id"])
        if data.get("order_id"):
            order = await self.orders.get(data["order_id"])
            if order is None:
                raise NotFoundError("Order", data["order_id"])
            if order.customer_id != customer.id:
                raise ValidationFailedError(
                    f"Order '{order.id}' does not belong to customer '{customer.id}'."
                )
        now = self.now
        ticket = Ticket(
            id=await next_id(self.session, "ticket", "T"),
            status="new",
            created_at=now,
            updated_at=now,
            tag_rows=[],
            **data,
        )
        if ticket.assignee_id:
            ticket.status = "open"
        await self._refresh_sla(ticket)
        self.tickets.add(ticket)
        await self.session.flush()
        await self.stage_tags(ticket, tags)
        self.audit(
            "tickets.create", "ticket", ticket.id, None, snapshot(ticket, {"tags": ticket.tags})
        )
        await self.commit()
        return ticket

    async def update(self, ticket_id: str, changes: dict[str, Any]) -> Ticket:
        ticket = await self.get(ticket_id)
        self.require_mutable(ticket)
        before = snapshot(ticket)
        if changes.get("order_id"):
            order = await self.orders.get(changes["order_id"])
            if order is None:
                raise NotFoundError("Order", changes["order_id"])
            if order.customer_id != ticket.customer_id:
                raise ValidationFailedError(
                    f"Order '{order.id}' does not belong to customer '{ticket.customer_id}'."
                )
        priority = changes.pop("priority", None)
        for key, value in changes.items():
            setattr(ticket, key, value)
        if priority:
            await self.stage_priority(ticket, priority)
        ticket.updated_at = self.now
        self.audit("tickets.update", "ticket", ticket.id, before, snapshot(ticket))
        await self.commit()
        return ticket

    async def assign(self, ticket_id: str, changes: dict[str, Any]) -> Ticket:
        """Set or clear the assignee and/or team. Only keys present in ``changes`` are touched."""
        if not changes:
            raise ValidationFailedError("Provide assignee_id and/or team_id.")
        ticket = await self.get(ticket_id)
        self.require_mutable(ticket)
        before = snapshot(ticket)
        if changes.get("assignee_id"):
            await self._require_agent(changes["assignee_id"])
        if changes.get("team_id"):
            await self._require_team(changes["team_id"])
        for key, value in changes.items():
            setattr(ticket, key, value)
        if changes.get("assignee_id") and ticket.status == "new":
            self.stage_status(ticket, "open")
        ticket.updated_at = self.now
        self.audit("tickets.assign", "ticket", ticket.id, before, snapshot(ticket))
        await self.commit()
        return ticket

    async def change_status(self, ticket_id: str, target: str, note: str | None) -> Ticket:
        ticket = await self.get(ticket_id)
        before = snapshot(ticket)
        self.stage_status(ticket, target)
        if note:
            await self._note(ticket, note)
        ticket.updated_at = self.now
        self.audit("tickets.change_status", "ticket", ticket.id, before, snapshot(ticket))
        await self.commit()
        return ticket

    async def close(self, ticket_id: str, note: str | None) -> Ticket:
        """Solve and close in one step. Allowed from open, pending or solved."""
        ticket = await self.get(ticket_id)
        if ticket.status not in CLOSEABLE:
            raise ConflictError(
                f"A ticket in status '{ticket.status}' cannot be closed; "
                f"it must be one of {sorted(CLOSEABLE)}.",
                current=ticket.status,
            )
        before = snapshot(ticket)
        if ticket.status != "solved":
            self.stage_status(ticket, "solved")
        self.stage_status(ticket, "closed")
        if note:
            await self._note(ticket, note)
        ticket.updated_at = self.now
        self.audit("tickets.close", "ticket", ticket.id, before, snapshot(ticket))
        await self.commit()
        return ticket

    async def reopen(self, ticket_id: str, reason: str) -> Ticket:
        ticket = await self.get(ticket_id)
        effective = await load_effective(self.session, self.ctx.settings)
        check_reopen(ticket.status, ticket.solved_at, self.now, effective.reopen_window_days)
        before = snapshot(ticket)
        ticket.status = "open"
        ticket.solved_at = None
        ticket.reopen_count += 1
        await self._note(ticket, f"Reopened: {reason}")
        ticket.updated_at = self.now
        self.audit("tickets.reopen", "ticket", ticket.id, before, snapshot(ticket))
        await self.commit()
        return ticket

    async def escalate(self, ticket_id: str, reason: str, team_id: str | None) -> Ticket:
        ticket = await self.get(ticket_id)
        if ticket.status in ("solved", "closed"):
            raise ConflictError(f"A {ticket.status} ticket cannot be escalated.")
        if ticket.escalation_level >= MAX_ESCALATION_LEVEL:
            raise ConflictError(
                f"Ticket '{ticket_id}' is already at the maximum escalation level "
                f"({MAX_ESCALATION_LEVEL}).",
                escalation_level=ticket.escalation_level,
            )
        if team_id:
            await self._require_team(team_id)
        before = snapshot(ticket)
        ticket.escalation_level += 1
        await self.stage_priority(ticket, next_priority(ticket.priority))
        if team_id:
            ticket.team_id = team_id
        await self._note(ticket, f"Escalated to level {ticket.escalation_level}: {reason}")
        ticket.updated_at = self.now
        self.audit("tickets.escalate", "ticket", ticket.id, before, snapshot(ticket))
        await self.commit()
        return ticket

    async def merge(self, source_id: str, target_id: str) -> Ticket:
        """Merge ``source`` into ``target``: comments and tags move, source is closed."""
        if source_id == target_id:
            raise ValidationFailedError("A ticket cannot be merged into itself.")
        source = await self.get(source_id)
        target = await self.get(target_id)
        if source.merged_into_id == target_id:
            return target  # idempotent replay
        if source.merged_into_id:
            raise ConflictError(
                f"Ticket '{source_id}' was already merged into '{source.merged_into_id}'."
            )
        if target.merged_into_id or target.status == "closed":
            raise ConflictError(f"Target ticket '{target_id}' is closed or merged away.")
        if source.customer_id != target.customer_id:
            raise PolicyViolationError("Only tickets from the same customer can be merged.")
        before = snapshot(source)
        moved = 0
        for comment in await self.comments.for_ticket(source_id):
            comment.ticket_id = target_id
            moved += 1
        await self.stage_tags(target, source.tags)
        if source.first_response_at and (
            target.first_response_at is None or source.first_response_at < target.first_response_at
        ):
            target.first_response_at = source.first_response_at
        source.merged_into_id = target_id
        if source.status != "solved":
            source.solved_at = source.solved_at or self.now
        source.status = "closed"
        source.closed_at = self.now
        source.updated_at = self.now
        await self._note(target, f"Merged {source_id} into this ticket ({moved} comments moved).")
        await self._note(source, f"Merged into {target_id}.")
        target.updated_at = self.now
        self.audit(
            "tickets.merge",
            "ticket",
            source_id,
            before,
            snapshot(source, {"moved_comments": moved, "target": target_id}),
        )
        await self.commit()
        return target

    async def submit_csat(self, ticket_id: str, score: int, comment: str | None) -> Ticket:
        ticket = await self.get(ticket_id)
        if ticket.status not in ("solved", "closed"):
            raise ConflictError("CSAT can only be submitted for solved or closed tickets.")
        if ticket.csat_score is not None:
            raise ConflictError(f"Ticket '{ticket_id}' already has a CSAT rating.")
        before = snapshot(ticket)
        ticket.csat_score = score
        ticket.csat_comment = comment
        ticket.csat_submitted_at = self.now
        self.audit("tickets.submit_csat", "ticket", ticket.id, before, snapshot(ticket))
        await self.commit()
        return ticket
