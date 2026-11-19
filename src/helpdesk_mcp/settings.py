"""Typed settings for the MCP server, read from ``HELPDESK_MCP_*`` environment variables."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class McpSettings(BaseSettings):
    """Everything the MCP server needs to know. Each field maps to ``HELPDESK_MCP_<FIELD>``."""

    model_config = SettingsConfigDict(env_prefix="HELPDESK_MCP_", extra="ignore")

    api_url: str = Field(
        default="http://127.0.0.1:8000", description="Base URL of the Helpdesk API."
    )
    api_key: SecretStr | None = Field(
        default=None, description="API key sent as a bearer token. Never logged."
    )
    timeout_seconds: float = Field(default=30.0, gt=0, description="Per-request HTTP timeout.")
    retries: int = Field(
        default=1,
        ge=0,
        le=5,
        description="Extra attempts after a connection error or timeout (same Idempotency-Key).",
    )
    openapi_path: Path | None = Field(
        default=None,
        description="openapi.json to build tools from. Default: the copy in the repository or "
        "package; if none exists, it is fetched from the API at /openapi.json.",
    )
    log_level: str = "INFO"
    log_json: bool = True
    host: str = Field(default="127.0.0.1", description="Bind address for streamable HTTP.")
    port: int = Field(default=8765, description="Port for streamable HTTP.")

    @property
    def base_url(self) -> str:
        return self.api_url.rstrip("/")
