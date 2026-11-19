"""Ticket comments: public replies and internal notes."""

from __future__ import annotations

from helpdesk.errors import ConflictError, NotFoundError
from helpdesk.models import Comment
from helpdesk.repositories.base import Page
from helpdesk.repositories.support import CommentRepository
from helpdesk.services.base import BaseService, Context, snapshot
from helpdesk.services.tickets import TicketService


class CommentService(BaseService):
    def __init__(self, ctx: Context) -> None:
        super().__init__(ctx)
        self.comments = CommentRepository(ctx.session)
        self.tickets = TicketService(ctx)

    async def list(
        self, ticket_id: str, *, include_internal: bool, limit: int, cursor: str | None
    ) -> Page[Comment]:
        await self.tickets.get(ticket_id)
        return await self.comments.list(
            ticket_id, include_internal=include_internal, limit=limit, cursor=cursor
        )

    async def get(self, comment_id: str) -> Comment:
        comment = await self.comments.get(comment_id)
        if comment is None:
            raise NotFoundError("Comment", comment_id)
        return comment

    async def add(self, ticket_id: str, body: str, *, internal: bool) -> Comment:
        ticket = await self.tickets.get(ticket_id)
        if ticket.status == "closed":
            raise ConflictError(f"Ticket '{ticket_id}' is closed; open a new ticket instead.")
        principal = self.ctx.principal
        agent_id = principal.agent_id if principal else None
        comment = await self.tickets.stage_comment(
            ticket,
            body,
            internal=internal,
            author_type="agent" if agent_id else "system",
            author_id=agent_id,
        )
        self.audit(
            "comments.add_note" if internal else "comments.add_reply",
            "comment",
            comment.id,
            None,
            snapshot(comment),
        )
        await self.commit()
        return comment
