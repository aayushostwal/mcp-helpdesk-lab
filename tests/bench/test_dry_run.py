"""End to end with the fake claude: real API and graders, no model."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from bench.harness import fake_claude
from bench.harness.cli import app
from bench.harness.orchestrate import Pause, existing_rows
from bench.harness.runner import RunConfig, RunSpec, execute_run
from bench.harness.store import read_rows
from bench.harness.tasks import load_tasks
from bench.harness.variants import ALL_VARIANTS, VARIANTS
from typer.testing import CliRunner

runner = CliRunner()
TASKS = {t.id: t for t in load_tasks()}


def _cfg(tmp_path: Path, seed_db: Path, mode: str, **kw: object) -> RunConfig:
    cfg = RunConfig(
        claude_cmd=[*__import__("bench.harness.runner", fromlist=["FAKE_CLAUDE"]).FAKE_CLAUDE],
        dry_run=True,
        raw_dir=tmp_path / "raw",
        seed_db=seed_db,
        retries=1,
        backoff_s=0.0,
        sleep=lambda _s: None,
        fake_env={"BENCH_FAKE_MODE": mode},
    )
    for k, v in kw.items():
        setattr(cfg, k, v)
    return cfg


def test_cli_dry_run_end_to_end_and_resume(tmp_path: Path, seed_db: Path) -> None:
    args = [
        "run",
        "--matrix",
        "pilot",
        "--dry-run",
        "--workers",
        "2",
        "--results-dir",
        str(tmp_path / "res"),
        "--cache-dir",
        str(seed_db.parent),
        "--variants",
        "toolsets,search-call",
        "--tasks",
        "easy-01-close-ticket,easy-02-find-kb-article,policy-01-refund-too-old",
    ]
    first = runner.invoke(app, args)
    assert first.exit_code == 0, first.output
    assert "finished (ok=" in first.output
    rows = read_rows(tmp_path / "res" / "runs.jsonl")
    base = read_rows(tmp_path / "res" / "context_baseline.jsonl")
    assert len(rows) == 6 and len(base) == 9  # 2 x 3 tasks; (2 variants + none) x 3 repeats
    assert all(r["status"] == "ok" and r["pass"] is True for r in rows), [
        r["grade_reason"] for r in rows
    ]
    assert all(r["claude_version"] == "fake" and r["env_flags"]["dry_run"] for r in rows)
    assert all(r["isolation_problems"] == [] or r["variant"] for r in base)
    assert list((tmp_path / "res" / "raw").glob("*.stream.jsonl.gz"))
    again = runner.invoke(app, args)
    assert again.exit_code == 0 and "0 to run" in again.output
    assert len(read_rows(tmp_path / "res" / "runs.jsonl")) == 6  # nothing was appended


def test_replay_mode_fails_action_tasks_and_records_them(tmp_path: Path, seed_db: Path) -> None:
    cfg = _cfg(tmp_path, seed_db, "replay")
    row = execute_run(
        RunSpec(VARIANTS["toolsets"], "haiku", TASKS["easy-03-ship-order"], 1), cfg, "fake"
    )
    assert row["status"] == "ok" and row["pass"] is False and "shipped" in row["grade_reason"]


@pytest.mark.parametrize(("mode", "status"), [("crash", "crash"), ("ratelimit", "rate_limited")])
def test_infrastructure_failures_are_recorded_after_bounded_retries(
    tmp_path: Path, seed_db: Path, mode: str, status: str
) -> None:
    cfg = _cfg(tmp_path, seed_db, mode)
    row = execute_run(
        RunSpec(VARIANTS["toolsets"], "haiku", TASKS["easy-03-ship-order"], 1), cfg, "fake"
    )
    assert row["status"] == status and row["retries"] == 1 and row["pass"] is None and row["error"]


def test_hang_is_killed_and_recorded_as_timeout(tmp_path: Path, seed_db: Path) -> None:
    cfg = _cfg(tmp_path, seed_db, "hang", timeout_s=2.0)
    row = execute_run(
        RunSpec(VARIANTS["toolsets"], "haiku", TASKS["easy-03-ship-order"], 1), cfg, "fake"
    )
    assert row["status"] == "timeout" and row["retries"] == 0
    assert row["pass"] is False  # state is still graded after a timeout


def test_provisioning_failure_is_an_infra_error(tmp_path: Path) -> None:
    cfg = RunConfig(retries=0, seed_db=tmp_path / "missing.db", dry_run=True, raw_dir=tmp_path)
    row = execute_run(RunSpec(VARIANTS["toolsets"], "haiku", TASKS["easy-03-ship-order"], 1), cfg)
    assert row["status"] == "infra_error" and row["error"]


def test_baseline_without_server_has_no_tools(tmp_path: Path, seed_db: Path) -> None:
    cfg = _cfg(tmp_path, seed_db, "replay")
    row = execute_run(RunSpec(ALL_VARIANTS["none"], "haiku", None, 1), cfg, "fake")
    assert row["init_tool_count"] == 0 and row["isolation_problems"] == [] and row["pass"] is None


def test_plan_and_listing_commands(tmp_path: Path) -> None:
    out = runner.invoke(app, ["plan", "--matrix", "full"])
    assert out.exit_code == 0 and "834 runs" in out.output and "780 task" in out.output
    listing = runner.invoke(app, ["list-tasks"])
    assert "easy-01-close-ticket" in listing.output
    bad = runner.invoke(app, ["run", "--matrix", "nope", "--plan"])
    assert bad.exit_code == 2
    plan = runner.invoke(
        app, ["run", "--matrix", "pilot", "--plan", "--results-dir", str(tmp_path)]
    )
    assert plan.exit_code == 0 and "27 runs planned" in plan.output


def test_pause_backs_off_after_a_rate_limit() -> None:
    slept: list[float] = []
    pause = Pause(slept.append, seconds=60)
    pause.wait(0)
    pause.trigger(10)
    pause.wait(20)
    assert slept == [50]
    pause.wait(100)
    assert slept == [50]


def test_existing_rows_reads_several_files(tmp_path: Path) -> None:
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    a.write_text('{"x": 1}\n')
    assert existing_rows(a, b) == [{"x": 1}]


def test_fake_claude_modes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BENCH_FAKE_MODE", "crash")
    assert fake_claude.main([]) == 1
    monkeypatch.setenv("BENCH_FAKE_MODE", "ratelimit")
    assert fake_claude.main([]) == 1
    assert '"api_error_status": 429' in capsys.readouterr().out
    cfg = tmp_path / "empty.json"
    cfg.write_text('{"mcpServers": {}}')
    monkeypatch.setenv("BENCH_FAKE_MODE", "replay")
    assert fake_claude.main(["--mcp-config", str(cfg)]) == 0
    events = [json.loads(ln) for ln in capsys.readouterr().out.splitlines()]
    init = next(e for e in events if e.get("subtype") == "init")
    assert init["tools"] == [] and init["mcp_servers"] == []
    assert fake_claude._api_from_config(str(cfg)) is None
    assert fake_claude._arg(["--a", "b"], "--a") == "b" and fake_claude._arg([], "--a") is None


def test_probe_tool_search_with_fake_claude(tmp_path: Path, seed_db: Path) -> None:
    from bench.harness.probe import PROBE_CASES, probe_variants, run_probe

    assert len(probe_variants()) == len(PROBE_CASES)
    cfg = _cfg(tmp_path, seed_db, "replay")
    rows = run_probe(cfg, "sonnet")
    assert len(rows) == len(PROBE_CASES)
    assert {r["probe_env"] for r in rows} == {"unset", "false", "true", "force", "auto"}
    assert all(r["status"] == "ok" for r in rows)


def test_probe_command_dry_run(
    tmp_path: Path, seed_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("bench.harness.cli.ensure_seed_db", lambda *_a, **_k: seed_db)
    result = runner.invoke(app, ["probe-tool-search", "--dry-run", "--results-dir", str(tmp_path)])
    assert result.exit_code == 0 and "ENABLE_TOOL_SEARCH=unset" in result.output
    assert (tmp_path / "tool_search_probe.jsonl").exists()
