"""Run a list of :class:`RunSpec` with a worker pool, writing each row as soon as it finishes."""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bench.harness.runner import INFRA_STATUSES, RunConfig, RunSpec, execute_run
from bench.harness.store import append_row, read_rows

RATE_LIMIT_PAUSE_S = 60.0


def claude_version(cmd: list[str]) -> str | None:
    try:
        out = subprocess.run(
            [*cmd, "--version"], capture_output=True, text=True, timeout=30, check=True
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return None
    return out.strip().split()[0] if out.strip() else None


@dataclass
class Summary:
    attempted: int = 0
    by_status: dict[str, int] | None = None

    def describe(self) -> str:
        parts = ", ".join(f"{k}={v}" for k, v in sorted((self.by_status or {}).items()))
        return f"{self.attempted} runs finished ({parts})"


class Pause:
    """Shared back-off: after a rate limit every worker waits before starting its next run."""

    def __init__(self, sleep: Callable[[float], None], seconds: float = RATE_LIMIT_PAUSE_S) -> None:
        self._lock = threading.Lock()
        self._until = 0.0
        self._sleep = sleep
        self._seconds = seconds

    def trigger(self, now: float) -> None:
        with self._lock:
            self._until = max(self._until, now + self._seconds)

    def wait(self, now: float) -> None:
        with self._lock:
            delay = self._until - now
        if delay > 0:
            self._sleep(delay)


def run_all(
    specs: list[RunSpec],
    cfg: RunConfig,
    *,
    runs_path: Path,
    baseline_path: Path,
    workers: int,
    version: str | None,
    progress: Callable[[str], None] = print,
    clock: Callable[[], float] | None = None,
) -> Summary:
    import time

    now = clock or time.monotonic
    pause = Pause(cfg.sleep)
    counts: dict[str, int] = {}

    def one(spec: RunSpec) -> dict[str, Any]:
        pause.wait(now())
        row = execute_run(spec, cfg, version)
        if row["status"] == "rate_limited":
            pause.trigger(now())
        append_row(baseline_path if spec.task is None else runs_path, row)
        return row

    total = len(specs)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(one, s): s for s in specs}
        for i, fut in enumerate(as_completed(futures), start=1):
            spec = futures[fut]
            row = fut.result()
            counts[row["status"]] = counts.get(row["status"], 0) + 1
            verdict = "" if row.get("pass") is None else (" PASS" if row["pass"] else " FAIL")
            progress(f"[{i}/{total}] {spec.key} -> {row['status']}{verdict}")
    return Summary(total, counts)


def existing_rows(*paths: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for p in paths:
        rows += read_rows(p)
    return rows


__all__ = ["INFRA_STATUSES", "Summary", "claude_version", "existing_rows", "run_all"]
