"""Service base class: request context, audit recording and transaction control."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession

from helpdesk.clock import Clock
from helpdesk.config import Settings
from helpdesk.integrations.webhook_sender import SimulatedWebhookSender, WebhookSender
from helpdesk.models import AuditEvent
from helpdesk.repositories.platform import AuditRepository

_last_event_stamp = 0


def new_event_id() -> str:
    """Time-ordered audit id: sorts by creation order, strictly increasing within a process."""
    global _last_event_stamp
    _last_event_stamp = max(_last_event_stamp + 1, time.time_ns())
    return f"AE-{_last_event_stamp:016x}{secrets.token_hex(2)}"


SENSITIVE_COLUMNS = frozenset({"key_hash", "salt", "secret"})


@dataclass(frozen=True)
class Principal:
    """The authenticated caller, resolved from an API key."""

    api_key_id: str
    name: str
    scopes: frozenset[str]
    agent_id: str | None = None
    agent_role: str | None = None


@dataclass
class Context:
    session: AsyncSession
    clock: Clock
    settings: Settings
    principal: Principal | None = None
    request_id: str | None = None
    webhook_sender: WebhookSender = field(default_factory=SimulatedWebhookSender)


def jsonable(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def snapshot(obj: Any, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """JSON-safe copy of an ORM row's column values, minus secrets."""
    mapper = sa_inspect(obj).mapper
    data = {
        attr.key: jsonable(getattr(obj, attr.key))
        for attr in mapper.column_attrs
        if attr.key not in SENSITIVE_COLUMNS
    }
    if extra:
        data.update(extra)
    return data


def diff(
    before: dict[str, Any] | None, after: dict[str, Any] | None
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Reduce two snapshots to only the fields that changed."""
    if before is None or after is None:
        return before, after
    keys = [k for k in after if before.get(k) != after.get(k)]
    return {k: before.get(k) for k in keys}, {k: after[k] for k in keys}


class BaseService:
    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx
        self.session = ctx.session
        self._audit_repo = AuditRepository(ctx.session)

    @property
    def now(self) -> datetime:
        return self.ctx.clock.now()

    def audit(
        self,
        action: str,
        entity_type: str,
        entity_id: str,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
    ) -> None:
        """Record an audit event in the current transaction (committed with the write)."""
        before_d, after_d = diff(before, after)
        principal = self.ctx.principal
        self._audit_repo.add(
            AuditEvent(
                id=new_event_id(),
                at=self.now,
                api_key_id=principal.api_key_id if principal else None,
                agent_id=principal.agent_id if principal else None,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                before=before_d,
                after=after_d,
                request_id=self.ctx.request_id,
            )
        )

    async def commit(self) -> None:
        await self.session.commit()
