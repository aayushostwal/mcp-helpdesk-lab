from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from helpdesk_mcp.executor import Executor
from helpdesk_mcp.settings import McpSettings
from helpdesk_mcp.spec import Spec
from helpdesk_mcp.toolsets import TOOLSET_INTRO, TOOLSETS
from helpdesk_mcp.variants import SearchCall, Toolsets, make_variant
from tests.mcp.conftest import Connect, result_json


def recording_executor(spec: Spec, seen: list[httpx.Request]) -> Executor:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://api.test")
    return Executor(spec, McpSettings(api_url="http://api.test"), client)


@pytest.mark.parametrize(
    ("count", "expected"), [(None, 102), (10, 10), (25, 25), (50, 50), (100, 100)]
)
async def test_per_endpoint_tool_counts(connect: Connect, count: int | None, expected: int) -> None:
    async with connect("per-endpoint", count) as (session, _):
        tools = (await session.list_tools()).tools
    assert len(tools) == expected
    assert len({t.name for t in tools}) == expected


async def test_per_endpoint_tools_mirror_the_openapi_file(connect: Connect, spec: Spec) -> None:
    async with connect("per-endpoint") as (session, _):
        tools = {t.name: t for t in (await session.list_tools()).tools}
    assert set(tools) == set(spec.operations)
    for op_id, op in spec.operations.items():
        tool = tools[op_id]
        assert tool.description == op.tool_description
        assert tool.inputSchema == op.input_schema


async def test_annotations_follow_the_http_method(connect: Connect, spec: Spec) -> None:
    async with connect("per-endpoint") as (session, _):
        tools = {t.name: t for t in (await session.list_tools()).tools}
    for op_id, op in spec.operations.items():
        hints = tools[op_id].annotations
        assert hints is not None
        assert hints.readOnlyHint == (op.method == "GET"), op_id
        assert hints.destructiveHint == (op.method == "DELETE"), op_id
        assert hints.idempotentHint == (op.method != "POST"), op_id
        assert hints.openWorldHint is False
    assert tools["tickets_get"].annotations.readOnlyHint is True  # type: ignore[union-attr]
    assert tools["refunds_create"].annotations.idempotentHint is False  # type: ignore[union-attr]
    assert tools["tags_delete"].annotations.destructiveHint is True  # type: ignore[union-attr]


async def test_toolsets_list(connect: Connect) -> None:
    async with connect("toolsets") as (session, _):
        tools = {t.name: t for t in (await session.list_tools()).tools}
    assert list(tools) == [
        "tickets", "customers", "orders_refunds", "knowledge",
        "team", "sla_reports", "admin", "webhooks",
    ]  # fmt: skip
    for name, tool in tools.items():
        enum = tool.inputSchema["properties"]["action"]["enum"]
        assert enum == list(TOOLSETS[name])
        assert tool.inputSchema["required"] == ["action"]
        assert TOOLSET_INTRO[name] in (tool.description or "")
        for action in enum:
            assert f"- {action}:" in (tool.description or "")
    assert "(params: ticket_id*, note)" in (tools["tickets"].description or "")
    assert tools["customers"].annotations.readOnlyHint is False  # type: ignore[union-attr]
    assert tools["customers"].annotations.destructiveHint is False  # type: ignore[union-attr]
    assert tools["tickets"].annotations.destructiveHint is True  # type: ignore[union-attr]


def test_toolsets_cover_every_operation_exactly_once(spec: Spec) -> None:
    mapped = [op for actions in TOOLSETS.values() for op in actions.values()]
    assert len(mapped) == len(set(mapped)) == 102
    assert set(mapped) == set(spec.operations)
    assert set(TOOLSET_INTRO) == set(TOOLSETS)
    assert len(TOOLSETS) == 8


async def test_every_operation_is_reachable_through_toolsets(spec: Spec) -> None:
    """Call each action once and check it reaches the right method and path."""
    seen: list[httpx.Request] = []
    executor = recording_executor(spec, seen)
    variant = Toolsets(spec, executor)
    for tool, actions in TOOLSETS.items():
        for action, op_id in actions.items():
            op = spec.operations[op_id]
            params = {p.name: "X1" for p in op.parameters if p.location == "path"}
            before = len(seen)
            outcome = await variant.call(tool, {"action": action, "params": params})
            assert outcome.result.ok, (tool, action, outcome.result.data)
            assert outcome.operation_id == op_id
            assert len(seen) == before + 1
            request = seen[-1]
            assert request.method == op.method
            assert request.url.path == op.path.format(**params)
    assert len(seen) == 102
    await executor.aclose()


async def test_every_operation_is_reachable_through_call_api(spec: Spec) -> None:
    seen: list[httpx.Request] = []
    executor = recording_executor(spec, seen)
    variant = SearchCall(spec, executor)
    for op_id, op in spec.operations.items():
        params = {p.name: "X1" for p in op.parameters if p.location == "path"}
        outcome = await variant.call("call_api", {"operation_id": op_id, "params": params})
        assert outcome.result.ok
        assert seen[-1].method == op.method and seen[-1].url.path == op.path.format(**params)
    assert len(seen) == 102
    await executor.aclose()


