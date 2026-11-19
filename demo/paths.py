"""Locations used by the demo."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from bench.harness.paths import ROOT

DEMO = ROOT / "demo"
TASKS_YAML = DEMO / "tasks.yaml"
RESULTS_DIR = DEMO / "results"
RAW_DIR = RESULTS_DIR / "raw"
COMBINED_JSON = RESULTS_DIR / "demo.json"
SHOTS_DIR = DEMO / "screenshots"
HTML_DIR = DEMO / ".html"  # intermediate pages, not committed
# Python interpreter that has Playwright and its browsers installed. Set DEMO_PLAYWRIGHT_PYTHON
# to a different interpreter; by default the current one is used (run `pip install playwright`
# and `playwright install chromium` in it first).
PLAYWRIGHT_PYTHON = Path(os.environ.get("DEMO_PLAYWRIGHT_PYTHON", sys.executable))
