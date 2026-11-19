"""Assemble the MCP server: context features from FastMCP, tool handling from a variant.

FastMCP supplies instructions, resources, prompts and both transports. Its decorator-based tool
registry builds each tool's schema from a Python function signature, which cannot express the
schemas generated from OpenAPI. So the two tool handlers (``tools/list`` and ``tools/call``) are
replaced with ones that delegate to the selected :class:`~helpdesk_mcp.variants.Variant`. This
uses ``FastMCP._mcp_server``, the same low-level server FastMCP itself registers on.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

from helpdesk_mcp import __version__, content
from helpdesk_mcp.executor import Executor, problem
from helpdesk_mcp.logging import get_logger
from helpdesk_mcp.settings import McpSettings
from helpdesk_mcp.spec import Spec, load_spec
from helpdesk_mcp.variants import Variant, make_variant
from mcp import types

SERVER_NAME = "helpdesk"

_RESOURCE_HANDLERS: tuple[type, ...] = (
    types.ListResourcesRequest,
    types.ReadResourceRequest,
    types.ListResourceTemplatesRequest,
)
_PROMPT_HANDLERS: tuple[type, ...] = (types.ListPromptsRequest, types.GetPromptRequest)


@dataclass
class ServerBundle:
    """The FastMCP app plus the parts a caller may need to inspect or close."""

    mcp: FastMCP
    variant: Variant
    executor: Executor

    async def aclose(self) -> None:
        await self.executor.aclose()


def build_server(
    settings: McpSettings,
    *,
    variant: str = "per-endpoint",
    tool_count: int | None = None,
    instructions: bool = True,
    resources: bool = True,
    prompts: bool = True,
    spec: Spec | None = None,
    client: httpx.AsyncClient | None = None,
) -> ServerBundle:
    """Build a server. ``client`` lets tests route HTTP calls into an in-process API."""
    spec = spec or load_spec(settings)
    executor = Executor(spec, settings, client)
    chosen = make_variant(variant, spec, executor, tool_count)
    mcp = FastMCP(
        SERVER_NAME,
        instructions=content.INSTRUCTIONS if instructions else None,
        host=settings.host,
        port=settings.port,
        log_level="WARNING",
    )
    _install_tools(mcp, chosen, variant)
    if resources:
        _add_resources(mcp, executor)
    else:
        for request_type in _RESOURCE_HANDLERS:
            mcp._mcp_server.request_handlers.pop(request_type, None)
    if prompts:
        _add_prompts(mcp)
    else:
        for request_type in _PROMPT_HANDLERS:
            mcp._mcp_server.request_handlers.pop(request_type, None)
    mcp._mcp_server.version = __version__
    return ServerBundle(mcp, chosen, executor)


def _install_tools(mcp: FastMCP, variant: Variant, variant_name: str) -> None:
    log = get_logger()
    server = mcp._mcp_server

    async def list_tools() -> list[types.Tool]:
        return variant.tools()

    async def call_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        started = time.perf_counter()
        try:
            outcome = await variant.call(name, arguments or {})
            result, operation_id = outcome.result, outcome.operation_id
        except Exception as exc:  # a bug here must not end the session
            log.error("tool_crashed", tool=name, variant=variant_name, error=type(exc).__name__)
            result = problem(500, "internal-error", "Tool failed", "The MCP server hit an error.")
            operation_id = None
        log.info(
            "tool_call",
            tool=name,
            variant=variant_name,
            operation_id=operation_id,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
            status=result.status,
            ok=result.ok,
            error_type=result.error_type,
            request_id=result.request_id,
        )
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=result.text())],
            isError=not result.ok,
        )

    # The SDK's decorators are untyped; they only register the handler on the low-level server.
    server.list_tools()(list_tools)  # type: ignore[no-untyped-call]
    server.call_tool(validate_input=False)(call_tool)


def _add_resources(mcp: FastMCP, executor: Executor) -> None:
    @mcp.resource(
        "helpdesk://schema",
        name="schema",
        description="Entities, id formats, relationships and value sets of the helpdesk.",
        mime_type="text/markdown",
    )
    def schema() -> str:
        return content.SCHEMA_RESOURCE

    @mcp.resource(
        "helpdesk://policies/refunds",
        name="refund-policy",
        description="Refund rules: order status, 30-day window, balance, approval above 500.00.",
        mime_type="text/markdown",
    )
    def refund_policy() -> str:
        return content.REFUND_POLICY_RESOURCE

    @mcp.resource(
        "helpdesk://policies/sla",
        name="sla-policy",
        description="SLA targets per priority and how first response and resolution are judged.",
        mime_type="text/markdown",
    )
    def sla_policy() -> str:
        return content.SLA_POLICY_RESOURCE

    @mcp.resource(
        "helpdesk://tickets/{ticket_id}",
        name="ticket",
        description="One ticket as JSON, for example helpdesk://tickets/T-1042.",
        mime_type="application/json",
    )
    async def ticket(ticket_id: str) -> str:
        result = await executor.call("tickets_get", {"ticket_id": ticket_id})
        if not result.ok:
            raise ValueError(result.text())
        return result.text()


def _add_prompts(mcp: FastMCP) -> None:
    @mcp.prompt(
        name="triage_ticket",
        description="Read a ticket, check SLA and customer context, then route it.",
    )
    def triage_ticket(ticket_id: str) -> str:
        return content.triage_ticket_prompt(ticket_id)

    @mcp.prompt(
        name="weekly_sla_report",
        description="Write a read-only weekly SLA report, optionally for one team.",
    )
    def weekly_sla_report(team: str | None = None) -> str:
        return content.weekly_sla_report_prompt(team)

    @mcp.prompt(
        name="refund_request",
        description="Handle a customer's refund request for an order under the refund policy.",
    )
    def refund_request(order_id: str, reason: str) -> str:
        return content.refund_request_prompt(order_id, reason)