async def test_toolset_argument_errors(spec: Spec) -> None:
    seen: list[httpx.Request] = []
    executor = recording_executor(spec, seen)
    variant = Toolsets(spec, executor)
    bad_action = await variant.call("tickets", {"action": "explode"})
    assert bad_action.result.status == 422 and "close" in bad_action.result.data["allowed"]
    assert (await variant.call("tickets", {})).result.status == 422
    extra = await variant.call("tickets", {"action": "list", "status": "open"})
    assert extra.result.status == 422 and "status" in extra.result.data["detail"]
    not_object = await variant.call("tickets", {"action": "list", "params": ["x"]})
    assert not_object.result.status == 422
    unknown = await variant.call("nope", {"action": "list"})
    assert unknown.result.status == 404 and unknown.result.error_type == "unknown-tool"
    assert not seen
    await executor.aclose()


async def test_toolset_rejects_a_mapping_to_a_missing_operation(
    spec: Spec, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(TOOLSETS["webhooks"], "bogus", "no_such_operation")
    seen: list[httpx.Request] = []
    executor = recording_executor(spec, seen)
    with pytest.raises(ValueError, match="no_such_operation"):
        Toolsets(spec, executor)
    await executor.aclose()


async def test_search_call_tools(connect: Connect) -> None:
    async with connect("search-call") as (session, _):
        tools = {t.name: t for t in (await session.list_tools()).tools}
        assert list(tools) == ["search_api", "describe_operation", "call_api"]
        assert tools["search_api"].annotations.readOnlyHint is True  # type: ignore[union-attr]
        assert tools["call_api"].annotations.readOnlyHint is False  # type: ignore[union-attr]

        found = result_json(await session.call_tool("search_api", {"query": "close a ticket"}))
        assert found["results"][0]["operation_id"] == "tickets_close"
        assert len(found["results"]) == 5
        first = found["results"][0]
        assert set(first) == {"operation_id", "summary", "method", "path", "required", "score"}
        assert first["required"] == ["ticket_id"]

        two = result_json(await session.call_tool("search_api", {"query": "refund", "limit": 2}))
        assert len(two["results"]) == 2

        described = result_json(
            await session.call_tool("describe_operation", {"operation_id": "refunds_create"})
        )
        assert described["method"] == "POST" and described["required_scopes"] == ["refunds:write"]
        assert described["input_schema"]["required"] == ["order_id", "amount", "reason"]


async def test_search_call_errors(connect: Connect) -> None:
    async with connect("search-call") as (session, _):
        cases: list[dict[str, Any]] = [
            {"query": ""},
            {"query": "x", "limit": 0},
            {"query": "x", "limit": True},
            {},
        ]
        for args in cases:
            result = await session.call_tool("search_api", args)
            assert result.isError and result_json(result)["status"] == 422
        unknown = await session.call_tool("describe_operation", {"operation_id": "tickets_clos"})
        assert unknown.isError
        assert "tickets_close" in result_json(unknown)["detail"]
        no_guess = await session.call_tool("describe_operation", {"operation_id": "zzzz"})
        assert "Similar" not in result_json(no_guess)["detail"]
        missing = await session.call_tool("call_api", {})
        assert missing.isError and result_json(missing)["status"] == 422
        unknown_op = await session.call_tool("call_api", {"operation_id": "nope"})
        assert result_json(unknown_op)["type"].endswith("unknown-operation")
        nonsense = await session.call_tool("nope", {})
        assert nonsense.isError and result_json(nonsense)["type"].endswith("unknown-tool")


async def test_per_endpoint_unknown_and_unlisted_tools(connect: Connect) -> None:
    async with connect("per-endpoint", 10) as (session, _):
        unlisted = await session.call_tool("webhooks_create", {})
        assert unlisted.isError and result_json(unlisted)["type"].endswith("unknown-tool")


def test_make_variant_validation(spec: Spec) -> None:
    executor = Executor(spec, McpSettings())
    with pytest.raises(ValueError, match="only applies"):
        make_variant("toolsets", spec, executor, 10)
    with pytest.raises(ValueError, match="Unknown variant"):
        make_variant("mystery", spec, executor)
    assert make_variant("per-endpoint", spec, executor, 10).name == "per-endpoint"


def test_tool_names_are_valid_mcp_names(spec: Spec) -> None:
    import re

    pattern = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
    names = [*spec.operations, *TOOLSETS, "search_api", "describe_operation", "call_api"]
    assert all(pattern.match(n) for n in names)


def test_json_schemas_survive_a_round_trip(spec: Spec) -> None:
    for op in spec.operations.values():
        assert json.loads(json.dumps(op.input_schema)) == op.input_schema
