from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from helpdesk.api.deps import Pagination, current_principal, scoped, service
from helpdesk.api.routers._common import ERROR_RESPONSES, to_page
from helpdesk.api.schemas.common import Paginated
from helpdesk.api.schemas.config import SettingsOut, SettingsUpdate
from helpdesk.api.schemas.platform import (
    AgentWorkloadReport,
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyOut,
    AuditEventOut,
    BacklogReport,
    CsatReport,
    SlaComplianceReport,
    TicketVolumeReport,
    WhoAmI,
)
from helpdesk.services.admin import ApiKeyService, SettingsService
from helpdesk.services.audit import AuditService
from helpdesk.services.base import Principal
from helpdesk.services.reports import ReportService

reports_router = APIRouter(prefix="/reports", tags=["reports"], responses=ERROR_RESPONSES)
audit_router = APIRouter(prefix="/audit", tags=["audit"], responses=ERROR_RESPONSES)
admin_router = APIRouter(prefix="/admin", tags=["admin"], responses=ERROR_RESPONSES)

Reports = Annotated[ReportService, Depends(service(ReportService))]
Audit = Annotated[AuditService, Depends(service(AuditService))]
SettingsSvc = Annotated[SettingsService, Depends(service(SettingsService))]
Keys = Annotated[ApiKeyService, Depends(service(ApiKeyService))]
StartQ = Annotated[
    datetime | None, Query(description="Include tickets created at or after this time.")
]
EndQ = Annotated[datetime | None, Query(description="Include tickets created before this time.")]


@reports_router.get(
    "/ticket-volume",
    operation_id="reports_ticket_volume",
    summary="Report ticket volume",
    description=(
        "Counts tickets created in a period, grouped by `group_by`: day, status, priority or "
        "team. Optional `start` / `end` (ISO 8601) bound the period. Use it for 'how many "
        "tickets did we get last week?' questions."
    ),
    response_model=TicketVolumeReport,
    dependencies=scoped("reports:read"),
)
async def reports_ticket_volume(
    svc: Reports,
    group_by: Literal["day", "status", "priority", "team"] = "day",
    start: StartQ = None,
    end: EndQ = None,
) -> TicketVolumeReport:
    return TicketVolumeReport.model_validate(await svc.ticket_volume(group_by, start, end))


@reports_router.get(
    "/sla-compliance",
    operation_id="reports_sla_compliance",
    summary="Report SLA compliance",
    description=(
        "Per priority: how many tickets were tracked against first-response and resolution "
        "targets, how many breached, and the compliance percentage. Includes overall figures. "
        "Optional `start` / `end` bound by ticket creation time."
    ),
    response_model=SlaComplianceReport,
    dependencies=scoped("reports:read"),
)
async def reports_sla_compliance(
    svc: Reports, start: StartQ = None, end: EndQ = None
) -> SlaComplianceReport:
    return SlaComplianceReport.model_validate(await svc.sla_compliance(start, end))


@reports_router.get(
    "/agent-workload",
    operation_id="reports_agent_workload",
    summary="Report agent workload",
    description=(
        "Active tickets (new, open, pending) per agent with a breakdown by status, busiest first. "
        "Use it to decide who should take the next ticket."
    ),
    response_model=AgentWorkloadReport,
    dependencies=scoped("reports:read"),
)
async def reports_agent_workload(svc: Reports) -> AgentWorkloadReport:
    return AgentWorkloadReport.model_validate(await svc.agent_workload())


@reports_router.get(
    "/csat",
    operation_id="reports_csat_summary",
    summary="Report customer satisfaction",
    description=(
        "Summarises CSAT ratings: response count, average score and the distribution of scores "
        "1-5. Optional `start` / `end` bound by ticket creation time."
    ),
    response_model=CsatReport,
    dependencies=scoped("reports:read"),
)
async def reports_csat_summary(svc: Reports, start: StartQ = None, end: EndQ = None) -> CsatReport:
    return CsatReport.model_validate(await svc.csat_summary(start, end))


@reports_router.get(
    "/backlog",
    operation_id="reports_backlog",
    summary="Report the open backlog",
    description="Counts active tickets (new, open, pending) by status and priority. Use it to see how big the backlog is and how urgent it is.",
    response_model=BacklogReport,
    dependencies=scoped("reports:read"),
)
async def reports_backlog(svc: Reports) -> BacklogReport:
    return BacklogReport.model_validate(await svc.backlog())


