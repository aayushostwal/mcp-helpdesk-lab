"""Offline re-grading of stored read-task runs from their stored final answer.

Used after a grader was corrected post hoc. Nothing is run against a model. The facts a read
grader needs (the workload report, the team list) come from a pristine seeded database, which is
what the agent saw too, because read tasks change nothing. Results are appended as new rows that
point back at the original (``regraded_from``); the original rows are never edited, and the
report counts the latest row per run key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from bench.harness.api import Api
from bench.harness.grading import GradeContext, load_fixtures
from bench.harness.isolation import run_env
from bench.harness.runner import INFRA_STATUSES
from bench.harness.store import append_row, read_rows
from bench.harness.tasks import Task, load_tasks


class RegradeError(ValueError):
    pass


@dataclass
class RegradeResult:
    task_id: str
    regraded: int = 0
    skipped_already: int = 0
    flipped_to_pass: list[str] = field(default_factory=list)
    flipped_to_fail: list[str] = field(default_factory=list)
    unchanged: int = 0

    def describe(self) -> str:
        return (
            f"{self.task_id}: regraded {self.regraded} run(s), {len(self.flipped_to_pass)} fail->pass, "
            f"{len(self.flipped_to_fail)} pass->fail, {self.unchanged} unchanged, "
            f"{self.skipped_already} already regraded"
        )


def _find_task(task_id: str) -> Task:
    for task in load_tasks():
        if task.id == task_id:
            return task
    raise RegradeError(f"unknown task '{task_id}'")


def regrade_task(
    task_id: str, runs_path: Path, seed_db: Path | None = None, *, dry: bool = False
) -> RegradeResult:
    task = _find_task(task_id)
    if task.kind != "read":
        raise RegradeError(
            f"{task_id} is a '{task.kind}' task: its grade depends on database state the agent "
            "changed, which is not stored. Offline re-grading is only allowed for read tasks."
        )
    rows = read_rows(runs_path)
    done = {r["regraded_from"] for r in rows if r.get("regraded_from")}
    originals = [
        r
        for r in rows
        if r["task_id"] == task_id
        and not r.get("regraded_from")
        and r["status"] not in INFRA_STATUSES
        and r.get("pass") is not None
    ]
    result = RegradeResult(task_id)
    fixtures = load_fixtures()
    with run_env(None, seed_db=seed_db) as env:
        api = Api(env.client())
        for row in originals:
            if row["run_id"] in done:
                result.skipped_already += 1
                continue
            reason = str(row["grade_reason"])
            if reason.startswith("unexpected write"):
                # The agent's writes are not stored in the row; a write failure stays a failure.
                passed, new_reason = False, reason
            else:
                ctx = GradeContext(api, fixtures, str(row.get("final_answer") or ""))
                ctx._writes = []  # read task on a pristine database: no writes to look at
                grade = task.grader(ctx)
                passed, new_reason = grade.passed, grade.reason
            new = dict(row)
            new.update(
                {
                    "run_id": f"{row['run_id']}-regrade",
                    "pass": passed,
                    "grade_reason": new_reason,
                    "regraded_from": row["run_id"],
                    "grade_reason_original": row["grade_reason"],
                    "pass_original": row["pass"],
                }
            )
            result.regraded += 1
            if passed and not row["pass"]:
                result.flipped_to_pass.append(row["run_id"])
            elif row["pass"] and not passed:
                result.flipped_to_fail.append(row["run_id"])
            else:
                result.unchanged += 1
            if not dry:
                append_row(runs_path, new)
    return result
