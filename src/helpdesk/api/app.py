"""Application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from helpdesk import __version__
from helpdesk.api.middleware import IdempotencyMiddleware, RequestContextMiddleware
from helpdesk.api.problems import install_exception_handlers, problem_response
from helpdesk.api.routers import agents, commerce, config, customers, platform, tickets
from helpdesk.clock import Clock, clock_from_settings
from helpdesk.config import Settings, get_settings
from helpdesk.db import make_engine, make_session_factory
from helpdesk.integrations.webhook_sender import (
    HttpxWebhookSender,
    SimulatedWebhookSender,
    WebhookSender,
)
from helpdesk.logging import configure_logging
from helpdesk.metrics import CONTENT_TYPE_LATEST, Metrics

DESCRIPTION = """
A support-desk backend: customers, tickets, orders, refunds, SLAs, knowledge base and more.

**Auth.** Send `Authorization: Bearer hd_...`. Keys carry scopes (`tickets:read`, ...).
**Errors.** `application/problem+json` (RFC 9457) with a stable `type` and a `request_id`.
**Pagination.** `limit` (max 100) and `cursor`; responses carry `next_cursor`.
**Idempotency.** Send `Idempotency-Key` on POSTs; repeats within 24h replay the first response.
"""

TAGS = [
    {"name": "customers", "description": "People who contact support."},
    {"name": "agents", "description": "Support staff."},
    {"name": "teams", "description": "Agent teams and on-call."},
    {"name": "tickets", "description": "Support tickets and their lifecycle."},
    {"name": "comments", "description": "Public replies and internal notes."},
    {"name": "tags", "description": "Ticket labels."},
    {"name": "orders", "description": "Customer orders."},
    {"name": "refunds", "description": "Policy-checked refunds with lead approval."},
    {"name": "sla", "description": "SLA policies and breach checks."},
    {"name": "macros", "description": "Canned replies with actions."},
    {"name": "knowledge-base", "description": "Help articles."},
    {"name": "webhooks", "description": "Outbound event subscriptions."},
    {"name": "reports", "description": "Aggregate reporting."},
    {"name": "audit", "description": "Who changed what."},
    {"name": "admin", "description": "Settings and API keys."},
]


def create_app(
    settings: Settings | None = None,
    *,
    clock: Clock | None = None,
    webhook_sender: WebhookSender | None = None,
    engine: AsyncEngine | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)
    engine = engine or make_engine(settings)
    metrics = Metrics()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        await engine.dispose()

    app = FastAPI(
        title="Helpdesk API",
        version=__version__,
        description=DESCRIPTION,
        openapi_tags=TAGS,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.clock = clock or clock_from_settings(settings.frozen_time)
    app.state.metrics = metrics
    app.state.webhook_sender = webhook_sender or (
        SimulatedWebhookSender()
        if settings.webhook_delivery == "simulated"
        else HttpxWebhookSender()
    )

    install_exception_handlers(app)
    app.add_middleware(IdempotencyMiddleware)
    app.add_middleware(RequestContextMiddleware, metrics=metrics)

    for router in (
        customers.router,
        agents.agents_router,
        agents.teams_router,
        tickets.router,
        tickets.comments_router,
        tickets.tags_router,
        commerce.orders_router,
        commerce.refunds_router,
        config.sla_router,
        config.macros_router,
        config.kb_router,
        config.webhooks_router,
        platform.reports_router,
        platform.audit_router,
        platform.admin_router,
    ):
        app.include_router(router, prefix="/v1")
    _add_operational_routes(app)
    app.openapi = lambda: _openapi(app)  # type: ignore[method-assign]
    return app


def _add_operational_routes(app: FastAPI) -> None:
    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    async def readyz(request: Request) -> Response:
        try:
            async with request.app.state.session_factory() as session:
                await session.execute(text("SELECT 1"))
        except Exception:
            return problem_response(
                request,
                status=503,
                slug="not-ready",
                title="Service not ready",
                detail="The database is not reachable.",
            )
        return JSONResponse({"status": "ready", "database": "ok"})

    @app.get("/metrics", include_in_schema=False)
    async def metrics_endpoint(request: Request) -> Response:
        return Response(request.app.state.metrics.render(), media_type=CONTENT_TYPE_LATEST)


def _openapi(app: FastAPI) -> dict[str, Any]:
    if app.openapi_schema:
        return app.openapi_schema
    from fastapi.openapi.utils import get_openapi

    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
        tags=TAGS,
    )
    # Error bodies are RFC 9457 problem documents, not plain JSON.
    for path_item in schema["paths"].values():
        for operation in path_item.values():
            for status, response in operation.get("responses", {}).items():
                content = response.get("content")
                if (
                    status.isdigit()
                    and int(status) >= 400
                    and content
                    and "application/json" in content
                ):
                    content["application/problem+json"] = content.pop("application/json")
    app.openapi_schema = schema
    return schema
