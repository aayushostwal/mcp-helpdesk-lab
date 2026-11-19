from __future__ import annotations

from pathlib import Path

import pytest
from bench.harness.cli import app
from bench.harness.regrade import RegradeError, regrade_task
from bench.harness.store import append_row, read_rows
from typer.testing import CliRunner


def row(
    run_id: str, repeat: int, answer: str, passed: bool, reason: str, **kw: object
) -> dict[str, object]:
    return {
        "run_id": run_id,
        "variant": "toolsets",
        "model_requested": "haiku",
        "task_id": "multi-05-busiest-agent",
        "repeat": repeat,
        "status": "ok",
        "pass": passed,
        "grade_reason": reason,
        "final_answer": answer,
        **kw,
    }


def seed_runs(path: Path, top: dict[str, object]) -> None:
    good = f"{top['name']}, team {top['team']}, {top['n']} active tickets"
    wrong = "Nobody useful"
    append_row(
        path, row("r1", 1, good, False, "answer names no busiest agent together with their team")
    )
    append_row(
        path, row("r2", 2, wrong, False, "answer names no busiest agent together with their team")
    )
    append_row(path, row("r3", 3, good, False, "unexpected write(s): tickets.assign"))
    append_row(path, row("r4", 1, good, True, "ok", variant="search-call"))
    append_row(path, row("rx", 2, good, False, "x", variant="search-call", status="infra_error"))


@pytest.fixture
def top(env: object) -> dict[str, object]:
    from bench.harness.api import Api

    api = Api(env.client())  # type: ignore[attr-defined]
    best = max(api.get("/v1/reports/agent-workload")["agents"], key=lambda a: a["active_tickets"])
    return {"name": best["agent_name"], "team": best["team_id"], "n": best["active_tickets"]}


def test_regrade_appends_rows_and_keeps_originals(
    tmp_path: Path, seed_db: Path, top: dict[str, object]
) -> None:
    runs = tmp_path / "runs.jsonl"
    seed_runs(runs, top)
    before = runs.read_text()
    res = regrade_task("multi-05-busiest-agent", runs, seed_db)
    assert (res.regraded, len(res.flipped_to_pass), res.flipped_to_fail, res.unchanged) == (
        4,
        1,
        [],
        3,
    )
    assert runs.read_text().startswith(before)
    new = {r["regraded_from"]: r for r in read_rows(runs) if r.get("regraded_from")}
    assert set(new) == {"r1", "r2", "r3", "r4"}
    assert new["r1"]["pass"] is True and new["r1"]["pass_original"] is False
    assert new["r1"]["grade_reason_original"].startswith("answer names no busiest")
    assert new["r2"]["pass"] is False
    assert new["r3"]["pass"] is False and "unexpected write" in new["r3"]["grade_reason"]
    again = regrade_task("multi-05-busiest-agent", runs, seed_db)
    assert again.regraded == 0 and again.skipped_already == 4


def test_regrade_dry_appends_nothing(tmp_path: Path, seed_db: Path, top: dict[str, object]) -> None:
    runs = tmp_path / "runs.jsonl"
    seed_runs(runs, top)
    before = runs.read_text()
    regrade_task("multi-05-busiest-agent", runs, seed_db, dry=True)
    assert runs.read_text() == before


def test_regrade_refuses_write_and_unknown_tasks(tmp_path: Path, seed_db: Path) -> None:
    with pytest.raises(RegradeError, match="read tasks"):
        regrade_task("multi-04-merge-duplicate-customers", tmp_path / "r.jsonl", seed_db)
    with pytest.raises(RegradeError, match="unknown"):
        regrade_task("nope", tmp_path / "r.jsonl", seed_db)


def test_regrade_command(tmp_path: Path, top: dict[str, object]) -> None:
    runs = tmp_path / "runs.jsonl"
    seed_runs(runs, top)
    result = CliRunner().invoke(
        app, ["regrade", "--task", "multi-05-busiest-agent", "--results-dir", str(tmp_path)]
    )
    assert result.exit_code == 0 and "1 fail->pass" in result.output
    bad = CliRunner().invoke(
        app, ["regrade", "--task", "easy-03-ship-order", "--results-dir", str(tmp_path)]
    )
    assert bad.exit_code == 2
