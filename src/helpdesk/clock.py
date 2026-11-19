"""A tiny clock abstraction so that time-dependent rules are testable."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FrozenClock:
    """A clock pinned to one instant. ``advance`` lets tests move it forward."""

    def __init__(self, instant: datetime) -> None:
        self._instant = instant if instant.tzinfo else instant.replace(tzinfo=UTC)

    def now(self) -> datetime:
        return self._instant

    def set(self, instant: datetime) -> None:
        self._instant = instant if instant.tzinfo else instant.replace(tzinfo=UTC)


def clock_from_settings(frozen_time: datetime | None) -> Clock:
    return FrozenClock(frozen_time) if frozen_time else SystemClock()
