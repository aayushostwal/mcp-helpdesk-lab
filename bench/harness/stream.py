"""Parse the ``--output-format stream-json`` output of one headless Claude Code run.

The stream is one JSON object per line. We use four kinds of event:

* ``system``/``init``: the tools, MCP servers, skills and plugins the process actually had, the
  CLI version and the resolved model id.
* ``assistant``: model output. Content blocks of type ``tool_use`` are tool calls. The first
  assistant event's ``usage`` is the first API request of the run, which is how the context cost
  of the tool definitions is isolated (``first_request_input_tokens``).
* ``user``: tool results (``is_error`` says whether the MCP tool reported an error).
* ``result``: totals (tokens, cost, durations, turns) and the final answer text.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from helpdesk_mcp.toolsets import TOOLSETS

MCP_PREFIX = "mcp__helpdesk__"
META_TOOLS = {"search_api", "describe_operation", "ToolSearch"}
_OP_FOR_ACTION = {
    (tool, action): op for tool, acts in TOOLSETS.items() for action, op in acts.items()
}


@dataclass
class ToolCall:
    tool: str
    operation_id: str | None
    ok: bool | None
    ms: int | None
    meta: bool = False  # search_api, describe_operation, ToolSearch: not an API operation

    def as_dict(self) -> dict[str, Any]:
        return {"tool": self.tool, "operation_id": self.operation_id, "ok": self.ok, "ms": self.ms}


@dataclass
class ParsedRun:
    has_init: bool = False
    has_result: bool = False
    tools: list[str] = field(default_factory=list)
    mcp_servers: list[dict[str, Any]] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    plugins: list[str] = field(default_factory=list)
    claude_version: str | None = None
    model_resolved: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    final_text: str = ""
    is_error: bool = False
    result_subtype: str | None = None
    api_error_status: int | None = None
    rate_limited: bool = False
    num_turns: int | None = None
    wall_ms: int | None = None
    api_ms: int | None = None
    ttft_ms: int | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    first_request_input_tokens: int | None = None
    total_cost_usd: float | None = None
    stop_reason: str | None = None
    terminal_reason: str | None = None

    @property
    def input_tokens_total(self) -> int:
        return self.input_tokens + self.cache_read_tokens + self.cache_creation_tokens


def operation_for(tool: str, args: dict[str, Any]) -> tuple[str | None, bool]:
    """Map a tool call to ``(operation_id, is_meta)``.

    ``per-endpoint``: the tool name is the operation id. ``toolsets``: ``(tool, action)`` maps to
    an operation. ``search-call``: ``call_api`` carries ``operation_id``; the search and describe
    tools are meta calls. Anything unknown gives ``(None, False)``.
    """
    name = tool.removeprefix(MCP_PREFIX)
    if name in META_TOOLS:
        return None, True
    if name == "call_api":
        op = args.get("operation_id")
        return (op if isinstance(op, str) else None), False
    if name in TOOLSETS:
        action = args.get("action")
        return _OP_FOR_ACTION.get((name, action)) if isinstance(action, str) else None, False
    return name, False


def _ts(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def read_events(lines: Iterable[str]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue  # a truncated last line after a kill
        if isinstance(obj, dict):
            events.append(obj)
    return events


def parse_stream(lines: Iterable[str]) -> ParsedRun:
    return parse_events(read_events(lines))


def parse_events(events: list[dict[str, Any]]) -> ParsedRun:
    run = ParsedRun()
    pending: dict[str, tuple[ToolCall, float | None]] = {}
    for ev in events:
        kind = ev.get("type")
        if kind == "system" and ev.get("subtype") == "init":
            run.has_init = True
            run.tools = list(ev.get("tools", []))
            run.mcp_servers = list(ev.get("mcp_servers", []))
            run.skills = list(ev.get("skills", []))
            run.plugins = [str(p.get("name")) for p in ev.get("plugins", []) if isinstance(p, dict)]
            run.claude_version = ev.get("claude_code_version")
            run.model_resolved = ev.get("model")
        elif kind == "assistant":
            _on_assistant(run, ev, pending)
        elif kind == "user":
            _on_user(ev, pending)
        elif kind == "rate_limit_event":
            info = ev.get("rate_limit_info") or {}
            if info.get("status") not in (None, "allowed", "allowed_warning"):
                run.rate_limited = True
        elif kind == "result":
            _on_result(run, ev)
    return run


def _on_assistant(
    run: ParsedRun, ev: dict[str, Any], pending: dict[str, tuple[ToolCall, float | None]]
) -> None:
    message = ev.get("message") or {}
    if run.first_request_input_tokens is None:
        usage = message.get("usage") or {}
        run.first_request_input_tokens = (
            int(usage.get("input_tokens", 0))
            + int(usage.get("cache_creation_input_tokens", 0))
            + int(usage.get("cache_read_input_tokens", 0))
        )
    if message.get("model") and not run.model_resolved:
        run.model_resolved = message["model"]
    stamp = _ts(ev.get("timestamp"))
    for block in message.get("content") or []:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        tool = str(block.get("name", ""))
        raw_args = block.get("input")
        args: dict[str, Any] = raw_args if isinstance(raw_args, dict) else {}
        op, meta = operation_for(tool, args)
        call = ToolCall(tool.removeprefix(MCP_PREFIX), op, None, None, meta)
        run.tool_calls.append(call)
        pending[str(block.get("id"))] = (call, stamp)


def _on_user(ev: dict[str, Any], pending: dict[str, tuple[ToolCall, float | None]]) -> None:
    stamp = _ts(ev.get("timestamp"))
    content = (ev.get("message") or {}).get("content")
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        entry = pending.pop(str(block.get("tool_use_id")), None)
        if entry is None:
            continue
        call, started = entry
        call.ok = not bool(block.get("is_error"))
        if started is not None and stamp is not None:
            call.ms = max(0, round((stamp - started) * 1000))


def _on_result(run: ParsedRun, ev: dict[str, Any]) -> None:
    run.has_result = True
    run.is_error = bool(ev.get("is_error"))
    run.result_subtype = ev.get("subtype")
    run.final_text = str(ev.get("result") or "")
    status = ev.get("api_error_status")
    run.api_error_status = int(status) if isinstance(status, int) else None
    run.num_turns = ev.get("num_turns")
    run.wall_ms = ev.get("duration_ms")
    run.api_ms = ev.get("duration_api_ms")
    run.ttft_ms = ev.get("ttft_ms")
    run.total_cost_usd = ev.get("total_cost_usd")
    run.stop_reason = ev.get("stop_reason")
    run.terminal_reason = ev.get("terminal_reason")
    usage = ev.get("usage") or {}
    run.input_tokens = int(usage.get("input_tokens", 0))
    run.output_tokens = int(usage.get("output_tokens", 0))
    run.cache_read_tokens = int(usage.get("cache_read_input_tokens", 0))
    run.cache_creation_tokens = int(usage.get("cache_creation_input_tokens", 0))
    resolved = ev.get("modelUsage")
    if isinstance(resolved, dict) and resolved:
        run.model_resolved = next(iter(resolved))


_RATE_LIMIT_TEXT = re.compile(r"rate.?limit|429|overloaded|usage limit|too many requests", re.I)


def looks_rate_limited(run: ParsedRun, stderr: str = "") -> bool:
    if run.rate_limited or run.api_error_status in (429, 529):
        return True
    return bool(run.is_error and _RATE_LIMIT_TEXT.search(run.final_text)) or bool(
        not run.has_result and _RATE_LIMIT_TEXT.search(stderr)
    )
