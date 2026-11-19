"""Prove every task is solvable and its grader is sound, without a model.

For each task, on a fresh seeded environment: grade the untouched state (an action task must
fail, a read task with an empty answer must fail, a policy task with an empty answer must fail),
then run the scripted oracle and grade again (must pass).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from bench.harness.api import Api
from bench.harness.grading import GradeContext, load_fixtures
from bench.harness.isolation import run_env
from bench.harness.tasks import Task


@dataclass
class OracleResult:
    task_id: str
    do_nothing_pass: bool
    do_nothing_reason: str
    oracle_pass: bool
    oracle_reason: str

    @property
    def ok(self) -> bool:
        return (not self.do_nothing_pass) and self.oracle_pass


def check_task(task: Task, seed_db: Path | None = None) -> OracleResult:
    fixtures = load_fixtures()
    with run_env(None, seed_db=seed_db) as env:
        api = Api(env.client())
        idle = task.grader(GradeContext(api, fixtures, ""))
        answer = task.oracle(api, fixtures)
        solved = task.grader(GradeContext(Api(env.client()), fixtures, answer))
    return OracleResult(task.id, idle.passed, idle.reason, solved.passed, solved.reason)
