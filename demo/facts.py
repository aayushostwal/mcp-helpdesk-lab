"""Read the ``facts`` block of a task in tasks.yaml (kept separate to avoid an import cycle)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from bench.harness.tasks import TaskError

from demo.paths import TASKS_YAML


@lru_cache(maxsize=4)
def load_doc(path: Path = TASKS_YAML) -> list[dict[str, Any]]:
    doc = yaml.safe_load(path.read_text())
    tasks = doc.get("tasks") if isinstance(doc, dict) else None
    if not isinstance(tasks, list) or not tasks:
        raise TaskError(f"{path} has no tasks")
    return [dict(t) for t in tasks]


def facts(task_id: str, path: Path = TASKS_YAML) -> dict[str, Any]:
    for t in load_doc(path):
        if t["id"] == task_id:
            return dict(t.get("facts", {}))
    raise TaskError(f"unknown demo task '{task_id}'")
