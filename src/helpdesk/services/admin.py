"""Admin operations: runtime settings and API keys."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from decimal import Decimal
from typing import Any

from helpdesk.errors import NotFoundError, ValidationFailedError
from helpdesk.models import ApiKey, Setting
from helpdesk.repositories.base import next_id
from helpdesk.repositories.config import SettingRepository
from helpdesk.repositories.people import AgentRepository
from helpdesk.repositories.platform import ApiKeyRepository
from helpdesk.security import SCOPES, generate_key
from helpdesk.services.base import BaseService, Context, snapshot
from helpdesk.services.runtime_settings import load_effective


class SettingsService(BaseService):
    def __init__(self, ctx: Context) -> None:
        super().__init__(ctx)
        self.rows = SettingRepository(ctx.session)

    async def get(self) -> dict[str, Any]:
        effective = await load_effective(self.session, self.ctx.settings)
        stored = await self.rows.all()
        return {
            "refund_window_days": effective.refund_window_days,
            "refund_approval_threshold": effective.refund_approval_threshold,
            "reopen_window_days": effective.reopen_window_days,
            "overridden": sorted(stored),
        }

    async def update(self, changes: dict[str, Any]) -> dict[str, Any]:
        before = await self.get()
        for key, value in changes.items():
            stored: Any = str(value) if isinstance(value, Decimal) else value
            row = await self.rows.get_row(key)
            if row is None:
                self.rows.add(Setting(key=key, value=stored, updated_at=self.now))
            else:
                row.value = stored
                row.updated_at = self.now
        await self.session.flush()
        after = await self.get()
        self.audit(
            "settings.update",
            "settings",
            "global",
            {k: str(v) for k, v in before.items()},
            {k: str(v) for k, v in after.items()},
        )
        await self.commit()
        return after


class ApiKeyService(BaseService):
    def __init__(self, ctx: Context) -> None:
        super().__init__(ctx)
        self.keys = ApiKeyRepository(ctx.session)
        self.agents = AgentRepository(ctx.session)

    async def list(self) -> Sequence[ApiKey]:
        return await self.keys.list_all()

    async def get(self, key_id: str) -> ApiKey:
        key = await self.keys.get(key_id)
        if key is None:
            raise NotFoundError("API key", key_id)
        return key

    async def create(
        self,
        name: str,
        scopes: Sequence[str],
        agent_id: str | None,
        expires_in_days: int | None,
    ) -> tuple[ApiKey, str]:
        """Create a key. The plaintext token is returned once and never stored."""
        unknown = sorted(set(scopes) - set(SCOPES))
        if unknown:
            raise ValidationFailedError(
                f"Unknown scopes: {unknown}. Valid scopes: {list(SCOPES)}.", unknown=unknown
            )
        if agent_id and await self.agents.get(agent_id) is None:
            raise NotFoundError("Agent", agent_id)
        material = generate_key()
        key = ApiKey(
            id=await next_id(self.session, "api_key", "K", 3),
            name=name,
            prefix=material.prefix,
            salt=material.salt,
            key_hash=material.key_hash,
            scopes=sorted(set(scopes)),
            agent_id=agent_id,
            created_at=self.now,
            expires_at=self.now + timedelta(days=expires_in_days) if expires_in_days else None,
        )
        self.keys.add(key)
        await self.session.flush()
        self.audit("api_keys.create", "api_key", key.id, None, snapshot(key))
        await self.commit()
        return key, material.token

    async def revoke(self, key_id: str) -> ApiKey:
        key = await self.get(key_id)
        if key.revoked_at is not None:
            return key
        before = snapshot(key)
        key.revoked_at = self.now
        self.audit("api_keys.revoke", "api_key", key.id, before, snapshot(key))
        await self.commit()
        return key
