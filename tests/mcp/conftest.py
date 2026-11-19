"""Fixtures for the MCP tests.

The real API runs in-process (ASGI transport) on the temp SQLite database and frozen clock that
the root ``conftest.py`` sets up. The MCP server under test talks to it through its normal HTTP
code path; only the network socket is replaced.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from mcp.shared.memory import create_connected_server_and_client_session

from helpdesk_mcp.server import ServerBundle, build_server
from helpdesk_mcp.settings import McpSettings
from helpdesk_mcp.spec import Spec, load_spec
from helpdesk_mcp.toolsets import TOOLSETS
from mcp import ClientSession, types
from tests.conftest import KeyFactory

OPERATION_TO_ACTION = {
    op: (tool, action) for tool, a in TOOLSETS.items() for action, op in a.items()
}


@pytest.fixture(scope="session")
def spec() -> Spec:
    return load_spec(McpSettings())


@pytest.fixture
def api_token(make_key: KeyFactory, world: dict[str, str]) -> Callable[..., Any]:
    async def token(agent: str | None = None, scopes: list[str] | None = None) -> str:
        return await make_key(scopes or ["admin"], agent_id=agent or world["admin"])

    return token


Connect = Callable[..., AbstractAsyncContextManager[tuple[ClientSession, ServerBundle]]]


@pytest.fixture
def connect(app: FastAPI, api_token: Callable[..., Any], spec: Spec) -> Connect:
    """Open an MCP client session against a server wired to the in-process API."""

    @asynccontextmanager
    async def factory(
        variant: str = "per-endpoint",
        tool_count: int | None = None,
        *,
        agent: str | None = None,
        token: str | None = None,
        no_key: bool = False,
        **flags: bool,
    ) -> AsyncIterator[tuple[ClientSession, ServerBundle]]:
        key = None if no_key else (token or await api_token(agent))
        settings = McpSettings(api_url="http://test", api_key=key, retries=0, log_level="WARNING")
        http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        bundle = build_server(
            settings, variant=variant, tool_count=tool_count, spec=spec, client=http, **flags
        )
        try:
            async with create_connected_server_and_client_session(bundle.mcp) as session:
                yield session, bundle
        finally:
            await http.aclose()

    return factory


def result_json(result: types.CallToolResult) -> Any:
    assert len(result.content) == 1
    block = result.content[0]
    assert isinstance(block, types.TextContent)
    return json.loads(block.text)


async def invoke(
    session: ClientSession, variant: str, operation_id: str, args: dict[str, Any] | None = None
) -> types.CallToolResult:
    """Call one operation through whichever tool surface the variant offers."""
    args = args or {}
    if variant == "per-endpoint":
        return await session.call_tool(operation_id, args)
    if variant == "toolsets":
        tool, action = OPERATION_TO_ACTION[operation_id]
        return await session.call_tool(tool, {"action": action, "params": args})
    return await session.call_tool("call_api", {"operation_id": operation_id, "params": args})
