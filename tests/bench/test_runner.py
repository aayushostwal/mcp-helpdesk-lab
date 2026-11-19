from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from bench.harness import runner
from bench.harness.runner import (
    DATE_NOTE,
    RunConfig,
    RunSpec,
    analyse_calls,
    build_command,
    build_env,
    classify,
    execute_run,
    isolation_problems,
    run_key,
    run_process,
)
from bench.harness.stream import ParsedRun, ToolCall, parse_stream
from bench.harness.tasks import load_tasks
from bench.harness.variants import ALL_VARIANTS, VARIANTS

from tests.bench.conftest import stream_lines

TASK = {t.id: t for t in load_tasks()}["easy-01-close-ticket"]


def test_command_uses_the_verified_flags() -> None:
    spec = RunSpec(VARIANTS["per-endpoint-100"], "haiku", TASK, 1)
    cmd = build_command(spec, RunConfig(), Path("/x/mcp.json"))
    assert cmd[:3] == ["claude", "-p", TASK.prompt]
    pairs = dict(zip(cmd[3::2], cmd[4::2], strict=False))
    assert pairs["--model"] == "haiku"
    assert pairs["--output-format"] == "stream-json"
    assert pairs["--mcp-config"] == "/x/mcp.json"
    assert pairs["--tools"] == ""
    assert pairs["--permission-mode"] == "bypassPermissions"
    assert pairs["--setting-sources"] == ""
    assert pairs["--max-turns"] == "30" and pairs["--max-budget-usd"] == "0.50"
    assert pairs["--append-system-prompt"] == DATE_NOTE
    for flag in (
        "--verbose",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--disable-slash-commands",
    ):
        assert flag in cmd
    assert "--bare" not in cmd and "--allowedTools" not in cmd


def test_client_tool_search_variant_enables_the_tool() -> None:
    spec = RunSpec(VARIANTS["per-endpoint-100+client-tool-search"], "sonnet", None, 1)
    cmd = build_command(spec, RunConfig(), Path("m.json"))
    assert cmd[cmd.index("--tools") + 1] == "ToolSearch"
    assert cmd[cmd.index("--max-budget-usd") + 1] == "1.50"
    assert build_env(spec, RunConfig())["ENABLE_TOOL_SEARCH"] == "true"
    main = RunSpec(VARIANTS["toolsets"], "haiku", TASK, 1)
    assert build_env(main, RunConfig())["ENABLE_TOOL_SEARCH"] == "false"


def test_env_drops_helpdesk_settings_and_marks_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HELPDESK_DATABASE_URL", "x")
    monkeypatch.setenv("DATABASE_URL", "y")
    spec = RunSpec(VARIANTS["toolsets"], "haiku", TASK, 1)
    env = build_env(spec, RunConfig(dry_run=True, fake_env={"BENCH_FAKE_MODE": "oracle"}))
    assert "HELPDESK_DATABASE_URL" not in env and "DATABASE_URL" not in env
    assert env["BENCH_FAKE_TASK"] == TASK.id and env["BENCH_FAKE_MODE"] == "oracle"
    assert "BENCH_FAKE_TASK" not in build_env(spec, RunConfig())


def test_budget_default_for_unknown_model() -> None:
    assert RunConfig().budget_for("opus") == 1.50


def test_run_key() -> None:
    assert RunSpec(VARIANTS["toolsets"], "haiku", TASK, 2).key == run_key(
        "toolsets", "haiku", TASK.id, 2
    )
    assert RunSpec(ALL_VARIANTS["none"], "haiku", None, 1).key == "none|haiku|baseline|1"


def test_classify_statuses() -> None:
    ok = parse_stream(stream_lines("baseline_no_server.jsonl"))
    assert classify(ok, 0, False, "") == "ok"
    assert classify(ok, 0, True, "") == "timeout"
    assert classify(ParsedRun(), 1, False, "boom") == "crash"
    assert classify(ParsedRun(), 1, False, "HTTP 429") == "rate_limited"
    err = ParsedRun(has_result=True, is_error=True, result_subtype="error_max_turns")
    assert classify(err, 1, False, "") == "max_turns"
    err.result_subtype = "error_max_budget_usd"
    assert classify(err, 1, False, "") == "budget_exceeded"
    err.result_subtype = "error_during_execution"
    assert classify(err, 1, False, "") == "error"


