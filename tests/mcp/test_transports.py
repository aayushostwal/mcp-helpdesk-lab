"""stdio and streamable HTTP, against a real subprocess and a real socket."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import uvicorn
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from helpdesk_mcp.server import build_server
from helpdesk_mcp.settings import McpSettings
from helpdesk_mcp.spec import Spec
from mcp import ClientSession, StdioServerParameters, types
from tests.mcp.conftest import result_json

SECRET = "hd_testkey_supersecretvalue"
ENV = {
    **os.environ,
    "HELPDESK_MCP_API_URL": "http://127.0.0.1:9",  # nothing listens here: calls fail fast
    "HELPDESK_MCP_API_KEY": SECRET,
    "HELPDESK_MCP_RETRIES": "0",
    "HELPDESK_MCP_TIMEOUT_SECONDS": "2",
}
COMMAND = [sys.executable, "-m", "helpdesk_mcp"]


def rpc(id_: int | None, method: str, params: dict[str, object] | None = None) -> str:
    message: dict[str, object] = {"jsonrpc": "2.0", "method": method}
    if id_ is not None:
        message["id"] = id_
    if params is not None:
        message["params"] = params
    return json.dumps(message) + "\n"


def test_stdout_carries_only_protocol_messages() -> None:
    """Every line on stdout is a JSON-RPC message; logs and the key never appear there."""
    script = "".join(
        [
            rpc(1, "initialize", {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "t", "version": "0"},
            }),
            rpc(None, "notifications/initialized"),
            rpc(2, "tools/list"),
            rpc(3, "tools/call", {"name": "search_api", "arguments": {"query": "refund"}}),
            rpc(4, "tools/call", {"name": "call_api", "arguments": {
                "operation_id": "tags_list"}}),
            rpc(5, "resources/list"),
        ]
    )  # fmt: skip
    proc = subprocess.Popen(  # noqa: S603
        [*COMMAND, "--variant", "search-call"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=ENV,
    )
    watchdog = threading.Timer(60, proc.kill)
    watchdog.start()
    try:
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(script)
        proc.stdin.flush()
        lines: list[str] = []
        while not any(json.loads(line).get("id") == 5 for line in lines):
            line = proc.stdout.readline()
            if not line:
                break
            lines.append(line)
        rest, stderr = proc.communicate(timeout=30)
        lines.extend(rest.splitlines())
    finally:
        watchdog.cancel()
        proc.kill()
    stdout = "".join(lines)
    assert lines, stderr
    messages = [
        json.loads(line) for line in lines if line.strip()
    ]  # raises if anything else is on stdout
    assert all(m["jsonrpc"] == "2.0" for m in messages)
    by_id = {m["id"]: m for m in messages}
    assert [t["name"] for t in by_id[2]["result"]["tools"]] == [
        "search_api", "describe_operation", "call_api",
    ]  # fmt: skip
    assert by_id[3]["result"]["isError"] is False
    assert by_id[4]["result"]["isError"] is True  # API unreachable, reported not raised
    assert "upstream-unreachable" in by_id[4]["result"]["content"][0]["text"]
    assert SECRET not in stdout and SECRET not in stderr

    log_lines = [json.loads(line) for line in stderr.splitlines() if line.startswith("{")]
    calls = [entry for entry in log_lines if entry["event"] == "tool_call"]
    assert [c["tool"] for c in calls] == ["search_api", "call_api"]
    assert calls[1]["operation_id"] == "tags_list" and calls[1]["status"] == 502
    assert calls[1]["error_type"] == "upstream-unreachable"
    assert {"duration_ms", "tool", "operation_id", "status", "error_type"} <= set(calls[1])


async def test_stdio_client_lists_and_calls() -> None:
    params = StdioServerParameters(
        command=COMMAND[0], args=[*COMMAND[1:], "--variant", "toolsets"], env=ENV
    )
    errlog = Path(os.devnull).open("w")  # noqa: ASYNC230
    try:
        async with (
            stdio_client(params, errlog=errlog) as (read, write),
            ClientSession(read, write) as session,
        ):
            init = await session.initialize()
            assert init.instructions and "Helpdesk" in init.instructions
            assert len((await session.list_tools()).tools) == 8
            result = await session.call_tool(
                "tickets", {"action": "get", "params": {"ticket_id": "T-1"}}
            )
            assert result.isError
    finally:
        errlog.close()


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


@asynccontextmanager
async def serve_http(spec: Spec) -> AsyncIterator[str]:
    port = free_port()
    settings = McpSettings(api_url="http://127.0.0.1:9", port=port, retries=0, timeout_seconds=2)
    bundle = build_server(settings, variant="search-call", spec=spec)
    config = uvicorn.Config(
        bundle.mcp.streamable_http_app(), host="127.0.0.1", port=port, log_level="warning"
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.05)
        assert server.started
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        server.should_exit = True
        await task
        await bundle.aclose()


async def test_streamable_http_transport(spec: Spec) -> None:
    async with (
        serve_http(spec) as url,
        streamable_http_client(url) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        init = await session.initialize()
        assert init.serverInfo.name == "helpdesk"
        tools = (await session.list_tools()).tools
        assert [t.name for t in tools] == ["search_api", "describe_operation", "call_api"]
        found = await session.call_tool("search_api", {"query": "close a ticket"})
        assert result_json(found)["results"][0]["operation_id"] == "tickets_close"
        resource = await session.read_resource("helpdesk://policies/refunds")  # type: ignore[arg-type]
        assert isinstance(resource.contents[0], types.TextResourceContents)


@pytest.mark.parametrize(
    "args", [["--variant", "nope"], ["--variant", "toolsets", "--tool-count", "5"]]
)
def test_bad_options_exit_with_a_message(args: list[str]) -> None:
    proc = subprocess.run(  # noqa: S603
        [*COMMAND, *args], capture_output=True, text=True, env=ENV, timeout=60, check=False
    )
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "error:" in proc.stderr
