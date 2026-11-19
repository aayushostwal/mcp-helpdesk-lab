"""Locations used by the harness."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "bench"
TASKS_DIR = BENCH / "tasks"
FIXTURES = TASKS_DIR / "fixtures.json"
RESULTS_DIR = BENCH / "results"
RAW_DIR = RESULTS_DIR / "raw"
CHARTS_DIR = RESULTS_DIR / "charts"
RUNS_JSONL = RESULTS_DIR / "runs.jsonl"
BASELINE_JSONL = RESULTS_DIR / "context_baseline.jsonl"
CACHE_DIR = BENCH / ".cache"
SCRATCH_DIR = BENCH / ".runs"
FROZEN_TIME = "2026-06-01T12:00:00+00:00"
