"""``python -m demo run|render|check``."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from demo import paths

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Fresh-agent demo.")


def _csv(value: str | None) -> list[str] | None:
    return [v.strip() for v in value.split(",") if v.strip()] if value else None


@app.command()
def run(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Use a fake claude; never calls a model.")
    ] = False,
    setups: Annotated[
        str | None, typer.Option(help="Comma list: per-endpoint,search-call.")
    ] = None,
    tasks: Annotated[str | None, typer.Option(help="Comma list of task ids.")] = None,
    force: Annotated[bool, typer.Option(help="Re-run even if a result file exists.")] = False,
    retries: Annotated[int, typer.Option(help="Retries for infrastructure errors only.")] = 1,
    results_dir: Annotated[Path | None, typer.Option(help="Where results go.")] = None,
) -> None:
    """Run the five tasks, one fresh Claude Code session each, under both setups."""
    from demo.run import demo_config, run_demo

    out = results_dir or (paths.DEMO / ".dry-run" if dry_run else paths.RESULTS_DIR)
    cfg = demo_config(results_dir=out, dry_run=dry_run, retries=retries)
    records = run_demo(
        cfg,
        results_dir=out,
        setups=_csv(setups),
        task_ids=_csv(tasks),
        force=force,
        progress=typer.echo,
    )
    typer.echo(f"{len(records)} run(s) recorded in {out}")


@app.command()
def render(
    results_dir: Annotated[Path | None, typer.Option(help="Read results from here.")] = None,
    out_dir: Annotated[Path | None, typer.Option(help="Write PNGs here.")] = None,
    html_only: Annotated[bool, typer.Option(help="Write the HTML pages, skip the PNGs.")] = False,
    skip_inspector: Annotated[
        bool, typer.Option(help="Do not start an MCP server for the inspector pages.")
    ] = False,
) -> None:
    """Build the screenshots (terminal transcripts, timelines, scoreboard, inspector pages)."""
    from demo.render import render_all

    written = render_all(
        results_dir or paths.RESULTS_DIR,
        out_dir or paths.SHOTS_DIR,
        html_only=html_only,
        inspector=not skip_inspector,
    )
    for path in written:
        typer.echo(str(path))


@app.command()
def check() -> None:
    """Prove each demo task is solvable (scripted oracle) and fails when nothing is done."""
    from demo.check import check_all, unknown_operations

    bad = unknown_operations()
    for item in bad:
        typer.echo(f"FAIL unknown operation: {item}")
    failures = len(bad)
    for res in check_all():
        failures += 0 if res.ok else 1
        typer.echo(
            f"{'ok  ' if res.ok else 'FAIL'} {res.task_id:22} "
            f"do-nothing={'pass' if res.do_nothing_pass else 'fail'} ({res.do_nothing_reason[:44]})  "
            f"oracle={'pass' if res.oracle_pass else 'FAIL'} ({res.oracle_reason[:50]})"
        )
    if failures:
        raise typer.Exit(1)
