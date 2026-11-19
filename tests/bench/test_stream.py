from __future__ import annotations

import json

from bench.harness.stream import (
    looks_rate_limited,
    operation_for,
    parse_events,
    parse_stream,
    read_events,
)

from tests.bench.conftest import stream_lines


def test_parse_search_call_run() -> None:
    run = parse_stream(stream_lines("search_call_close_ticket.jsonl"))
    assert run.has_init and run.has_result
    assert run.claude_version
    assert sorted(run.tools) == [
        "mcp__helpdesk__call_api",
        "mcp__helpdesk__describe_operation",
        "mcp__helpdesk__search_api",
    ]
    assert run.skills == []
    assert [s["name"] for s in run.mcp_servers] == ["helpdesk"]
    assert run.model_resolved and run.model_resolved.startswith("claude-")
    tools = [c.tool for c in run.tool_calls]
    assert tools[0] == "search_api" and tools.count("call_api") >= 2
    assert run.tool_calls[0].meta is True
    ops = [c.operation_id for c in run.tool_calls if not c.meta]
    assert "tickets_close" in ops
    assert all(c.ok is not None for c in run.tool_calls)
    assert any(c.ms is not None for c in run.tool_calls)
    assert run.first_request_input_tokens and run.first_request_input_tokens > 1000
    assert run.input_tokens_total >= run.first_request_input_tokens
    assert run.total_cost_usd and run.total_cost_usd > 0
    assert run.num_turns and run.wall_ms and run.api_ms and run.ttft_ms
    assert "T-1042" in run.final_text
    assert not run.is_error and run.result_subtype == "success"


def test_failed_tool_call_is_recorded() -> None:
    events = [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "a",
                        "name": "mcp__helpdesk__orders_get",
                        "input": {},
                    }
                ]
            },
        },
        {
            "type": "user",
            "message": {"content": [{"type": "tool_result", "tool_use_id": "a", "is_error": True}]},
        },
    ]
    call = parse_events(events).tool_calls[0]
    assert call.ok is False and call.ms is None


def test_parse_toolsets_run_maps_actions_to_operations() -> None:
    run = parse_stream(stream_lines("toolsets_refund.jsonl"))
    ops = [c.operation_id for c in run.tool_calls]
    assert "tickets_get" in ops and "refunds_create" in ops
    assert {c.tool for c in run.tool_calls} <= {"tickets", "customers", "orders_refunds"}


def test_parse_baseline_without_server() -> None:
    run = parse_stream(stream_lines("baseline_no_server.jsonl"))
    assert run.tools == [] and run.mcp_servers == []
    assert run.tool_calls == []
    assert run.final_text.strip().lower().startswith("ok")
    assert run.first_request_input_tokens == run.input_tokens_total


def test_operation_for_each_variant() -> None:
    assert operation_for("mcp__helpdesk__tickets_close", {}) == ("tickets_close", False)
    assert operation_for("mcp__helpdesk__tickets", {"action": "close"}) == ("tickets_close", False)
    assert operation_for("mcp__helpdesk__tickets", {"action": "nope"}) == (None, False)
    assert operation_for("mcp__helpdesk__tickets", {}) == (None, False)
    assert operation_for("mcp__helpdesk__call_api", {"operation_id": "refunds_get"}) == (
        "refunds_get",
        False,
    )
    assert operation_for("mcp__helpdesk__call_api", {}) == (None, False)
    assert operation_for("mcp__helpdesk__search_api", {"query": "x"}) == (None, True)
    assert operation_for("ToolSearch", {}) == (None, True)


def test_truncated_and_blank_lines_are_ignored() -> None:
    lines = stream_lines("baseline_no_server.jsonl")
    events = read_events([*lines, "", '{"type": "assistant", "mess'])
    assert len(events) == len([ln for ln in lines if ln.strip()])
    assert read_events(["[1, 2]"]) == []


def test_run_without_result_event() -> None:
    lines = [ln for ln in stream_lines("baseline_no_server.jsonl") if '"type":"result"' not in ln]
    events = [e for e in read_events(lines) if e.get("type") != "result"]
    run = parse_events(events)
    assert run.has_init and not run.has_result


def test_rate_limit_detection() -> None:
    limited = parse_events(
        [{"type": "rate_limit_event", "rate_limit_info": {"status": "rejected"}}]
    )
    assert limited.rate_limited and looks_rate_limited(limited)
    ok = parse_events([{"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}}])
    assert not looks_rate_limited(ok)
    err = parse_events(
        [{"type": "result", "is_error": True, "api_error_status": 429, "result": "x"}]
    )
    assert looks_rate_limited(err)
    text = parse_events(
        [{"type": "result", "is_error": True, "result": "API Error: rate limit reached"}]
    )
    assert looks_rate_limited(text)
    assert looks_rate_limited(parse_events([]), "HTTP 429 too many requests")
    assert not looks_rate_limited(parse_events([]), "segfault")


def test_tool_use_timing_from_timestamps() -> None:
    events = [
        {
            "type": "assistant",
            "timestamp": "2026-01-01T00:00:00.000Z",
            "message": {
                "model": "claude-x",
                "usage": {
                    "input_tokens": 1,
                    "cache_read_input_tokens": 2,
                    "cache_creation_input_tokens": 3,
                },
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "mcp__helpdesk__tickets_get",
                        "input": {},
                    }
                ],
            },
        },
        {
            "type": "user",
            "timestamp": "2026-01-01T00:00:00.250Z",
            "message": {
                "content": [{"type": "tool_result", "tool_use_id": "t1", "is_error": False}]
            },
        },
        {"type": "user", "message": {"content": "plain text"}},
        {
            "type": "user",
            "message": {"content": [{"type": "tool_result", "tool_use_id": "unknown"}]},
        },
    ]
    run = parse_events(events)
    assert run.first_request_input_tokens == 6
    assert run.model_resolved == "claude-x"
    call = run.tool_calls[0]
    assert (call.operation_id, call.ok, call.ms) == ("tickets_get", True, 250)
    assert json.dumps(call.as_dict())
