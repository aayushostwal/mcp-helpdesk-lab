"""The three tool designs. Each one lists tools and routes calls to the shared executor.

A variant never talks to HTTP itself: it maps a tool call onto ``(operation_id, arguments)``
and hands that to :meth:`Executor.call`. That is what keeps results identical across variants.
"""

from __future__ import annotations

import difflib
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from helpdesk_mcp.executor import ExecResult, Executor, problem
from helpdesk_mcp.search import Bm25Index, build_index
from helpdesk_mcp.spec import Operation, Spec
from helpdesk_mcp.subset import select_operations
from helpdesk_mcp.toolsets import TOOLSET_INTRO, TOOLSETS
from mcp import types

VARIANTS = ("per-endpoint", "toolsets", "search-call")
SEARCH_DEFAULT_LIMIT = 5
SEARCH_MAX_LIMIT = 20


@dataclass(frozen=True)
class Outcome:
    """What a tool call produced. ``operation_id`` is None for tools that call no operation."""

    result: ExecResult
    operation_id: str | None


def annotations_for(methods: set[str]) -> types.ToolAnnotations:
    """Hints from HTTP methods: GET reads, DELETE destroys, only POST is not idempotent."""
    return types.ToolAnnotations(
        readOnlyHint=methods <= {"GET"},
        destructiveHint="DELETE" in methods,
        idempotentHint="POST" not in methods,
        openWorldHint=False,
    )


class Variant(ABC):
    name: str

    def __init__(self, spec: Spec, executor: Executor) -> None:
        self.spec = spec
        self.executor = executor

    @abstractmethod
    def tools(self) -> list[types.Tool]: ...

    @abstractmethod
    async def call(self, tool_name: str, arguments: Mapping[str, Any]) -> Outcome: ...


class PerEndpoint(Variant):
    """One tool per operation. Name, description and schema come straight from the OpenAPI file."""

    name = "per-endpoint"

    def __init__(self, spec: Spec, executor: Executor, tool_count: int | None = None) -> None:
        super().__init__(spec, executor)
        self.operation_ids = select_operations(spec, tool_count)

    def tools(self) -> list[types.Tool]:
        out = []
        for op_id in self.operation_ids:
            op = self.spec.operations[op_id]
            out.append(
                types.Tool(
                    name=op.operation_id,
                    description=op.tool_description,
                    inputSchema=op.input_schema,
                    annotations=annotations_for({op.method}),
                )
            )
        return out

    async def call(self, tool_name: str, arguments: Mapping[str, Any]) -> Outcome:
        if tool_name not in self.operation_ids:
            return Outcome(
                problem(
                    404, "unknown-tool", "Unknown tool", f"This server has no tool '{tool_name}'."
                ),
                None,
            )
        return Outcome(await self.executor.call(tool_name, arguments), tool_name)


def _action_line(action: str, op: Operation) -> str:
    names = [f"{n}*" if n in op.required_arguments else n for n in op.argument_names]
    params = ", ".join(names) if names else "none"
    return f"- {action}: {op.summary} (params: {params})"


class Toolsets(Variant):
    """Eight domain tools; ``action`` picks the operation, ``params`` carries its arguments."""

    name = "toolsets"

    def __init__(self, spec: Spec, executor: Executor) -> None:
        super().__init__(spec, executor)
        for tool, actions in TOOLSETS.items():
            for action, op_id in actions.items():
                if op_id not in spec.operations:
                    raise ValueError(f"toolset {tool}.{action} maps to unknown operation {op_id}")

    def tools(self) -> list[types.Tool]:
        out = []
        for tool, actions in TOOLSETS.items():
            ops = {action: self.spec.operations[op_id] for action, op_id in actions.items()}
            lines = [_action_line(action, op) for action, op in ops.items()]
            description = (
                f"{TOOLSET_INTRO[tool]}\n\n"
                "Pass `action` (one of the names below) and `params` (an object with that "
                "action's arguments; * marks required ones).\n\nActions:\n" + "\n".join(lines)
            )
            schema = {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": list(actions)},
                    "params": {
                        "type": "object",
                        "description": "Arguments for the action, as listed in the description.",
                    },
                },
                "required": ["action"],
                "additionalProperties": False,
            }
            out.append(
                types.Tool(
                    name=tool,
                    description=description,
                    inputSchema=schema,
                    annotations=annotations_for({op.method for op in ops.values()}),
                )
            )
        return out

    async def call(self, tool_name: str, arguments: Mapping[str, Any]) -> Outcome:
        actions = TOOLSETS.get(tool_name)
        if actions is None:
            return Outcome(
                problem(
                    404, "unknown-tool", "Unknown tool", f"This server has no tool '{tool_name}'."
                ),
                None,
            )
        action = arguments.get("action")
        if not isinstance(action, str) or action not in actions:
            return Outcome(
                problem(
                    422,
                    "invalid-arguments",
                    "Unknown action",
                    f"'{action}' is not an action of {tool_name}. Actions: {sorted(actions)}.",
                    allowed=sorted(actions),
                ),
                None,
            )
        params = arguments.get("params") or {}
        extra = sorted(set(arguments) - {"action", "params"})
        if not isinstance(params, Mapping) or extra:
            return Outcome(
                problem(
                    422,
                    "invalid-arguments",
                    "Invalid arguments",
                    "Pass only `action` and `params`; `params` must be an object"
                    + (f" (unexpected: {extra})." if extra else "."),
                ),
                actions[action],
            )
        op_id = actions[action]
        return Outcome(await self.executor.call(op_id, params), op_id)


