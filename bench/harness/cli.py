"""``python -m bench``: run the benchmark, check the tasks, build the report."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from bench.harness import paths
from bench.harness.isolation import ensure_seed_db
from bench.harness.matrix import MatrixError, estimate, expand, load_matrix, pending
from bench.harness.orchestrate import claude_version, existing_rows, run_all
from bench.harness.runner import FAKE_CLAUDE, INFRA_STATUSES, RunConfig
from bench.harness.tasks import load_tasks, solvable_at

app = typer.Typer(add_completion=False, no_args_is_help=True, help="MCP tool-design benchmark.")

DRY_RESULTS = paths.SCRATCH_DIR / "dry-run-results"


def _csv(value: str | None) -> list[str] | None:
    return [v.strip() for v in value.split(",") if v.strip()] if value else None


@app.command()
def run(
    matrix: Annotated[str, typer.Option(help="full, pilot or a YAML file.")] = "pilot",
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Use a fake claude; never calls a model.")
    ] = False,
    workers: Annotated[int, typer.Option(help="Parallel runs.")] = 3,
    models: Annotated[str | None, typer.Option(help="Comma list, e.g. sonnet,haiku.")] = None,
    variants: Annotated[str | None, typer.Option(help="Comma list of variant names.")] = None,
    tasks: Annotated[str | None, typer.Option(help="Comma list of task ids.")] = None,
    repeats: Annotated[int | None, typer.Option(help="Repeats per task.")] = None,
    baseline_only: Annotated[bool, typer.Option(help="Only the context baseline runs.")] = False,
    tasks_only: Annotated[bool, typer.Option(help="Skip the context baseline runs.")] = False,
    timeout: Annotated[float, typer.Option(help="Wall-clock limit per run in seconds.")] = 300.0,
    retries: Annotated[int, typer.Option(help="Retries for infrastructure errors only.")] = 2,
    results_dir: Annotated[Path | None, typer.Option(help="Where results go.")] = None,
    plan: Annotated[
        bool, typer.Option("--plan", help="Print the plan and estimate, run nothing.")
    ] = False,
    limit: Annotated[
        int | None, typer.Option(help="Run at most N pending runs (debugging).")
    ] = None,
    cache_dir: Annotated[
        Path, typer.Option(help="Where the pristine seeded database is kept.")
    ] = paths.CACHE_DIR,
) -> None:
    """Run (or resume) a matrix. Finished run keys are skipped."""
    try:
        mx = load_matrix(matrix)
        if variants:
            mx = mx.replace(variants=tuple(_csv(variants) or ()))
        if models:
            mx = mx.replace(models=tuple(_csv(models) or ()))
        if tasks:
            mx = mx.replace(tasks=tuple(_csv(tasks) or ()))
        if repeats:
            mx = mx.replace(repeats=repeats)
        specs, skipped = expand(
            mx, load_tasks(), do_tasks=not baseline_only, do_baseline=not tasks_only
        )
    except MatrixError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc
    out_dir = results_dir or (DRY_RESULTS if dry_run else paths.RESULTS_DIR)
    runs_path, base_path = out_dir / "runs.jsonl", out_dir / "context_baseline.jsonl"
    rows = existing_rows(runs_path, base_path)
    from bench.harness.matrix import done_keys

    todo = pending(specs, done_keys(rows, INFRA_STATUSES))
    if limit is not None:
        todo = todo[:limit]
    est_rows = existing_rows(paths.RUNS_JSONL, paths.BASELINE_JSONL) if dry_run else rows
    typer.echo(
        f"matrix '{mx.name}': {len(specs)} runs planned, {len(specs) - len(pending(specs, done_keys(rows, INFRA_STATUSES)))} already done, {len(todo)} to run"
    )
    if skipped:
        typer.echo(
            f"  skipped {len(skipped)} (variant, task) pairs that cannot be solved by design (see docs/benchmark.md)"
        )
    typer.echo("  estimate for this run: " + estimate(todo, est_rows, workers).describe())
    if plan or not todo:
        return
    cfg = RunConfig(
        timeout_s=timeout, retries=retries, dry_run=dry_run, seed_db=ensure_seed_db(cache_dir)
    )
    if dry_run:
        cfg.claude_cmd = list(FAKE_CLAUDE)
        cfg.backoff_s = 0.1
        cfg.fake_env = {"BENCH_FAKE_MODE": "oracle"}
        cfg.raw_dir = out_dir / "raw"
    else:
        cfg.raw_dir = out_dir / "raw"
    version = claude_version(cfg.claude_cmd) if not dry_run else "fake"
    summary = run_all(
        todo,
        cfg,
        runs_path=runs_path,
        baseline_path=base_path,
        workers=workers,
        version=version,
        progress=typer.echo,
    )
    typer.echo(summary.describe())


@app.command()
def plan(
    matrix: Annotated[str, typer.Option()] = "full", workers: Annotated[int, typer.Option()] = 3
) -> None:
    """Print run count, time and cost estimate for a matrix (uses results so far as basis)."""
    mx = load_matrix(matrix)
    specs, skipped = expand(mx, load_tasks())
    rows = existing_rows(paths.RUNS_JSONL, paths.BASELINE_JSONL)
    typer.echo(f"matrix '{mx.name}': " + estimate(specs, rows, workers).describe())
    typer.echo(f"  {len(skipped)} (variant, task) pairs skipped as unsolvable at that tool count")


@app.command("check-tasks")
def check_tasks() -> None:
    """Run every task's scripted oracle and the do-nothing check against a fresh API."""
    from bench.harness.isolation import ensure_seed_db
    from bench.harness.oracle_check import check_task

    seed = ensure_seed_db()
    failures = 0
    for task in load_tasks():
        res = check_task(task, seed)
        mark = "ok  " if res.ok else "FAIL"
        failures += 0 if res.ok else 1
        typer.echo(
            f"{mark} {task.id:36} do-nothing={'pass' if res.do_nothing_pass else 'fail'} "
            f"({res.do_nothing_reason[:40]})  oracle={'pass' if res.oracle_pass else 'FAIL'} ({res.oracle_reason[:50]})"
        )
    if failures:
        raise typer.Exit(1)


