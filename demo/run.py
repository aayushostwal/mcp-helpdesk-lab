"""Run the demo: one fresh Claude Code session per (task, setup), then write the result files."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from bench.harness.isolation import ensure_seed_db
from bench.harness.orchestrate import claude_version
from bench.harness.runner import FAKE_CLAUDE, INFRA_STATUSES, RunConfig, RunSpec, execute_run
from bench.harness.scrub import scrub_text
from bench.harness.store import read_raw_stream
from bench.harness.tasks import Task

from demo import paths
from demo.setups import SETUPS
from demo.tasks import load_demo_tasks, titles
from demo.transcript import build_transcript

MODEL = "sonnet"
# Looser than the benchmark's 30 turns / 300 s / 1.50 USD: these tasks are multi-step.
MAX_TURNS = 40
TIMEOUT_S = 600.0
BUDGET_USD = 2.0


def demo_config(
    *, results_dir: Path = paths.RESULTS_DIR, dry_run: bool = False, retries: int = 1
) -> RunConfig:
    cfg = RunConfig(
        timeout_s=TIMEOUT_S,
        max_turns=MAX_TURNS,
        retries=retries,
        backoff_s=60.0,
        budget_usd={MODEL: BUDGET_USD},
        raw_dir=results_dir / "raw",
        seed_db=ensure_seed_db(),
        dry_run=dry_run,
    )
    if dry_run:
        cfg.claude_cmd = [*FAKE_CLAUDE[:-1], "demo.fake_claude"]
        cfg.backoff_s = 0.1
        cfg.fake_env = {"BENCH_FAKE_MODE": "oracle"}
    return cfg


def result_path(results_dir: Path, setup: str, task_id: str) -> Path:
    return results_dir / setup / f"{task_id}.json"


def build_record(row: dict[str, Any], task: Task, setup: str, raw_dir: Path) -> dict[str, Any]:
    """The per-run result: the benchmark row's metrics plus the full timeline and final answer."""
    raw = raw_dir / f"{row['run_id']}.stream.jsonl.gz"
    transcript = build_transcript(read_raw_stream(raw)) if raw.exists() else build_transcript("")
    total_tokens = (row.get("input_tokens_total") or 0) + (row.get("output_tokens") or 0)
    return {
        "setup": setup,
        "task_id": task.id,
        "title": titles().get(task.id, task.id),
        "prompt": task.prompt,
        "status": row["status"],
        "pass": row.get("pass"),
        "grade_reason": row.get("grade_reason"),
        "error": row.get("error"),
        "wall_s": _s(row.get("wall_ms")),
        "ttfc_s": _s(transcript.ttfc_ms),
        "n_tool_calls": len(transcript.calls),
        "num_turns": row.get("num_turns"),
        "wrong_write_calls": row.get("wrong_write_calls"),
        "wrong_operations": row.get("wrong_operations"),
        "failed_calls": row.get("failed_calls"),
        "tokens": {
            "input": row.get("input_tokens"),
            "output": row.get("output_tokens"),
            "cache_read": row.get("cache_read_tokens"),
            "cache_creation": row.get("cache_creation_tokens"),
            "input_total": row.get("input_tokens_total"),
            "total": total_tokens,
            "first_request": row.get("first_request_input_tokens"),
        },
        "cost_usd": row.get("total_cost_usd"),
        "tools_listed": row.get("init_tool_count"),
        "calls": [c.as_dict() for c in transcript.calls],
        "agent_text": [{"at_ms": at, "text": text} for at, text in transcript.texts],
        "final_answer": transcript.final_text,
        "model": row.get("model_resolved"),
        "claude_version": row.get("claude_version"),
        "isolation_problems": row.get("isolation_problems"),
        "run_id": row["run_id"],
        "started_at": row.get("ts"),
        "retries": row.get("retries", 0),
        "retry_log": row.get("retry_log", []),
        "raw_stream": f"raw/{row['run_id']}.stream.jsonl.gz" if raw.exists() else None,
    }


def _s(ms: Any) -> float | None:
    return None if ms is None else round(ms / 1000, 2)


def write_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(scrub_text(json.dumps(record, indent=2, ensure_ascii=False)) + "\n")


def load_records(results_dir: Path = paths.RESULTS_DIR) -> list[dict[str, Any]]:
    order = [t.id for t in load_demo_tasks()]
    out: list[dict[str, Any]] = []
    for setup in SETUPS:
        for task_id in order:
            p = result_path(results_dir, setup, task_id)
            if p.exists():
                out.append(json.loads(p.read_text()))
    return out


def write_combined(results_dir: Path = paths.RESULTS_DIR) -> Path:
    records = load_records(results_dir)
    combined = {
        "n_runs": len(records),
        "model": MODEL,
        "setups": list(SETUPS),
        "note": "One run per task and setup; see docs/demo.md.",
        "runs": records,
    }
    target = results_dir / "demo.json"
    target.write_text(scrub_text(json.dumps(combined, indent=2, ensure_ascii=False)) + "\n")
    return target


def run_demo(
    cfg: RunConfig,
    *,
    results_dir: Path = paths.RESULTS_DIR,
    setups: list[str] | None = None,
    task_ids: list[str] | None = None,
    force: bool = False,
    progress: Callable[[str], None] = print,
) -> list[dict[str, Any]]:
    """Run each task under each setup, back to back (task by task, setups alternating).

    A finished record is kept unless ``force``. Infrastructure failures (rate limit, crash) are
    retried by ``execute_run``; a wrong answer is kept as the result, never re-run.
    """
    tasks = [t for t in load_demo_tasks() if not task_ids or t.id in task_ids]
    chosen = [s for s in SETUPS if not setups or s in setups]
    version = "fake" if cfg.dry_run else claude_version(cfg.claude_cmd)
    done: list[dict[str, Any]] = []
    for task in tasks:
        for setup in chosen:
            path = result_path(results_dir, setup, task.id)
            if path.exists() and not force:
                progress(f"skip {setup}/{task.id} (result exists)")
                continue
            spec_ = RunSpec(SETUPS[setup], MODEL, task, 1)
            row = execute_run(spec_, cfg, version)
            record = build_record(row, task, setup, cfg.raw_dir)
            if row["status"] in INFRA_STATUSES:
                progress(f"INFRA FAILURE {setup}/{task.id}: {row['status']}: {row.get('error')}")
                continue  # not written: a rerun picks it up
            write_record(path, record)
            done.append(record)
            progress(
                f"{setup:12} {task.id:20} {'PASS' if record['pass'] else 'FAIL'} "
                f"{record['wall_s']}s calls={record['n_tool_calls']} "
                f"cost={record['cost_usd']} {record['grade_reason'] or ''}"
            )
    write_combined(results_dir)
    return done
