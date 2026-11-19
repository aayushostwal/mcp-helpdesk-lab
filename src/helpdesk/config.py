"""Application settings, loaded from environment variables (see docs/operations.md)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def normalize_database_url(url: str) -> str:
    """Map plain driver-less URLs onto the async drivers this service uses."""
    if url.startswith("sqlite://") and not url.startswith("sqlite+"):
        return url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


class Settings(BaseSettings):
    """All configuration. Every field maps to an env var named ``HELPDESK_<FIELD>``.

    ``DATABASE_URL`` is also accepted without the prefix.
    """

    model_config = SettingsConfigDict(
        env_prefix="HELPDESK_", env_file=".env", extra="ignore", populate_by_name=True
    )

    database_url: str = Field(
        default="sqlite+aiosqlite:///./helpdesk.db",
        validation_alias=AliasChoices("DATABASE_URL", "HELPDESK_DATABASE_URL"),
    )
    environment: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"
    log_json: bool = True
    host: str = "127.0.0.1"
    port: int = 8000
    problem_base_uri: str = "https://helpdesk.example/problems/"
    frozen_time: datetime | None = Field(
        default=None,
        description="Pin the service clock (ISO 8601). Used by the benchmark and demos so that "
        "seeded data stays inside the refund and reopen windows.",
    )
    webhook_delivery: Literal["live", "simulated"] = "live"
    webhook_timeout_seconds: float = 5.0
    webhook_allow_private_hosts: bool = False
    idempotency_ttl_hours: int = 24

    # Defaults for runtime-adjustable business settings (see admin settings endpoints).
    refund_window_days: int = 30
    refund_approval_threshold: Decimal = Decimal("500.00")
    reopen_window_days: int = 7

    @field_validator("database_url")
    @classmethod
    def _normalize_url(cls, value: str) -> str:
        return normalize_database_url(value)

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()
