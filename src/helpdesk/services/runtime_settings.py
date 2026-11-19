"""Business settings that admins can change at runtime, layered over env defaults."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from helpdesk.config import Settings
from helpdesk.repositories.config import SettingRepository


@dataclass(frozen=True)
class EffectiveSettings:
    refund_window_days: int
    refund_approval_threshold: Decimal
    reopen_window_days: int


async def load_effective(session: AsyncSession, settings: Settings) -> EffectiveSettings:
    stored = await SettingRepository(session).all()
    return EffectiveSettings(
        refund_window_days=int(stored.get("refund_window_days", settings.refund_window_days)),
        refund_approval_threshold=Decimal(
            str(stored.get("refund_approval_threshold", settings.refund_approval_threshold))
        ),
        reopen_window_days=int(stored.get("reopen_window_days", settings.reopen_window_days)),
    )
