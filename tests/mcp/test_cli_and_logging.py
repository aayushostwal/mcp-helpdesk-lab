from __future__ import annotations

import json
from typing import Any

import pytest
from typer.testing import CliRunner

from helpdesk_mcp import cli
from helpdesk_mcp.logging import configure_logging
from tests.conftest import create_ticket
from tests.mcp.conftest import Connect, invoke

runner = CliRunner()


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any]]:
    """Replace the run loops so the CLI can be exercised without serving."""
    calls: list[tuple[str, Any]] = []

    async def fake_stdio(self: Any) -> None:
        calls.append(("stdio", self))

    async def fake_http(self: Any) -> None:
        calls.append(("http", self))

    monkeypatch.setattr("mcp.server.fastmcp.FastMCP.run_stdio_async", fake_stdio)
    monkeypatch.setattr("mcp.server.fastmcp.FastMCP.run_streamable_http_async", fake_http)
    monkeypatch.setenv("HELPDESK_MCP_API_KEY", "hd_k_s")
    return calls


def test_default_run_is_stdio(started: list[tuple[str, Any]]) -> None:
    result = runner.invoke(cli.app, [])
    assert result.exit_code == 0, result.output
    assert [kind for kind, _ in started] == ["stdio"]


def test_http_transport_and_flags(started: list[tuple[str, Any]]) -> None:
    result = runner.invoke(
        cli.app,
        ["--transport", "streamable-http", "--variant", "per-endpoint", "--tool-count", "10",
         "--no-instructions", "--no-resources", "--no-prompts"],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert [kind for kind, _ in started] == ["http"]
    mcp = started[0][1]
    assert mcp.instructions is None


def test_missing_key_warns_on_stderr(
    started: list[tuple[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HELPDESK_MCP_API_KEY")
    result = runner.invoke(cli.app, ["--variant", "search-call"])
    assert result.exit_code == 0
    assert "HELPDESK_MCP_API_KEY is not set" in result.output


@pytest.mark.parametrize(
    "args",
    [["--transport", "carrier-pigeon"], ["--variant", "x"], ["--tool-count", "0"],
     ["--variant", "search-call", "--tool-count", "3"]],
)  # fmt: skip
def test_invalid_options(started: list[tuple[str, Any]], args: list[str]) -> None:
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 2
    assert not started


async def test_one_log_line_per_tool_call(
    connect: Connect,
    client: Any,
    admin: dict[str, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    ticket = await create_ticket(client, admin)
    configure_logging("INFO", json_logs=True)
    capsys.readouterr()
    async with connect("per-endpoint") as (session, _):
        await invoke(session, "per-endpoint", "tickets_get", {"ticket_id": ticket["id"]})
        await invoke(session, "per-endpoint", "tickets_get", {"ticket_id": "T-9999"})
    captured = capsys.readouterr()
    assert captured.out == ""  # nothing on stdout
    entries = [json.loads(line) for line in captured.err.splitlines() if line.startswith("{")]
    calls = [e for e in entries if e["event"] == "tool_call"]
    assert len(calls) == 2
    ok, failed = calls
    assert (ok["tool"], ok["operation_id"], ok["status"], ok["ok"]) == (
        "tickets_get", "tickets_get", 200, True,
    )  # fmt: skip
    assert ok["error_type"] is None and ok["duration_ms"] >= 0
    assert (failed["status"], failed["ok"], failed["error_type"]) == (404, False, "not-found")
    # no secrets, no response bodies, no arguments
    assert "Printer on fire" not in captured.err
    assert "Bearer" not in captured.err and "hd_" not in captured.err


def test_console_log_format(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("DEBUG", json_logs=False)
    from helpdesk_mcp.logging import get_logger

    get_logger().info("hello", tool="x")
    captured = capsys.readouterr()
    assert captured.out == "" and "hello" in captured.err
    configure_logging("INFO", json_logs=True)
