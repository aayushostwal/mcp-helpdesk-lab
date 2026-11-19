from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pytest
from bench.harness import report
from bench.harness.cli import app
from bench.harness.report import (
    build_markdown,
    build_report,
    context_table,
    latest_rows,
    summary_rows,
)
from bench.harness.store import append_row, write_raw_stream
from typer.testing import CliRunner


def task_row(
    variant: str, model: str, task: str, band: str, repeat: int, passed: bool, **kw: Any
) -> dict[str, Any]:
    return {
        "run_id": f"{variant}-{model}-{task}-{repeat}",
        "kind": "task",
        "variant": variant,
        "model_requested": model,
        "task_id": task,
        "band": band,
        "repeat": repeat,
        "status": "ok",
        "pass": passed,
        "grade_reason": "ok" if passed else "bad",
        "input_tokens_total": 40000 + repeat,
        "first_request_input_tokens": 30000,
        "wall_ms": 5000 + 100 * repeat,
        "num_turns": 3,
        "total_cost_usd": 0.01,
        "wrong_calls": 1,
        "wrong_write_calls": 0,
        "wrong_read_calls": 1,
        "n_tool_calls": 3,
        "isolation_problems": [],
        **kw,
    }


def base_row(variant: str, model: str, repeat: int, tokens: int) -> dict[str, Any]:
    return {
        "run_id": f"b-{variant}-{model}-{repeat}",
        "kind": "baseline",
        "variant": variant,
        "model_requested": model,
        "task_id": "baseline",
        "band": None,
        "repeat": repeat,
        "status": "ok",
        "pass": None,
        "first_request_input_tokens": tokens,
        "wall_ms": 2000,
        "total_cost_usd": 0.001,
        "isolation_problems": [],
    }


def write_results(path: Path) -> None:
    for variant in ("per-endpoint-100", "toolsets", "search-call"):
        for model in ("haiku", "sonnet"):
            for band, task in (
                ("easy", "e1"),
                ("multi-step", "m1"),
                ("ambiguous", "a1"),
                ("policy", "p1"),
            ):
                for rep in (1, 2):
                    append_row(
                        path / "runs.jsonl",
                        task_row(variant, model, task, band, rep, rep == 1 or band == "easy"),
                    )
    sizes = {
        "none": 3000,
        "per-endpoint-10": 5000,
        "per-endpoint-100": 30000,
        "toolsets": 9000,
        "search-call": 4400,
        "per-endpoint-100+client-tool-search": 6500,
        "none+client-tool-search": 4800,
    }
    for model in ("haiku", "sonnet"):
        for variant, tokens in sizes.items():
            for rep in (1, 2, 3):
                append_row(path / "context_baseline.jsonl", base_row(variant, model, rep, tokens))
    append_row(
        path / "runs.jsonl",
        task_row(
            "toolsets",
            "haiku",
            "e1",
            "easy",
            9,
            False,
            status="crash",
            pass_=None,
            **{"pass": None},
        ),
    )


def test_context_table_subtracts_the_matching_no_server_baseline(tmp_path: Path) -> None:
    write_results(tmp_path)
    from bench.harness.store import read_rows

    table = {
        (c["variant"], c["model"]): c
        for c in context_table(read_rows(tmp_path / "context_baseline.jsonl"))
    }
    assert table[("per-endpoint-100", "haiku")]["added_tokens"] == 27000
    assert (
        table[("per-endpoint-100+client-tool-search", "haiku")]["added_tokens"] == 1700
    )  # vs none+client-tool-search
    assert table[("toolsets", "sonnet")]["added_tokens"] == 6000
    assert table[("none", "haiku")]["added_tokens"] == 0


def test_latest_row_wins_and_infra_error_is_superseded() -> None:
    first = task_row("toolsets", "haiku", "e1", "easy", 1, False, status="infra_error")
    second = task_row("toolsets", "haiku", "e1", "easy", 1, True)
    assert latest_rows([first, second]) == [second]
    assert latest_rows([second, first]) == [second]


def test_full_report_writes_csv_markdown_and_six_charts(tmp_path: Path) -> None:
    write_results(tmp_path)
    write_raw_stream(
        tmp_path / "raw",
        "b-toolsets-haiku-1",
        json.dumps({"type": "system", "subtype": "init", "tools": ["mcp__helpdesk__tickets"]})
        + "\n",
    )
    info = build_report(tmp_path)
    assert len(info["charts"]) == 6
    for name in info["charts"]:
        png = (tmp_path / "charts" / name).read_bytes()
        assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 5000
    rows = list(csv.DictReader((tmp_path / "summary.csv").open()))
    cell = next(
        r
        for r in rows
        if r["kind"] == "task"
        and r["variant"] == "toolsets"
        and r["model"] == "haiku"
        and r["band"] == "all"
    )
    assert cell["n"] == "8" and float(cell["wilson_lo"]) < float(cell["pass_rate"]) < float(
        cell["wilson_hi"]
    )
    assert any(r["kind"] == "context_baseline" for r in rows)
    text = (tmp_path / "summary.md").read_text()
    assert "too small to conclude" in text and "crash=1" in text
    assert json.loads((tmp_path / "tool_lists.json").read_text()) == {
        "toolsets": ["mcp__helpdesk__tickets"]
    }


def test_markdown_flags_isolation_leaks_and_lists_failures() -> None:
    rows = [
        task_row(
            "toolsets", "haiku", "e1", "easy", 1, False, isolation_problems=["unexpected tools"]
        )
    ]
    text = build_markdown(rows, [], [])
    assert (
        "1 run(s) had unexpected" in text
        and "Failed runs" in text
        and "No context baseline runs yet" in text
    )


def test_report_on_empty_results(tmp_path: Path) -> None:
    info = build_report(tmp_path)
    assert info["charts"] == [] and (tmp_path / "summary.csv").exists()
    assert "No graded task runs yet" in (tmp_path / "summary.md").read_text()


def test_summary_rows_cover_all_bands(tmp_path: Path) -> None:
    rows = [
        task_row("toolsets", "haiku", t, b, 1, True) for b, t in (("easy", "e"), ("policy", "p"))
    ]
    out = summary_rows(rows, [])
    assert {r["band"] for r in out} == {"all", "easy", "policy"}


def test_report_command(tmp_path: Path) -> None:
    write_results(tmp_path)
    result = CliRunner().invoke(app, ["report", "--results-dir", str(tmp_path)])
    assert result.exit_code == 0 and (tmp_path / "charts" / "3-pass-rate-by-band.png").exists()


def test_charts_return_false_without_data(tmp_path: Path) -> None:
    assert not report.chart_context([], tmp_path / "a.png")
    for fn in (
        report.chart_input_tokens,
        report.chart_pass_rate,
        report.chart_wall,
        report.chart_wrong_calls,
        report.chart_cost,
    ):
        assert not fn([], tmp_path / "x.png")


@pytest.mark.parametrize("variant", ["per-endpoint-100+client-tool-search", "per-endpoint-25"])
def test_labels(variant: str) -> None:
    assert "\n" in report._label(variant)
