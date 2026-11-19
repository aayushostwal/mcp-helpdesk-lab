"""Locations used by the demo."""

from __future__ import annotations

from pathlib import Path

from bench.harness.paths import ROOT

DEMO = ROOT / "demo"
TASKS_YAML = DEMO / "tasks.yaml"
RESULTS_DIR = DEMO / "results"
RAW_DIR = RESULTS_DIR / "raw"
COMBINED_JSON = RESULTS_DIR / "demo.json"
SHOTS_DIR = DEMO / "screenshots"
HTML_DIR = DEMO / ".html"  # intermediate pages, not committed
# The conda environment that has Playwright and its browsers (the project venv does not).
PLAYWRIGHT_PYTHON = Path("/Users/aayushostwal/opt/miniconda3/envs/plat/bin/python")
