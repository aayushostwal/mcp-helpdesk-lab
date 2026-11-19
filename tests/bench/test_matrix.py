from __future__ import annotations

from pathlib import Path

import pytest
from bench.harness.matrix import (
    FULL,
    PILOT,
    MatrixError,
    done_keys,
    estimate,
    expand,
    load_matrix,
    pending,
)
from bench.harness.runner import INFRA_STATUSES, RunSpec
from bench.harness.tasks import load_tasks
from bench.harness.variants import ALL_VARIANTS, VARIANTS, baseline_for

TASKS = load_tasks()


def test_full_matrix_counts() -> None:
    specs, skipped = expand(FULL, TASKS)
    task_runs = [s for s in specs if s.task]
    # 7 variants x 2 models x 20 tasks x 3 repeats, minus the tasks that cannot be solved at 10 tools
    assert len(task_runs) == 7 * 2 * 20 * 3 - 10 * 2 * 3 == 780
    assert len(skipped) == 10 and all(v == "per-endpoint-10" for v, _ in skipped)
    baselines = [s for s in specs if not s.task]
    assert len(baselines) == (7 + 2) * 2 * 3 == 54
    assert len({s.key for s in specs}) == len(specs)


def test_include_unsolvable_restores_840() -> None:
    specs, skipped = expand(FULL.replace(include_unsolvable=True), TASKS, do_baseline=False)
    assert len(specs) == 840 and not skipped


def test_pilot_preset() -> None:
    specs, _ = expand(PILOT, TASKS)
    assert len([s for s in specs if s.task]) == 15
    assert len([s for s in specs if not s.task]) == 12  # 3 variants + no-server, 3 repeats
    assert {s.model for s in specs} == {"haiku"}
    bands = [s.task.band for s in specs if s.task and s.variant.name == "toolsets"]
    assert sorted(bands) == ["ambiguous", "easy", "easy", "multi-step", "multi-step"]


def test_client_tool_search_variant_is_configured_separately() -> None:
    ts = VARIANTS["per-endpoint-100+client-tool-search"]
    assert ts.tool_search_env == "true" and ts.builtin_tools == "ToolSearch"
    assert ts.mcp_args == ["--variant", "per-endpoint", "--tool-count", "100"]
    main = VARIANTS["per-endpoint-100"]
    assert main.tool_search_env == "false" and main.builtin_tools == ""
    assert baseline_for(ts).name == "none+client-tool-search"
    assert baseline_for(main).name == "none"
    assert ALL_VARIANTS["none"].mcp_args is None and not ALL_VARIANTS["none"].has_server


def test_selection_flags() -> None:
    only_tasks, _ = expand(PILOT, TASKS, do_baseline=False)
    only_base, _ = expand(PILOT, TASKS, do_tasks=False)
    assert all(s.task for s in only_tasks) and not any(s.task for s in only_base)


def test_unknown_variant_or_task_rejected() -> None:
    with pytest.raises(MatrixError):
        expand(PILOT.replace(variants=("nope",)), TASKS)
    with pytest.raises(MatrixError):
        expand(PILOT.replace(tasks=("nope",)), TASKS)


def test_load_matrix_yaml(tmp_path: Path) -> None:
    path = tmp_path / "m.yaml"
    path.write_text(
        "name: tiny\nvariants: [toolsets]\nmodels: [haiku]\ntasks: [easy-01-close-ticket]\nrepeats: 2\n"
    )
    mx = load_matrix(str(path))
    assert (mx.name, mx.repeats, mx.baseline_repeats) == ("tiny", 2, 3)
    specs, _ = expand(mx, TASKS, do_baseline=False)
    assert len(specs) == 2
    with pytest.raises(MatrixError):
        load_matrix(str(tmp_path / "missing.yaml"))
    assert load_matrix("full") is FULL and load_matrix("pilot") is PILOT


def test_resumability_skips_finished_runs_but_retries_infra_errors() -> None:
    specs, _ = expand(PILOT, TASKS, do_baseline=False)
    first, second, third = specs[:3]

    def row(spec: RunSpec, status: str) -> dict[str, object]:
        return {
            "variant": spec.variant.name,
            "model_requested": spec.model,
            "task_id": spec.task.id if spec.task else "baseline",
            "repeat": spec.repeat,
            "status": status,
        }

    rows = [row(first, "ok"), row(second, "timeout"), row(third, "infra_error")]
    keys = done_keys(rows, INFRA_STATUSES)
    todo = pending(specs, keys)
    assert first not in todo and second not in todo and third in todo
    assert len(todo) == len(specs) - 2


def test_estimate_uses_pilot_rows_and_scales_models() -> None:
    specs, _ = expand(
        FULL.replace(variants=("toolsets",), tasks=("easy-01-close-ticket",), repeats=1), TASKS
    )

    def r(kind: str, model: str, cost: float, wall: int = 5000) -> dict[str, object]:
        return {
            "kind": kind,
            "variant": "toolsets",
            "model_requested": model,
            "status": "ok",
            "wall_ms": wall,
            "total_cost_usd": cost,
        }

    rows = [r("task", "haiku", 0.002), r("baseline", "haiku", 0.001), r("baseline", "sonnet", 0.01)]
    est = estimate(specs, rows, 3)
    assert est.runs == len(specs) and est.task_runs == 2 and est.baseline_runs == len(specs) - 2
    assert est.cost_usd > 0 and est.wall_minutes > 0
    assert "pilot" in est.describe()
    none = estimate(specs, [], 3)
    assert "planning defaults" in none.basis
