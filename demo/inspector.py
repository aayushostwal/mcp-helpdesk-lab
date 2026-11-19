"""Ask a real helpdesk-mcp server, over stdio, what it exposes. No fake UI: just the data."""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

from mcp.client.stdio import stdio_client

from mcp import ClientSession, StdioServerParameters


async def _collect(variant: str) -> dict[str, Any]:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "helpdesk_mcp", "--variant", variant],
        # Listing needs no API: point it at a closed port so nothing real is touched.
        env={
            **{k: v for k, v in os.environ.items() if not k.startswith("HELPDESK")},
            "HELPDESK_MCP_API_URL": "http://127.0.0.1:9",
            "HELPDESK_MCP_API_KEY": "hd_inspector_placeholder",
            "HELPDESK_MCP_LOG_LEVEL": "WARNING",
        },
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        init = await session.initialize()
        resources = await session.list_resources()
        templates = await session.list_resource_templates()
        prompts = await session.list_prompts()
        tools = await session.list_tools()
    return {
        "variant": variant,
        "server_name": init.serverInfo.name,
        "server_version": init.serverInfo.version,
        "protocol": str(init.protocolVersion),
        "instructions": init.instructions or "",
        "resources": [
            {
                "uri": str(r.uri),
                "name": r.name,
                "description": r.description or "",
                "mime": r.mimeType or "",
            }
            for r in resources.resources
        ],
        "templates": [
            {
                "uri": t.uriTemplate,
                "name": t.name,
                "description": t.description or "",
                "mime": t.mimeType or "",
            }
            for t in templates.resourceTemplates
        ],
        "prompts": [
            {
                "name": p.name,
                "description": p.description or "",
                "arguments": [
                    f"{a.name}{' (required)' if a.required else ' (optional)'}"
                    + (f": {a.description}" if a.description else "")
                    for a in (p.arguments or [])
                ],
            }
            for p in prompts.prompts
        ],
        "tools": [t.name for t in tools.tools],
    }


def collect(variant: str) -> dict[str, Any]:
    return asyncio.run(_collect(variant))