class SearchCall(Variant):
    """Three tools: search the operations, read one in full, call one."""

    name = "search-call"

    def __init__(self, spec: Spec, executor: Executor) -> None:
        super().__init__(spec, executor)
        self.index: Bm25Index = build_index(spec.operations.values())

    def search(self, query: str, limit: int = SEARCH_DEFAULT_LIMIT) -> list[dict[str, Any]]:
        results = []
        for hit in self.index.search(query, limit):
            op = self.spec.operations[hit.operation_id]
            results.append(
                {
                    "operation_id": op.operation_id,
                    "summary": op.summary,
                    "method": op.method,
                    "path": op.path,
                    "required": list(op.required_arguments),
                    "score": round(hit.score, 3),
                }
            )
        return results

    def tools(self) -> list[types.Tool]:
        read_only = types.ToolAnnotations(
            readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
        )
        return [
            types.Tool(
                name="search_api",
                description=(
                    "Find Helpdesk API operations by keyword (for example 'refund an order'). "
                    "Returns the best matches with their ids and one-line summaries. Use "
                    "describe_operation for the full input schema, then call_api to run one."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "What you want to do."},
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": SEARCH_MAX_LIMIT,
                            "default": SEARCH_DEFAULT_LIMIT,
                            "description": "Maximum number of results.",
                        },
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
                annotations=read_only,
            ),
            types.Tool(
                name="describe_operation",
                description=(
                    "Get the full description and input schema of one operation, as returned "
                    "by search_api."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {"operation_id": {"type": "string"}},
                    "required": ["operation_id"],
                    "additionalProperties": False,
                },
                annotations=read_only,
            ),
            types.Tool(
                name="call_api",
                description=(
                    "Run one operation. `params` holds its arguments exactly as described by "
                    "describe_operation (path, query and body fields in one flat object)."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "operation_id": {"type": "string"},
                        "params": {"type": "object", "description": "Arguments for the operation."},
                    },
                    "required": ["operation_id"],
                    "additionalProperties": False,
                },
                annotations=types.ToolAnnotations(
                    readOnlyHint=False,
                    destructiveHint=True,
                    idempotentHint=False,
                    openWorldHint=False,
                ),
            ),
        ]

    async def call(self, tool_name: str, arguments: Mapping[str, Any]) -> Outcome:
        if tool_name == "search_api":
            return self._search(arguments)
        if tool_name == "describe_operation":
            return self._describe(arguments)
        if tool_name == "call_api":
            op_id = arguments.get("operation_id")
            if not isinstance(op_id, str):
                return Outcome(
                    problem(
                        422, "invalid-arguments", "Invalid arguments", "operation_id is required."
                    ),
                    None,
                )
            params = arguments.get("params") or {}
            return Outcome(await self.executor.call(op_id, params), op_id)
        return Outcome(
            problem(404, "unknown-tool", "Unknown tool", f"This server has no tool '{tool_name}'."),
            None,
        )

    def _search(self, arguments: Mapping[str, Any]) -> Outcome:
        query = arguments.get("query")
        limit = arguments.get("limit", SEARCH_DEFAULT_LIMIT)
        if not isinstance(query, str) or not query.strip():
            return Outcome(
                problem(
                    422,
                    "invalid-arguments",
                    "Invalid arguments",
                    "query must be a non-empty string.",
                ),
                None,
            )
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= SEARCH_MAX_LIMIT
        ):
            return Outcome(
                problem(
                    422,
                    "invalid-arguments",
                    "Invalid arguments",
                    f"limit must be an integer from 1 to {SEARCH_MAX_LIMIT}.",
                ),
                None,
            )
        data = {"query": query, "results": self.search(query, limit)}
        return Outcome(ExecResult(True, 200, data), None)

    def _describe(self, arguments: Mapping[str, Any]) -> Outcome:
        op_id = arguments.get("operation_id")
        op = self.spec.get(op_id) if isinstance(op_id, str) else None
        if op is None:
            wanted = str(op_id or "")
            guesses = difflib.get_close_matches(wanted, list(self.spec.operations), n=3, cutoff=0.7)
            guesses = guesses or [h.operation_id for h in self.index.search(wanted, 3)]
            return Outcome(
                problem(
                    404,
                    "unknown-operation",
                    "Unknown operation",
                    f"There is no operation '{op_id}'. Use search_api to find one."
                    + (f" Similar: {guesses}." if guesses else ""),
                ),
                None,
            )
        data = {
            "operation_id": op.operation_id,
            "method": op.method,
            "path": op.path,
            "summary": op.summary,
            "description": op.description,
            "required_scopes": list(op.scopes),
            "input_schema": op.input_schema,
        }
        return Outcome(ExecResult(True, 200, data), op.operation_id)


def make_variant(
    name: str, spec: Spec, executor: Executor, tool_count: int | None = None
) -> Variant:
    if name == "per-endpoint":
        return PerEndpoint(spec, executor, tool_count)
    if tool_count is not None:
        raise ValueError("--tool-count only applies to the per-endpoint variant.")
    if name == "toolsets":
        return Toolsets(spec, executor)
    if name == "search-call":
        return SearchCall(spec, executor)
    raise ValueError(f"Unknown variant '{name}'. Choose one of {', '.join(VARIANTS)}.")