@audit_router.get(
    "/events",
    operation_id="audit_list_events",
    summary="List audit events",
    description=(
        "Every write to the system is recorded. Filter by `actor` (API key id or agent id), "
        "`entity_type` (ticket, customer, refund, ...), `entity_id`, `action` (for example "
        "`refunds.approve`), `request_id`, and a `since` / `until` time window. Newest first. "
        "Use it to answer 'who changed this and when?'."
    ),
    response_model=Paginated[AuditEventOut],
    dependencies=scoped("audit:read"),
)
async def audit_list_events(
    svc: Audit,
    page: Pagination,
    actor: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    action: str | None = None,
    request_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> Paginated[AuditEventOut]:
    result = await svc.list(
        actor=actor,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        request_id=request_id,
        since=since,
        until=until,
        limit=page.limit,
        cursor=page.cursor,
    )
    return to_page(result, AuditEventOut)


@audit_router.get(
    "/events/{event_id}",
    operation_id="audit_get_event",
    summary="Get one audit event",
    description="Returns a single audit event with the before/after values of the fields it changed.",
    response_model=AuditEventOut,
    dependencies=scoped("audit:read"),
)
async def audit_get_event(event_id: str, svc: Audit) -> AuditEventOut:
    return AuditEventOut.model_validate(await svc.get(event_id))


@admin_router.get(
    "/whoami",
    operation_id="admin_whoami",
    summary="Show the calling API key",
    description=(
        "Returns the key id, name, scopes and linked agent (with role) for the key making the "
        "call. Useful to check what you are allowed to do, for example whether you can approve "
        "refunds."
    ),
    response_model=WhoAmI,
    dependencies=scoped(),
)
async def admin_whoami(principal: Annotated[Principal, Depends(current_principal)]) -> WhoAmI:
    return WhoAmI(
        api_key_id=principal.api_key_id,
        name=principal.name,
        scopes=sorted(principal.scopes),
        agent_id=principal.agent_id,
        agent_role=principal.agent_role,
    )


@admin_router.get(
    "/settings",
    operation_id="admin_settings_get",
    summary="Get business settings",
    description=(
        "Returns the effective refund window (days), refund approval threshold and ticket reopen "
        "window (days), and which of them were changed from the defaults."
    ),
    response_model=SettingsOut,
    dependencies=scoped("admin"),
)
async def admin_settings_get(svc: SettingsSvc) -> SettingsOut:
    return SettingsOut.model_validate(await svc.get())


@admin_router.patch(
    "/settings",
    operation_id="admin_settings_update",
    summary="Change business settings",
    description=(
        "Updates `refund_window_days`, `refund_approval_threshold` and/or `reopen_window_days`. "
        "Takes effect immediately for new operations. Send only the fields to change."
    ),
    response_model=SettingsOut,
    dependencies=scoped("admin"),
)
async def admin_settings_update(body: SettingsUpdate, svc: SettingsSvc) -> SettingsOut:
    return SettingsOut.model_validate(await svc.update(body.model_dump(exclude_unset=True)))


@admin_router.get(
    "/api-keys",
    operation_id="admin_api_keys_list",
    summary="List API keys",
    description="Lists API keys with scopes, linked agent and revoked/expiry state. Secrets are never returned.",
    response_model=list[ApiKeyOut],
    dependencies=scoped("admin"),
)
async def admin_api_keys_list(svc: Keys) -> list[ApiKeyOut]:
    return [ApiKeyOut.model_validate(k) for k in await svc.list()]


@admin_router.post(
    "/api-keys",
    operation_id="admin_api_keys_create",
    summary="Create an API key",
    description=(
        "Creates a key with the given `scopes` (for example tickets:read, refunds:write, or "
        "admin for everything). Link it to an `agent_id` so the agent's role applies (leads can "
        "approve refunds). The plaintext `token` is returned once and cannot be recovered."
    ),
    response_model=ApiKeyCreated,
    status_code=201,
    dependencies=scoped("admin"),
)
async def admin_api_keys_create(body: ApiKeyCreate, svc: Keys) -> ApiKeyCreated:
    key, token = await svc.create(body.name, body.scopes, body.agent_id, body.expires_in_days)
    return ApiKeyCreated(**ApiKeyOut.model_validate(key).model_dump(), token=token)


@admin_router.get(
    "/api-keys/{key_id}",
    operation_id="admin_api_keys_get",
    summary="Get an API key",
    description="Returns one API key's metadata (for example `K-003`): scopes, linked agent, expiry, last use and revocation. The secret is never returned.",
    response_model=ApiKeyOut,
    dependencies=scoped("admin"),
)
async def admin_api_keys_get(key_id: str, svc: Keys) -> ApiKeyOut:
    return ApiKeyOut.model_validate(await svc.get(key_id))


@admin_router.post(
    "/api-keys/{key_id}/revoke",
    operation_id="admin_api_keys_revoke",
    summary="Revoke an API key",
    description="Immediately disables the API key; later requests using it get 401. Use it when a key leaks or an integration is retired. Safe to repeat.",
    response_model=ApiKeyOut,
    dependencies=scoped("admin"),
)
async def admin_api_keys_revoke(key_id: str, svc: Keys) -> ApiKeyOut:
    return ApiKeyOut.model_validate(await svc.revoke(key_id))
