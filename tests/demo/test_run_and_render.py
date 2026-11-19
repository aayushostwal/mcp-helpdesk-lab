"""The runner and the renderer, with the fake claude and without a browser."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from demo.cli import app
from demo.fake_claude import main as fake_main
from demo.inspector import collect
from demo.run import demo_config, load_records, run_demo, write_combined
from demo.setups import SETUPS
from typer.testing import CliRunner

from demo import pages, render
from demo import paths as demo_paths

cli = CliRunner()


@pytest.fixture(scope="module")
def dry_results(tmp_path_factory: pytest.TempPathFactory, seed_db: Path) -> Path:
    out = tmp_path_factory.mktemp("demo-results")
    cfg = demo_config(results_dir=out, dry_run=True)
    cfg.seed_db = seed_db
    messages: list[str] = []
    run_demo(cfg, results_dir=out, progress=messages.append)
    assert len(messages) == 10 and all("PASS" in m for m in messages)
    return out


def test_dry_run_writes_ten_records_and_a_combined_file(dry_results: Path) -> None:
    for setup in SETUPS:
        assert len(list((dry_results / setup).glob("*.json"))) == 5
    combined = json.loads((dry_results / "demo.json").read_text())
    assert combined["n_runs"] == 10 and len(combined["runs"]) == 10
    rec = load_records(dry_results)[0]
    assert rec["pass"] is True and rec["final_answer"] and rec["calls"]
    assert rec["raw_stream"] and (dry_results / rec["raw_stream"]).exists()
    assert write_combined(dry_results).exists()


def test_existing_results_are_skipped_unless_forced(dry_results: Path, seed_db: Path) -> None:
    cfg = demo_config(results_dir=dry_results, dry_run=True)
    cfg.seed_db = seed_db
    msgs: list[str] = []
    assert run_demo(cfg, results_dir=dry_results, progress=msgs.append) == []
    assert len(msgs) == 10 and all(m.startswith("skip") for m in msgs)
    msgs.clear()
    done = run_demo(
        cfg,
        results_dir=dry_results,
        setups=["search-call"],
        task_ids=["e-research"],
        force=True,
        progress=msgs.append,
    )
    assert len(done) == 1 and done[0]["setup"] == "search-call"


def test_infrastructure_failure_is_reported_and_not_recorded(tmp_path: Path, seed_db: Path) -> None:
    cfg = demo_config(results_dir=tmp_path, dry_run=True, retries=0)
    cfg.seed_db = seed_db
    cfg.fake_env = {"BENCH_FAKE_MODE": "crash"}
    msgs: list[str] = []
    done = run_demo(cfg, results_dir=tmp_path, task_ids=["e-research"], progress=msgs.append)
    assert done == [] and any("INFRA FAILURE" in m for m in msgs)
    assert not list(tmp_path.glob("*/*.json"))


def test_fake_claude_modes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("BENCH_FAKE_MODE", "crash")
    assert fake_main([]) == 1
    monkeypatch.setenv("BENCH_FAKE_MODE", "oracle")
    monkeypatch.setenv("BENCH_FAKE_TASK", "unknown")
    assert fake_main([]) == 0
    assert '"type"' in capsys.readouterr().out


# ----------------------------------------------------------------------------------- pages
def test_truncate_marks_the_cut_and_keeps_the_text() -> None:
    assert pages.truncate("short", 10) == "short"
    cut = pages.truncate("x" * 50, 10)
    assert cut.startswith("x" * 10) and "... [truncated, 40 more characters]" in cut
    assert (
        pages.fmt_ms(None) == "?" and pages.fmt_ms(12) == "12 ms" and pages.fmt_ms(2500) == "2.5 s"
    )
    assert pages.secs(None) == "n/a"


def test_terminal_page_shows_prompt_calls_answer_and_truncation(dry_results: Path) -> None:
    rec = dict(load_records(dry_results)[0])
    rec["final_answer"] = "A" * 3000
    text = pages.terminal_page(rec)
    assert rec["prompt"][:40] in text and "[truncated, 1500 more characters]" in text
    assert "PASS" in text and "tool calls" in text


def test_terminal_page_collapses_long_call_lists(dry_results: Path) -> None:
    rec: dict[str, Any] = dict(load_records(dry_results)[0])
    base = rec["calls"][0]
    rec["calls"] = [{**base, "n": i, "start_ms": i * 100} for i in range(40)]
    rec["agent_text"] = [
        {"at_ms": 2000, "text": "thinking in the hidden middle"},
        {"at_ms": 1, "text": "hi"},
    ]
    rec["pass"] = False
    text = pages.terminal_page(rec)
    assert "14 more tool calls not shown" in text and "FAIL" in text
    assert "hidden middle" not in text and ">hi<" in text


def test_timeline_scoreboard_and_notes(dry_results: Path) -> None:
    recs = load_records(dry_results)
    svg = pages.timeline_page(recs, "search-call", 20.0)
    assert svg.count("<rect") > 10 and "first call" in svg and "seconds since" in svg
    board = pages.scoreboard_page(recs)
    assert "n = 1 run per task" in board and "Total" in board and "5/5 pass" in board
    bad = {**recs[0], "pass": False, "grade_reason": "wrong team"}
    assert pages.score_note(bad) == "wrong team"
    assert pages.score_note({**recs[0], "status": "timeout"}) == "run ended: timeout"
    noisy = {**recs[0], "wrong_write_calls": 2, "failed_calls": 1}
    assert "2 write(s)" in pages.score_note(noisy) and "1 call(s)" in pages.score_note(noisy)
    odd = {
        **recs[0],
        "wrong_write_calls": 0,
        "failed_calls": 0,
        "wrong_operations": ["admin_settings_get"],
    }
    assert pages.score_note(odd) == "reads outside the task: admin_settings_get"
    assert "FAIL" in pages.scoreboard_page([bad])
    assert pages.timeline_page([bad], "per-endpoint", 12.0)


def test_inspector_collects_real_server_data_and_renders_pages() -> None:
    data = {v: collect(v) for v in SETUPS}
    assert len(data["per-endpoint"]["tools"]) == 102 and len(data["search-call"]["tools"]) == 3
    main = data["per-endpoint"]
    assert "Writes change real data" in main["instructions"]
    assert len(main["resources"]) == 3 and len(main["templates"]) == 1 and len(main["prompts"]) == 3
    assert SETUPS["per-endpoint"].tool_count == 102
    assert "Writes change real data" in pages.instructions_page(main)
    assert "helpdesk://schema" in pages.resources_page(main)
    assert "triage_ticket" in pages.prompts_page(main)
    assert "tickets_escalate" in pages.tools_page(main)


def test_render_all_html_only_and_screenshot_call(
    dry_results: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = render.render_all(
        dry_results, tmp_path / "png", html_only=True, inspector=False, html_dir=tmp_path / "html"
    )
    names = {p.name for p in out}
    assert "03-scoreboard.html" in names and "02-timeline-per-endpoint.html" in names
    assert sum(n.startswith("01-terminal-") for n in names) == 6
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    png = render.render_all(
        dry_results, tmp_path / "png", inspector=False, html_dir=tmp_path / "html"
    )
    assert calls and all(p.suffix == ".png" for p in png)
    jobs = json.loads((tmp_path / "html" / "jobs.json").read_text())
    assert len(jobs) == len(png)
    monkeypatch.setattr(demo_paths, "PLAYWRIGHT_PYTHON", Path("/nonexistent/python"))
    calls.clear()
    render.screenshot([(tmp_path / "html" / "03-scoreboard.html", tmp_path / "x.png")])
    assert calls[0][0] == sys.executable


# --------------------------------------------------------------------------------------- cli
def test_cli_check_and_dry_run_and_render(tmp_path: Path, seed_db: Path) -> None:
    res = cli.invoke(app, ["check"])
    assert res.exit_code == 0 and res.output.count("ok  ") == 5
    out = tmp_path / "res"
    run = cli.invoke(
        app,
        [
            "run",
            "--dry-run",
            "--results-dir",
            str(out),
            "--tasks",
            "e-research",
            "--setups",
            "search-call",
        ],
    )
    assert run.exit_code == 0 and "1 run(s) recorded" in run.output
    shots = cli.invoke(
        app,
        [
            "render",
            "--results-dir",
            str(out),
            "--out-dir",
            str(tmp_path / "s"),
            "--html-only",
            "--skip-inspector",
        ],
    )
    assert shots.exit_code == 0 and "03-scoreboard.html" in shots.output
