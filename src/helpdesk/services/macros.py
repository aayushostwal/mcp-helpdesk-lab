"""Macros: canned replies with optional ticket actions."""

from __future__ import annotations

from typing import Any

from helpdesk.errors import ConflictError, NotFoundError
from helpdesk.models import Comment, Macro, Ticket
from helpdesk.repositories.base import Page, next_id
from helpdesk.repositories.config import MacroRepository
from helpdesk.repositories.people import AgentRepository, CustomerRepository
from helpdesk.services.base import BaseService, Context, snapshot
from helpdesk.services.tickets import TicketService


def render_macro(body: str, variables: dict[str, str]) -> str:
    for key, value in variables.items():
        body = body.replace("{{" + key + "}}", value)
    return body


class MacroService(BaseService):
    def __init__(self, ctx: Context) -> None:
        super().__init__(ctx)
        self.macros = MacroRepository(ctx.session)
        self.tickets = TicketService(ctx)
        self.customers = CustomerRepository(ctx.session)
        self.agents = AgentRepository(ctx.session)

    async def get(self, macro_id: str) -> Macro:
        macro = await self.macros.get(macro_id)
        if macro is None:
            raise NotFoundError("Macro", macro_id)
        return macro

    async def list(self, *, q: str | None, limit: int, cursor: str | None) -> Page[Macro]:
        return await self.macros.list(q=q, limit=limit, cursor=cursor)

    async def create(self, data: dict[str, Any]) -> Macro:
        if await self.macros.get_by_name(data["name"]):
            raise ConflictError(f"A macro named '{data['name']}' already exists.")
        macro = Macro(id=await next_id(self.session, "macro", "M", 3), created_at=self.now, **data)
        self.macros.add(macro)
        await self.session.flush()
        self.audit("macros.create", "macro", macro.id, None, snapshot(macro))
        await self.commit()
        return macro

    async def update(self, macro_id: str, changes: dict[str, Any]) -> Macro:
        macro = await self.get(macro_id)
        renamed = "name" in changes and changes["name"] != macro.name
        if renamed and await self.macros.get_by_name(changes["name"]):
            raise ConflictError(f"A macro named '{changes['name']}' already exists.")
        before = snapshot(macro)
        for key, value in changes.items():
            setattr(macro, key, value)
        self.audit("macros.update", "macro", macro.id, before, snapshot(macro))
        await self.commit()
        return macro

    async def delete(self, macro_id: str) -> None:
        macro = await self.get(macro_id)
        before = snapshot(macro)
        await self.macros.delete(macro)
        self.audit("macros.delete", "macro", macro_id, before, None)
        await self.commit()

    async def apply(self, macro_id: str, ticket_id: str) -> tuple[Ticket, Comment]:
        """Post the macro text on the ticket and run its actions, all in one transaction."""
        macro = await self.get(macro_id)
        ticket = await self.tickets.get(ticket_id)
        self.tickets.require_mutable(ticket)
        before = snapshot(ticket, {"tags": ticket.tags})
        actions: dict[str, Any] = macro.actions or {}
        customer = await self.customers.get(ticket.customer_id)
        principal = self.ctx.principal
        agent = (
            await self.agents.get(principal.agent_id) if principal and principal.agent_id else None
        )
        text = render_macro(
            macro.body,
            {
                "ticket_id": ticket.id,
                "customer_name": customer.name if customer else "there",
                "agent_name": agent.name if agent else "Support",
            },
        )
        internal = not actions.get("public", True)
        comment = await self.tickets.stage_comment(
            ticket,
            text,
            internal=internal,
            author_type="agent" if agent else "system",
            author_id=agent.id if agent else None,
        )
        if actions.get("priority"):
            await self.tickets.stage_priority(ticket, actions["priority"])
        if actions.get("add_tags"):
            await self.tickets.stage_tags(ticket, list(actions["add_tags"]))
        if actions.get("status"):
            self.tickets.stage_status(ticket, actions["status"])
        ticket.updated_at = self.now
        self.audit(
            "macros.apply",
            "ticket",
            ticket.id,
            before,
            snapshot(ticket, {"tags": ticket.tags, "macro_id": macro.id, "comment_id": comment.id}),
        )
        await self.commit()
        return ticket, comment
