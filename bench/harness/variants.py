"""The benchmark variants: how the helpdesk MCP server is started and how Claude Code loads it.

Tool search (deferred loading of MCP tool definitions) is controlled by two things, found
empirically with Claude Code 2.1.326 (see docs/benchmark.md):

* the built-in ``ToolSearch`` tool must be available (``--tools "ToolSearch"``); with ``--tools ""``
  deferral is impossible whatever the environment says, and
* ``ENABLE_TOOL_SEARCH`` must not be ``false``.

The main matrix therefore uses ``--tools ""`` and ``ENABLE_TOOL_SEARCH=false`` (definitions are
sent up front, twice guaranteed). The extra variant uses ``--tools ToolSearch`` and
``ENABLE_TOOL_SEARCH=true``.
"""

from __future__ import annotations

from dataclasses import dataclass

MAIN_TOOL_SEARCH_ENV = "false"
CLIENT_TOOL_SEARCH_ENV = "true"
CLIENT_TOOL_SEARCH_SUFFIX = "+client-tool-search"
NO_SERVER = "none"


@dataclass(frozen=True)
class BenchVariant:
    name: str
    kind: str  # per-endpoint | toolsets | search-call | none
    tool_count: int  # tools the server lists (0 for no server)
    server_tool_count: int | None = None  # --tool-count value for per-endpoint
    client_tool_search: bool = False
    # Only the tool-search probe sets these; the matrix never does.
    tools_override: str | None = None
    env_override: str | None = None

    @property
    def has_server(self) -> bool:
        return self.kind != "none"

    @property
    def mcp_args(self) -> list[str] | None:
        if not self.has_server:
            return None
        args = ["--variant", self.kind]
        if self.server_tool_count is not None:
            args += ["--tool-count", str(self.server_tool_count)]
        return args

    @property
    def tool_search_env(self) -> str:
        if self.env_override is not None:
            return self.env_override
        return CLIENT_TOOL_SEARCH_ENV if self.client_tool_search else MAIN_TOOL_SEARCH_ENV

    @property
    def builtin_tools(self) -> str:
        if self.tools_override is not None:
            return self.tools_override
        return "ToolSearch" if self.client_tool_search else ""

    @property
    def exposes_all_tools_up_front(self) -> bool:
        return not self.client_tool_search


def _per_endpoint(n: int, *, client_tool_search: bool = False) -> BenchVariant:
    suffix = CLIENT_TOOL_SEARCH_SUFFIX if client_tool_search else ""
    return BenchVariant(f"per-endpoint-{n}{suffix}", "per-endpoint", n, n, client_tool_search)


VARIANTS: dict[str, BenchVariant] = {
    v.name: v
    for v in (
        _per_endpoint(10),
        _per_endpoint(25),
        _per_endpoint(50),
        _per_endpoint(100),
        BenchVariant("toolsets", "toolsets", 8),
        BenchVariant("search-call", "search-call", 3),
        _per_endpoint(100, client_tool_search=True),
    )
}
NO_SERVER_VARIANTS: dict[str, BenchVariant] = {
    NO_SERVER: BenchVariant(NO_SERVER, "none", 0),
    NO_SERVER + CLIENT_TOOL_SEARCH_SUFFIX: BenchVariant(
        NO_SERVER + CLIENT_TOOL_SEARCH_SUFFIX, "none", 0, client_tool_search=True
    ),
}
ALL_VARIANTS: dict[str, BenchVariant] = {**VARIANTS, **NO_SERVER_VARIANTS}


def baseline_for(variant: BenchVariant) -> BenchVariant:
    """The no-server variant with the same Claude Code configuration (same built-in tools)."""
    key = NO_SERVER + (CLIENT_TOOL_SEARCH_SUFFIX if variant.client_tool_search else "")
    return NO_SERVER_VARIANTS[key]
