"""``helpdesk-mcp``: run the MCP server over stdio (default) or streamable HTTP."""

from __future__ import annotations

import sys
from typing import Annotated, Literal

import anyio
import typer

from helpdesk_mcp.logging import configure_logging
from helpdesk_mcp.server import ServerBundle, build_server
from helpdesk_mcp.settings import McpSettings
from helpdesk_mcp.spec import SpecError
from helpdesk_mcp.subset import ToolCountError
from helpdesk_mcp.variants import VARIANTS

app = typer.Typer(add_completion=False, help="Helpdesk MCP server.")

Transport = Literal["stdio", "streamable-http"]


async def _run(bundle: ServerBundle, transport: str) -> None:
    try:
        if transport == "stdio":
            await bundle.mcp.run_stdio_async()
        else:
            await bundle.mcp.run_streamable_http_async()
    finally:
        await bundle.aclose()


@app.command()
def main(
    variant: Annotated[
        str, typer.Option(help=f"Tool design: {', '.join(VARIANTS)}.")
    ] = "per-endpoint",
    tool_count: Annotated[
        int | None,
        typer.Option(help="per-endpoint only: expose N tools (10, 25, 50, 100). Default: all."),
    ] = None,
    transport: Annotated[str, typer.Option(help="stdio (default) or streamable-http.")] = "stdio",
    no_instructions: Annotated[
        bool, typer.Option("--no-instructions", help="Do not send server instructions.")
    ] = False,
    no_resources: Annotated[
        bool, typer.Option("--no-resources", help="Do not expose resources.")
    ] = False,
    no_prompts: Annotated[
        bool, typer.Option("--no-prompts", help="Do not expose prompts.")
    ] = False,
) -> None:
    """Start the server. Configure the API with HELPDESK_MCP_API_URL and HELPDESK_MCP_API_KEY."""
    if transport not in ("stdio", "streamable-http"):
        raise typer.BadParameter("must be 'stdio' or 'streamable-http'", param_hint="--transport")
    settings = McpSettings()
    configure_logging(settings.log_level, settings.log_json)
    try:
        bundle = build_server(
            settings,
            variant=variant,
            tool_count=tool_count,
            instructions=not no_instructions,
            resources=not no_resources,
            prompts=not no_prompts,
        )
    except (ValueError, ToolCountError, SpecError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc
    if settings.api_key is None:
        typer.echo("warning: HELPDESK_MCP_API_KEY is not set; API calls will get 401.", err=True)
    try:
        anyio.run(_run, bundle, transport)
    except KeyboardInterrupt:  # pragma: no cover
        sys.exit(130)


__all__ = ["app", "main"]
