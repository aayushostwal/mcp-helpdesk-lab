from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from helpdesk.api.schemas.common import InModel, OutModel


class AuditEventOut(OutModel):
    id: str
    at: datetime
    api_key_id: str | None
    agent_id: str | None
    action: str = Field(examples=["tickets.escalate"])
    entity_type: str
    entity_id: str
    before: dict[str, Any] | None = Field(description="Changed fields, previous values.")
    after: dict[str, Any] | None = Field(description="Changed fields, new values.")
    request_id: str | None


class ApiKeyOut(OutModel):
    id: str = Field(examples=["K-003"])
    name: str
    prefix: str = Field(description="First characters of the token, for identification.")
    scopes: list[str]
    agent_id: str | None
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None


class ApiKeyCreated(ApiKeyOut):
    token: str = Field(description="The API key. Shown only in this response; store it safely.")


class ApiKeyCreate(InModel):
    name: str = Field(min_length=1, max_length=100)
    scopes: list[str] = Field(min_length=1)
    agent_id: str | None = Field(
        default=None, description="Link the key to an agent (required to approve refunds)."
    )
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class WhoAmI(OutModel):
    api_key_id: str
    name: str
    scopes: list[str]
    agent_id: str | None
    agent_role: str | None


class TicketVolumeBucket(OutModel):
    key: str
    count: int


class TicketVolumeReport(OutModel):
    group_by: str
    start: datetime | None
    end: datetime | None
    total: int
    buckets: list[TicketVolumeBucket]


class SlaComplianceRow(OutModel):
    priority: str
    tickets: int
    first_response_tracked: int
    first_response_breached: int
    first_response_compliance_pct: float | None
    resolution_tracked: int
    resolution_breached: int
    resolution_compliance_pct: float | None


class SlaComplianceReport(OutModel):
    start: datetime | None
    end: datetime | None
    evaluated_at: datetime
    by_priority: list[SlaComplianceRow]
    overall_first_response_compliance_pct: float | None
    overall_resolution_compliance_pct: float | None


class AgentWorkloadRow(OutModel):
    agent_id: str
    agent_name: str
    team_id: str | None
    active_tickets: int
    by_status: dict[str, int]


class AgentWorkloadReport(OutModel):
    total_active_tickets: int
    agents: list[AgentWorkloadRow]


class CsatReport(OutModel):
    start: datetime | None
    end: datetime | None
    responses: int
    average_score: float | None
    distribution: dict[str, int]


class BacklogCell(OutModel):
    status: str
    priority: str
    count: int


class BacklogReport(OutModel):
    total_active: int
    cells: list[BacklogCell]