@app.command("list-tasks")
def list_tasks() -> None:
    """List tasks and at which per-endpoint tool counts each can be solved."""
    for t in load_tasks():
        counts = [str(n) for n in (10, 25, 50, 100) if solvable_at(t, n)]
        typer.echo(f"{t.id:36} {t.band:10} solvable at N={','.join(counts)}")


@app.command()
def report(results_dir: Annotated[Path | None, typer.Option()] = None) -> None:
    """Write summary.csv, summary.md and the charts from the results."""
    from bench.harness.report import build_report

    out = results_dir or paths.RESULTS_DIR
    build_report(out)
    typer.echo(f"report written to {out}")


if __name__ == "__main__":  # pragma: no cover
    app()


@app.command("probe-tool-search")
def probe_tool_search(
    model: Annotated[
        str, typer.Option(help="Model to probe (tool search needs sonnet or larger).")
    ] = "sonnet",
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    results_dir: Annotated[Path | None, typer.Option()] = None,
) -> None:
    """Measure first-request tokens for each ENABLE_TOOL_SEARCH setting (about 9 short runs)."""
    from bench.harness.probe import run_probe
    from bench.harness.store import append_row

    out_dir = results_dir or (DRY_RESULTS if dry_run else paths.RESULTS_DIR)
    cfg = RunConfig(dry_run=dry_run, retries=1, raw_dir=out_dir / "raw", seed_db=ensure_seed_db())
    if dry_run:
        cfg.claude_cmd = list(FAKE_CLAUDE)
        cfg.fake_env = {"BENCH_FAKE_MODE": "replay"}
    for row in run_probe(cfg, model):
        append_row(out_dir / "tool_search_probe.jsonl", row)
        typer.echo(
            f"tools={row['probe_tools']:<10} ENABLE_TOOL_SEARCH={row['probe_env']:<6} "
            f"first request tokens={row['first_request_input_tokens']} tools listed={row['init_tool_count']} "
            f"status={row['status']}"
        )
