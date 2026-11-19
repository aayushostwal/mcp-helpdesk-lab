"""Matrix definition, expansion, presets, resumability and run-count/time/cost estimates."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from bench.harness.runner import RunSpec, run_key
from bench.harness.stats import median
from bench.harness.tasks import Task, solvable_at
from bench.harness.variants import ALL_VARIANTS, NO_SERVER_VARIANTS, VARIANTS, baseline_for

PILOT_TASKS = (
    "easy-01-close-ticket",
    "easy-02-find-kb-article",
    "multi-01-refund-recent-order",
    "multi-06-merge-duplicate-tickets",
    "ambig-01-tell-the-customer",
)
PILOT_VARIANTS = ("per-endpoint-100", "toolsets", "search-call")
# Planning defaults when no pilot data exists for a model or variant.
DEFAULT_SECONDS_PER_RUN = 40.0
DEFAULT_COST_PER_RUN = {"haiku": 0.01, "sonnet": 0.08}


class MatrixError(ValueError):
    pass


@dataclass(frozen=True)
class Matrix:
    name: str
    variants: tuple[str, ...]
    models: tuple[str, ...]
    tasks: tuple[str, ...] | None  # None: all tasks
    repeats: int
    baseline_repeats: int = 3
    include_unsolvable: bool = False
    extra: dict[str, Any] = field(default_factory=dict, compare=False)

    def replace(self, **kw: Any) -> Matrix:
        data: dict[str, Any] = {**self.__dict__, **kw}
        return Matrix(**data)


FULL = Matrix("full", tuple(VARIANTS), ("sonnet", "haiku"), None, 3)
PILOT = Matrix("pilot", PILOT_VARIANTS, ("haiku",), PILOT_TASKS, 1)


def load_matrix(name_or_path: str) -> Matrix:
    if name_or_path == "full":
        return FULL
    if name_or_path == "pilot":
        return PILOT
    path = Path(name_or_path)
    if not path.exists():
        raise MatrixError(f"unknown matrix '{name_or_path}' (use full, pilot or a YAML file)")
    doc = yaml.safe_load(path.read_text())
    return Matrix(
        name=str(doc.get("name", path.stem)),
        variants=tuple(doc.get("variants", tuple(VARIANTS))),
        models=tuple(doc.get("models", ("haiku",))),
        tasks=tuple(doc["tasks"]) if doc.get("tasks") else None,
        repeats=int(doc.get("repeats", 1)),
        baseline_repeats=int(doc.get("baseline_repeats", 3)),
        include_unsolvable=bool(doc.get("include_unsolvable", False)),
    )


def expand(
    matrix: Matrix,
    tasks: list[Task],
    *,
    do_tasks: bool = True,
    do_baseline: bool = True,
) -> tuple[list[RunSpec], list[tuple[str, str]]]:
    """All runs in a stable order, plus the (variant, task) pairs skipped as unsolvable."""
    unknown = [v for v in matrix.variants if v not in VARIANTS]
    if unknown:
        raise MatrixError(f"unknown variant(s): {unknown}; choose from {list(VARIANTS)}")
    by_id = {t.id: t for t in tasks}
    if matrix.tasks is not None:
        missing = [t for t in matrix.tasks if t not in by_id]
        if missing:
            raise MatrixError(f"unknown task(s): {missing}")
        chosen = [by_id[t] for t in matrix.tasks]
    else:
        chosen = list(tasks)
    specs: list[RunSpec] = []
    skipped: list[tuple[str, str]] = []
    if do_baseline:
        base_variants = [ALL_VARIANTS[v] for v in matrix.variants]
        base_variants += [baseline_for(ALL_VARIANTS[v]) for v in matrix.variants]
        seen: set[str] = set()
        for variant in base_variants:
            if variant.name in seen:
                continue
            seen.add(variant.name)
            for model in matrix.models:
                specs += [
                    RunSpec(variant, model, None, r) for r in range(1, matrix.baseline_repeats + 1)
                ]
    if do_tasks:
        for vname in matrix.variants:
            variant = VARIANTS[vname]
            for model in matrix.models:
                for task in chosen:
                    if (
                        variant.kind == "per-endpoint"
                        and not matrix.include_unsolvable
                        and not solvable_at(task, variant.server_tool_count)
                    ):
                        if (vname, task.id) not in skipped:
                            skipped.append((vname, task.id))
                        continue
                    specs += [
                        RunSpec(variant, model, task, r) for r in range(1, matrix.repeats + 1)
                    ]
    return specs, skipped


def pending(specs: list[RunSpec], done_keys: set[str]) -> list[RunSpec]:
    return [s for s in specs if s.key not in done_keys]


def done_keys(rows: list[dict[str, Any]], infra_statuses: set[str]) -> set[str]:
    """Keys that finished (any status except infrastructure failures, which are retried)."""
    return {
        run_key(r["variant"], r["model_requested"], r["task_id"], r["repeat"])
        for r in rows
        if r.get("status") not in infra_statuses
    }


@dataclass
class Estimate:
    runs: int
    task_runs: int
    baseline_runs: int
    seconds_serial: float
    cost_usd: float
    workers: int
    basis: str

    @property
    def wall_minutes(self) -> float:
        return self.seconds_serial / max(self.workers, 1) / 60

    def describe(self) -> str:
        return (
            f"{self.runs} runs ({self.task_runs} task + {self.baseline_runs} context baseline); "
            f"about {self.wall_minutes:.0f} min with {self.workers} workers; "
            f"about ${self.cost_usd:.2f} CLI-reported cost ({self.basis})"
        )


def estimate(specs: list[RunSpec], rows: list[dict[str, Any]], workers: int) -> Estimate:
    """Rough time and cost from earlier rows (the pilot): per (kind, variant family, model) medians."""

    def family(variant: str) -> str:
        if "+" in variant:
            return variant
        return "per-endpoint" if variant.startswith("per-endpoint") else variant

    wall: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    cost: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for r in rows:
        if r.get("status") in ("crash", "infra_error"):
            continue
        key = (r["kind"], family(r["variant"]), r["model_requested"])
        if r.get("wall_ms") is not None:
            wall[key].append(r["wall_ms"] / 1000 + 4)  # + about 4 s to provision and tear down
        if r.get("total_cost_usd") is not None:
            cost[key].append(r["total_cost_usd"])

    # Scale a model's cost from another model when it has no task rows yet, using the ratio of
    # their context-baseline costs for the same variant family.
    def base_cost(fam: str, model: str) -> float | None:
        return median(cost[("baseline", fam, model)])

    def task_cost(fam: str, model: str) -> float | None:
        direct = median(cost[("task", fam, model)])
        if direct is not None:
            return direct
        mine = base_cost(fam, model)
        for (kind, f, other), vals in cost.items():
            theirs = base_cost(fam, other)
            if kind == "task" and f == fam and other != model and vals and mine and theirs:
                return float(median(vals) or 0) * mine / theirs
        return None

    seconds = dollars = 0.0
    guessed = 0
    for s in specs:
        kind = "task" if s.task else "baseline"
        fam = family(s.variant.name)
        key = (kind, fam, s.model)
        w = median(wall[key])
        c = median(cost[key]) if kind == "baseline" else task_cost(fam, s.model)
        if w is None:
            same = [v for k, vals in wall.items() if k[0] == kind and k[2] == s.model for v in vals]
            w = median(same) or median(
                [v for k, vals in wall.items() if k[0] == kind for v in vals]
            )
        if c is None:
            same = [v for k, vals in cost.items() if k[0] == kind and k[2] == s.model for v in vals]
            c = median(same)
        if w is None or c is None:
            guessed += 1
            w = w if w is not None else DEFAULT_SECONDS_PER_RUN
            c = c if c is not None else DEFAULT_COST_PER_RUN.get(s.model, 0.05)
        seconds += w
        dollars += c
    n_task = sum(1 for s in specs if s.task)
    basis = "from pilot medians" if rows else "no pilot data, planning defaults"
    if rows and guessed:
        basis += f"; {guessed} runs use planning defaults"
    return Estimate(len(specs), n_task, len(specs) - n_task, seconds, dollars, workers, basis)


__all__ = ["NO_SERVER_VARIANTS", "Estimate", "Matrix", "MatrixError", "expand", "load_matrix"]
