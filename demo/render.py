"""Build the screenshots: HTML pages first, then PNGs through Playwright (1200 px, 2x)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from demo import pages, paths
from demo.run import load_records
from demo.setups import SETUPS

TERMINAL_TASKS = ["d-refund-trap", "b-refund-triage", "a-sla-sweep"]


def screenshot(jobs: list[tuple[Path, Path]], python: Path | None = None) -> None:
    """Screenshot each HTML file to its PNG with the interpreter that has Playwright."""
    interpreter = str(python or paths.PLAYWRIGHT_PYTHON)
    if not Path(interpreter).exists():
        interpreter = sys.executable
    shot = Path(__file__).with_name("shot.py")
    jobs_file = jobs[0][0].parent / "jobs.json"
    jobs_file.write_text(json.dumps([{"html": str(h), "png": str(p)} for h, p in jobs]))
    subprocess.run([interpreter, str(shot), str(jobs_file)], check=True)


def plan_pages(
    records: list[dict[str, Any]], inspect: dict[str, dict[str, Any]] | None
) -> dict[str, str]:
    """Map output file name (without extension) to page HTML."""
    out: dict[str, str] = {}
    by_key = {(r["setup"], r["task_id"]): r for r in records}
    for setup in SETUPS:
        for task_id in TERMINAL_TASKS:
            if (setup, task_id) in by_key:
                out[f"01-terminal-{task_id}-{setup}"] = pages.terminal_page(
                    by_key[(setup, task_id)]
                )
    x_max = max((r["wall_s"] or 0 for r in records), default=1.0)
    x_max = float(int(x_max / 5) * 5 + 5)
    for setup in SETUPS:
        if any(r["setup"] == setup for r in records):
            out[f"02-timeline-{setup}"] = pages.timeline_page(records, setup, x_max)
    out["03-scoreboard"] = pages.scoreboard_page(records)
    if inspect:
        main = inspect["per-endpoint"]
        out["04-inspector-instructions"] = pages.instructions_page(main)
        out["04-inspector-resources"] = pages.resources_page(main)
        out["04-inspector-prompts"] = pages.prompts_page(main)
        for variant, data in inspect.items():
            out[f"04-inspector-tools-{variant}"] = pages.tools_page(data)
    return out


def render_all(
    results_dir: Path,
    out_dir: Path,
    *,
    html_only: bool = False,
    inspector: bool = True,
    html_dir: Path = paths.HTML_DIR,
) -> list[Path]:
    records = load_records(results_dir)
    inspect = None
    if inspector:
        from demo.inspector import collect

        inspect = {v: collect(v) for v in SETUPS}
    planned = plan_pages(records, inspect)
    html_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    jobs: list[tuple[Path, Path]] = []
    for name, text in planned.items():
        html_path = html_dir / f"{name}.html"
        html_path.write_text(text)
        jobs.append((html_path, out_dir / f"{name}.png"))
    if not html_only and jobs:
        screenshot(jobs)
        return [png for _, png in jobs]
    return [h for h, _ in jobs]
