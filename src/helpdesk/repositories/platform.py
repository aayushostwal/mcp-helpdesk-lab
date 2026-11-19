"""Queries for API keys, audit events and idempotency records."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from helpdesk.models import ApiKey, AuditEvent, IdempotencyRecord
from helpdesk.repositories.base import Page, paginate


class ApiKeyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, key_id: str) -> ApiKey | None:
        return await self.session.get(ApiKey, key_id)

    async def get_by_prefix(self, prefix: str) -> ApiKey | None:
        return await self.session.scalar(select(ApiKey).where(ApiKey.prefix == prefix))

    def add(self, key: ApiKey) -> None:
        self.session.add(key)

    async def list_all(self) -> Sequence[ApiKey]:
        return (await self.session.scalars(select(ApiKey).order_by(ApiKey.id))).all()


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def add(self, event: AuditEvent) -> None:
        self.session.add(event)

    async def get(self, event_id: str) -> AuditEvent | None:
        return await self.session.get(AuditEvent, event_id)

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
        stmt = select(AuditEvent)
        if actor:
            stmt = stmt.where(or_(AuditEvent.api_key_id == actor, AuditEvent.agent_id == actor))
        if entity_type:
            stmt = stmt.where(AuditEvent.entity_type == entity_type)
        if entity_id:
            stmt = stmt.where(AuditEvent.entity_id == entity_id)
        if action:
            stmt = stmt.where(AuditEvent.action == action)
        if request_id:
            stmt = stmt.where(AuditEvent.request_id == request_id)
        if since:
            stmt = stmt.where(AuditEvent.at >= since)
        if until:
            stmt = stmt.where(AuditEvent.at < until)
        return await paginate(
            self.session,
            stmt,
            sort=AuditEvent.at,
            id_col=AuditEvent.id,
            limit=limit,
            cursor=cursor,
        )


class IdempotencyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find(self, scope: str, key: str) -> IdempotencyRecord | None:
        stmt = select(IdempotencyRecord).where(
            IdempotencyRecord.scope == scope, IdempotencyRecord.key == key
        )
        return await self.session.scalar(stmt)

    def add(self, record: IdempotencyRecord) -> None:
        self.session.add(record)

    async def purge_before(self, cutoff: datetime) -> None:
        await self.session.execute(
            delete(IdempotencyRecord).where(IdempotencyRecord.created_at < cutoff)
        )
