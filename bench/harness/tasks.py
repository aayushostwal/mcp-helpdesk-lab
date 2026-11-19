"""Task definitions: YAML files in ``bench/tasks`` plus the Python graders and oracles."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

import bench.tasks.graders
import bench.tasks.oracles  # noqa: F401  (registers oracles)
from bench.harness.grading import GRADERS, ORACLES, Grader, Oracle
from bench.harness.paths import TASKS_DIR
from helpdesk_mcp.settings import McpSettings
from helpdesk_mcp.spec import Spec, load_spec
from helpdesk_mcp.subset import select_operations

BAND_COUNTS = {"easy": 6, "multi-step": 8, "ambiguous": 4, "policy": 2}


@dataclass(frozen=True)
class Task:
    id: str
    band: str
    kind: str  # action | read | policy
    prompt: str
    needs_operations: tuple[str, ...]
    also_ok_operations: tuple[str, ...]
    grader_name: str
    oracle_name: str

    @property
    def grader(self) -> Grader:
        return GRADERS[self.grader_name]

    @property
    def oracle(self) -> Oracle:
        return ORACLES[self.oracle_name]

    @property
    def expects_state_change(self) -> bool:
        return self.kind == "action"


class TaskError(ValueError):
    pass


def parse_task(doc: dict[str, Any]) -> Task:
    try:
        task = Task(
            id=str(doc["id"]),
            band=str(doc["band"]),
            kind=str(doc["kind"]),
            prompt=str(doc["prompt"]),
            needs_operations=tuple(doc["needs_operations"]),
            also_ok_operations=tuple(doc.get("also_ok_operations", ())),
            grader_name=str(doc["grader"]),
            oracle_name=str(doc.get("oracle", doc["grader"])),
        )
    except KeyError as exc:
        raise TaskError(f"task {doc.get('id', '?')} is missing {exc}") from exc
    if task.band not in BAND_COUNTS:
        raise TaskError(f"{task.id}: unknown band '{task.band}'")
    if task.kind not in ("action", "read", "policy"):
        raise TaskError(f"{task.id}: unknown kind '{task.kind}'")
    if task.grader_name not in GRADERS or task.oracle_name not in ORACLES:
        raise TaskError(f"{task.id}: grader or oracle is not registered")
    return task


def load_tasks(directory: Path = TASKS_DIR) -> list[Task]:
    tasks = [parse_task(yaml.safe_load(p.read_text())) for p in sorted(directory.glob("*.yaml"))]
    ids = [t.id for t in tasks]
    if len(ids) != len(set(ids)):
        raise TaskError("duplicate task ids")
    return tasks


_SPEC: Spec | None = None


def spec() -> Spec:
    global _SPEC
    if _SPEC is None:
        _SPEC = load_spec(McpSettings())
    return _SPEC


def solvable_at(task: Task, tool_count: int | None) -> bool:
    """True when a per-endpoint server with ``tool_count`` tools offers every needed operation."""
    exposed = set(select_operations(spec(), tool_count))
    return set(task.needs_operations) <= exposed


def operation_is_write(operation_id: str) -> bool | None:
    op = spec().get(operation_id)
    return None if op is None else op.method != "GET"