def test_isolation_problems_detect_leaks() -> None:
    clean = parse_stream(stream_lines("search_call_close_ticket.jsonl"))
    assert isolation_problems(clean, VARIANTS["search-call"]) == []
    leaky = ParsedRun(
        has_init=True,
        tools=["Bash", "mcp__helpdesk__x"],
        skills=["s"],
        mcp_servers=[{"name": "other", "status": "failed"}],
    )
    problems = " ".join(isolation_problems(leaky, VARIANTS["search-call"]))
    assert (
        "Bash" in problems
        and "other" in problems
        and "skills" in problems
        and "connected" in problems
    )
    ts = ParsedRun(
        has_init=True,
        tools=["ToolSearch"],
        mcp_servers=[{"name": "helpdesk", "status": "connected"}],
    )
    assert isolation_problems(ts, VARIANTS["per-endpoint-100+client-tool-search"]) == []
    assert isolation_problems(
        ts, VARIANTS["per-endpoint-100"]
    )  # ToolSearch is a leak in the main matrix


def test_analyse_calls_separates_wrong_writes_from_wrong_reads() -> None:
    calls = [
        ToolCall("tickets_get", "tickets_get", True, 1),  # also ok
        ToolCall("tickets_close", "tickets_close", True, 1),  # needed
        ToolCall("customers_list", "customers_list", True, 1),  # wrong read
        ToolCall("orders_cancel", "orders_cancel", False, 1),  # wrong write, failed
        ToolCall("search_api", None, True, 1, meta=True),
        ToolCall("bogus", "bogus_op", False, 1),  # unknown operation
        ToolCall("tickets", None, True, 1),  # action that maps to nothing
    ]
    out = analyse_calls(ParsedRun(tool_calls=calls), TASK)
    assert (
        out["wrong_write_calls"] == 1 and out["wrong_read_calls"] == 1 and out["wrong_calls"] == 2
    )
    assert out["unknown_calls"] == 2 and out["meta_calls"] == 1 and out["failed_calls"] == 2
    assert out["needed_operations_called"] == ["tickets_close"]
    assert analyse_calls(ParsedRun(tool_calls=calls), None)["wrong_calls"] == 0


def test_run_process_kills_the_whole_group_on_timeout(tmp_path: Path) -> None:
    marker = tmp_path / "child.pid"
    script = (
        "import os, subprocess, sys, time\n"
        f"p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(marker)!r}, 'w').write(str(p.pid)); print('started', flush=True); time.sleep(60)\n"
    )
    start = time.monotonic()
    out, _, _, timed_out = run_process(
        [sys.executable, "-c", script], tmp_path, dict(os.environ), 2.0
    )
    assert timed_out and "started" in out and time.monotonic() - start < 20
    pid = int(marker.read_text())
    time.sleep(0.3)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_run_process_normal_exit(tmp_path: Path) -> None:
    out, err, rc, timed_out = run_process(
        [sys.executable, "-c", "import sys; print('hi'); print('e', file=sys.stderr)"],
        tmp_path,
        dict(os.environ),
        20,
    )
    assert (out.strip(), err.strip(), rc, timed_out) == ("hi", "e", 0, False)


def test_retries_only_infrastructure_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = RunSpec(VARIANTS["toolsets"], "haiku", TASK, 1)
    sleeps: list[float] = []
    cfg = RunConfig(retries=2, backoff_s=1.0, sleep=sleeps.append)

    def fake(statuses: list[str]) -> Any:
        it = iter(statuses)
        return lambda *_a, **_k: {"status": next(it), "error": "e", "pass": None}

    monkeypatch.setattr(runner, "attempt", fake(["rate_limited", "crash", "ok"]))
    row = execute_run(spec, cfg)
    assert row["status"] == "ok" and row["retries"] == 2 and len(row["retry_log"]) == 2
    assert sleeps == [3.0, 2.0]  # rate limits back off three times longer
    monkeypatch.setattr(runner, "attempt", fake(["crash", "crash", "crash"]))
    assert execute_run(spec, cfg)["status"] == "crash"
    monkeypatch.setattr(runner, "attempt", fake(["ok"]))
    wrong = {"status": "ok", "pass": False}
    monkeypatch.setattr(runner, "attempt", lambda *_a, **_k: wrong)
    assert execute_run(spec, cfg)["retries"] == 0  # a wrong answer is never retried
    monkeypatch.setattr(runner, "attempt", fake(["timeout"]))
    assert execute_run(spec, cfg)["retries"] == 0  # nor is a timeout


def test_git_info_is_cached() -> None:
    assert runner.git_info() == runner.git_info()
