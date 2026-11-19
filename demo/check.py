"""Prove every demo task is solvable and its grader is sound, without a model."""

from __future__ import annotations

from pathlib import Path

from bench.harness.isolation import ensure_seed_db
from bench.harness.oracle_check import OracleResult, check_task
from bench.harness.tasks import spec

from demo.tasks import load_demo_tasks


def unknown_operations() -> list[str]:
    """Operation ids named in tasks.yaml that the API does not have."""
    known = spec()
    bad: list[str] = []
    for task in load_demo_tasks():
        for op in (*task.needs_operations, *task.also_ok_operations):
            if known.get(op) is None:
                bad.append(f"{task.id}: {op}")
    return bad


def check_all(seed_db: Path | None = None) -> list[OracleResult]:
    seed = seed_db or ensure_seed_db()
    return [check_task(task, seed) for task in load_demo_tasks()]
