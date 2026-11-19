"""Tags and ticket tagging."""

from __future__ import annotations

from collections.abc import Sequence

from helpdesk.errors import ConflictError, NotFoundError
from helpdesk.models import Tag, Ticket
from helpdesk.repositories.support import TagRepository
from helpdesk.services.base import BaseService, Context, snapshot
from helpdesk.services.tickets import TicketService


class TagService(BaseService):
    def __init__(self, ctx: Context) -> None:
        super().__init__(ctx)
        self.tags = TagRepository(ctx.session)
        self.tickets = TicketService(ctx)

    async def list(self) -> Sequence[tuple[Tag, int]]:
        return await self.tags.list_with_counts()

    async def create(self, name: str) -> Tag:
        if await self.tags.get_by_name(name):
            raise ConflictError(f"Tag '{name}' already exists.")
        tag = Tag(name=name, created_at=self.now)
        self.tags.add(tag)
        await self.session.flush()
        self.audit("tags.create", "tag", tag.name, None, snapshot(tag))
        await self.commit()
        return tag

    async def delete(self, name: str) -> None:
        tag = await self.tags.get_by_name(name)
        if tag is None:
            raise NotFoundError("Tag", name)
        before = snapshot(tag)
        await self.tags.delete(tag)
        self.audit("tags.delete", "tag", name, before, None)
        await self.commit()

    async def ticket_tags(self, ticket_id: str) -> Sequence[str]:
        return (await self.tickets.get(ticket_id)).tags

    async def add_to_ticket(self, ticket_id: str, name: str) -> Ticket:
        ticket = await self.tickets.get(ticket_id)
        before = {"tags": ticket.tags}
        if await self.tickets.stage_tags(ticket, [name]):
            self.audit("tickets.add_tag", "ticket", ticket.id, before, {"tags": ticket.tags})
            await self.commit()
        return ticket

    async def remove_from_ticket(self, ticket_id: str, name: str) -> Ticket:
        ticket = await self.tickets.get(ticket_id)
        if name not in ticket.tags:
            raise NotFoundError("Tag on ticket", f"{ticket_id}/{name}")
        before = {"tags": ticket.tags}
        ticket.tag_rows = [tag for tag in ticket.tag_rows if tag.name != name]
        self.audit("tickets.remove_tag", "ticket", ticket.id, before, {"tags": ticket.tags})
        await self.commit()
        return ticket
