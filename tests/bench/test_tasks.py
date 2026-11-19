from __future__ import annotations

from pathlib import Path

import pytest
from bench.harness.tasks import (
    BAND_COUNTS,
    TaskError,
    load_tasks,
    operation_is_write,
    parse_task,
    solvable_at,
    spec,
)

from helpdesk_mcp.subset import ALWAYS_INCLUDED

TASKS = load_tasks()


def test_twenty_tasks_in_the_design_bands() -> None:
    assert len(TASKS) == 20
    for band, count in BAND_COUNTS.items():
        assert sum(1 for t in TASKS if t.band == band) == count
    assert len({t.id for t in TASKS}) == 20


def test_prompts_do_not_name_operations() -> None:
    operation_ids = set(spec().operations)
    for task in TASKS:
        assert not any(op in task.prompt for op in operation_ids), task.id


def test_needed_operations_exist() -> None:
    known = set(spec().operations)
    for task in TASKS:
        assert task.needs_operations
        assert set(task.needs_operations) <= known, task.id
        assert set(task.also_ok_operations) <= known, task.id


def test_at_least_two_tasks_where_the_right_move_is_not_to_act() -> None:
    policy = [t for t in TASKS if t.kind == "policy"]
    assert len(policy) >= 2
    assert all(t.band == "policy" for t in policy)


def test_every_task_is_solvable_at_25_50_100() -> None:
    for n in (25, 50, 100, None):
        assert all(solvable_at(t, n) for t in TASKS), n


def test_always_included_covers_all_needs_within_25() -> None:
    needs = {op for t in TASKS for op in t.needs_operations}
    assert needs <= set(ALWAYS_INCLUDED[:25])


def test_ten_tools_cannot_solve_half_the_tasks_by_design() -> None:
    unsolvable = sorted(t.id for t in TASKS if not solvable_at(t, 10))
    assert len(unsolvable) == 10
    assert all(t.band in ("easy", "multi-step") for t in TASKS if t.id in unsolvable)
    assert {t.band for t in TASKS if solvable_at(t, 10)} == set(BAND_COUNTS)


def test_operation_is_write() -> None:
    assert operation_is_write("tickets_get") is False
    assert operation_is_write("tickets_close") is True
    assert operation_is_write("not_an_operation") is None


def test_parse_task_errors(tmp_path: Path) -> None:
    good = {
        "id": "x",
        "band": "easy",
        "kind": "action",
        "prompt": "p",
        "needs_operations": ["tickets_get"],
        "grader": "close_ticket",
    }
    assert parse_task(good).oracle_name == "close_ticket"
    assert parse_task(good).expects_state_change
    for bad in (
        {k: v for k, v in good.items() if k != "prompt"},
        {**good, "band": "huge"},
        {**good, "kind": "dance"},
        {**good, "grader": "missing"},
    ):
        with pytest.raises(TaskError):
            parse_task(bad)


def test_duplicate_ids_rejected(tmp_path: Path) -> None:
    for name in ("a.yaml", "b.yaml"):
        (tmp_path / name).write_text(
            "id: same\nband: easy\nkind: read\nprompt: p\nneeds_operations: [tickets_get]\n"
            "grader: kb_lookup\n"
        )
    with pytest.raises(TaskError):
        load_tasks(tmp_path)
