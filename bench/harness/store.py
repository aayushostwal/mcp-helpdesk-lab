"""Append-only result files: JSONL rows and compressed raw streams. Everything is scrubbed."""

from __future__ import annotations

import gzip
import json
import threading
from pathlib import Path
from typing import Any

from bench.harness.scrub import scrub_text

_LOCK = threading.Lock()


def append_row(path: Path, row: dict[str, Any]) -> None:
    """Append one JSON line (scrubbed). Safe to call from several worker threads."""
    line = scrub_text(json.dumps(row, sort_keys=True, separators=(",", ":")))
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def write_raw_stream(raw_dir: Path, run_id: str, stream_text: str) -> Path:
    """Store the scrubbed stream as ``<run_id>.stream.jsonl.gz``."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    target = raw_dir / f"{run_id}.stream.jsonl.gz"
    with gzip.open(target, "wt", encoding="utf-8", compresslevel=9) as fh:
        fh.write(scrub_text(stream_text))
    return target


def read_raw_stream(path: Path) -> str:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return fh.read()
