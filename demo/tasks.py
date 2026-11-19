"""Load the demo tasks as benchmark :class:`Task` objects so the benchmark machinery applies."""

from __future__ import annotations

from pathlib import Path

from bench.harness.tasks import Task, TaskError, parse_task

import demo.graders  # noqa: F401  (registers graders and oracles)
from demo.facts import facts, load_doc  # noqa: F401
from demo.paths import TASKS_YAML


def load_demo_tasks(path: Path = TASKS_YAML) -> list[Task]:
    tasks = [parse_task(t) for t in load_doc(path)]
    if len({t.id for t in tasks}) != len(tasks):
        raise TaskError("duplicate task ids")
    return tasks


def titles(path: Path = TASKS_YAML) -> dict[str, str]:
    return {str(t["id"]): str(t.get("title", t["id"])) for t in load_doc(path)}
