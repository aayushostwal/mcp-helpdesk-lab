"""Read access to the audit log."""

from __future__ import annotations

from datetime import datetime

from helpdesk.errors import NotFoundError
from helpdesk.models import AuditEvent
from helpdesk.repositories.base import Page
from helpdesk.repositories.platform import AuditRepository
from helpdesk.services.base import BaseService, Context


class AuditService(BaseService):
    def __init__(self, ctx: Context) -> None:
        super().__init__(ctx)
        self.events = AuditRepository(ctx.session)

    async def get(self, event_id: str) -> AuditEvent:
        event = await self.events.get(event_id)
        if event is None:
            raise NotFoundError("Audit event", event_id)
        return event

    async def list(
        self,
        *,
        actor: str | None,
        entity_type: str | None,
        entity_id: str | None,
        action: str | None,
        request_id: str | None,
        since: datetime | None,
        until: datetime | None,
        limit: int,
        cursor: str | None,
    ) -> Page[AuditEvent]:
        return await self.events.list(
            actor=actor,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            request_id=request_id,
            since=since,
            until=until,
            limit=limit,
            cursor=cursor,
        )
