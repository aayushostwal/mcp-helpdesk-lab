"""Execute one benchmark run: provision, start Claude Code, parse, grade, build the result row."""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bench.harness.api import Api
from bench.harness.grading import GradeContext, load_fixtures
from bench.harness.isolation import RunEnv, kill_group, run_env
from bench.harness.paths import RAW_DIR, ROOT
from bench.harness.store import write_raw_stream
from bench.harness.stream import ParsedRun, looks_rate_limited, parse_stream
from bench.harness.tasks import Task, operation_is_write, spec
from bench.harness.variants import BenchVariant

BASELINE_PROMPT = "Reply with the word ok"
INFRA_STATUSES = {"crash", "rate_limited", "infra_error"}
GRADED_STATUSES = {"ok", "timeout", "max_turns", "budget_exceeded", "error"}
DEFAULT_BUDGET_USD = {"haiku": 0.50, "sonnet": 1.50}
# The agent's own system prompt names the real date; the API clock is frozen at the seed date.
# Without this line a model sees delivery dates months in the past and (correctly, by its own
# calendar) refuses refunds. The same line is sent in every run, baselines included.
DATE_NOTE = (
    "The helpdesk system clock is set to 2026-06-01 (UTC). Treat 2026-06-01 as today's date for "
    "all work in this session, whatever other date you were given."
)
FAKE_CLAUDE = [sys.executable, "-m", "bench.harness.fake_claude"]  # needs PYTHONPATH=repo root


@dataclass(frozen=True)
class RunSpec:
    variant: BenchVariant
    model: str
    task: Task | None  # None means the "Reply with the word ok" context baseline
    repeat: int

    @property
    def key(self) -> str:
        return run_key(
            self.variant.name, self.model, self.task.id if self.task else "baseline", self.repeat
        )

    @property
    def prompt(self) -> str:
        return self.task.prompt if self.task else BASELINE_PROMPT


def run_key(variant: str, model: str, task_id: str, repeat: int) -> str:
    return f"{variant}|{model}|{task_id}|{repeat}"


@dataclass
class RunConfig:
    claude_cmd: list[str] = field(default_factory=lambda: ["claude"])
    timeout_s: float = 300.0
    max_turns: int = 30
    retries: int = 2
    backoff_s: float = 20.0
    budget_usd: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_BUDGET_USD))
    raw_dir: Path = RAW_DIR
    scratch: Path | None = None
    seed_db: Path | None = None
    dry_run: bool = False
    sleep: Callable[[float], None] = time.sleep
    fake_env: dict[str, str] = field(default_factory=dict)

    def budget_for(self, model: str) -> float:
        return self.budget_usd.get(model, max(self.budget_usd.values()))


# ----------------------------------------------------------------------- git / version info
_GIT: dict[str, Any] = {}


def git_info() -> dict[str, Any]:
    if not _GIT:

        def out(*args: str) -> str:
            try:
                return subprocess.run(
                    ["git", *args],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=10,
                ).stdout.strip()
            except (subprocess.SubprocessError, OSError):
                return ""

        _GIT["git_sha"] = out("rev-parse", "--short", "HEAD") or None
        _GIT["git_dirty"] = bool(out("status", "--porcelain", "--", "src", "bench", "openapi.json"))
    return dict(_GIT)


# ------------------------------------------------------------------------- command and env
def build_command(spec_: RunSpec, cfg: RunConfig, mcp_config: Path) -> list[str]:
    """The exact ``claude`` invocation (also documented in docs/benchmark.md)."""
    return [
        *cfg.claude_cmd,
        "-p",
        spec_.prompt,
        "--model",
        spec_.model,
        "--output-format",
        "stream-json",
        "--verbose",
        "--strict-mcp-config",
        "--mcp-config",
        str(mcp_config),
        "--tools",
        spec_.variant.builtin_tools,
        "--permission-mode",
        "bypassPermissions",
        "--no-session-persistence",
        "--disable-slash-commands",
        "--setting-sources",
        "",
        "--append-system-prompt",
        DATE_NOTE,
        "--max-turns",
        str(cfg.max_turns),
        "--max-budget-usd",
        f"{cfg.budget_for(spec_.model):.2f}",
    ]


def build_env(spec_: RunSpec, cfg: RunConfig) -> dict[str, str]:
    env = dict(os.environ)
    for name in list(env):
        if name.startswith(("HELPDESK", "DATABASE_URL")):
            del env[name]
    env.pop("ENABLE_TOOL_SEARCH", None)
    if spec_.variant.tool_search_env:  # empty means "leave it unset" (probe only)
        env["ENABLE_TOOL_SEARCH"] = spec_.variant.tool_search_env
    if cfg.dry_run:
        env.update(cfg.fake_env)
        env["PYTHONPATH"] = str(ROOT)  # the fake runs from an empty directory
        env["BENCH_FAKE_TASK"] = spec_.task.id if spec_.task else ""
    return env


def run_process(
    cmd: list[str], cwd: Path, env: dict[str, str], timeout_s: float
) -> tuple[str, str, int | None, bool]:
    """Run the agent in its own process group; hard-kill the whole group on timeout."""
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=timeout_s)
        return out, err, proc.returncode, False
    except subprocess.TimeoutExpired:
        kill_group(proc)
        with contextlib.suppress(Exception):
            out, err = proc.communicate(timeout=5)
            return out or "", err or "", proc.returncode, True
        return "", "", None, True
    finally:
        kill_group(proc)


# --------------------------------------------------------------------------- isolation check
def isolation_problems(parsed: ParsedRun, variant: BenchVariant) -> list[str]:
    """Anything in the init event that is not the helpdesk server (a leak from the user setup)."""
    problems: list[str] = []
    allowed_builtin = {"ToolSearch"} if variant.client_tool_search else set()
    foreign = [
        t for t in parsed.tools if not t.startswith("mcp__helpdesk__") and t not in allowed_builtin
    ]
    if foreign:
        problems.append(f"unexpected tools: {foreign[:5]}")
    names = sorted(str(s.get("name")) for s in parsed.mcp_servers)
    expected = ["helpdesk"] if variant.has_server else []
    if names != expected:
        problems.append(f"mcp servers {names}, expected {expected}")
    if any(s.get("status") != "connected" for s in parsed.mcp_servers):
        problems.append("mcp server not connected")
    if parsed.skills:
        problems.append(f"skills loaded: {parsed.skills[:5]}")
    return problems


# ------------------------------------------------------------------------------ call analysis
def analyse_calls(parsed: ParsedRun, task: Task | None) -> dict[str, Any]:
    """Count calls outside the task's needed operations, writes separately from reads."""
    needs = set(task.needs_operations) if task else set()
    ok_reads = needs | (set(task.also_ok_operations) if task else set())
    wrong_writes = wrong_reads = unknown = failed = meta = 0
    wrong_ops: list[str] = []
    for call in parsed.tool_calls:
        if call.ok is False:
            failed += 1
        if call.meta:
            meta += 1
            continue
        if call.operation_id is None or spec().get(call.operation_id) is None:
            unknown += 1
            continue
        if task is None or call.operation_id in ok_reads:
            continue
        wrong_ops.append(call.operation_id)
        if operation_is_write(call.operation_id):
            wrong_writes += 1
        else:
            wrong_reads += 1
    return {
        "wrong_write_calls": wrong_writes,
        "wrong_read_calls": wrong_reads,
        "wrong_calls": wrong_writes + wrong_reads,
        "wrong_operations": wrong_ops,
        "unknown_calls": unknown,
        "failed_calls": failed,
        "meta_calls": meta,
        "needed_operations_called": sorted(
            needs & {c.operation_id for c in parsed.tool_calls if c.operation_id}
        ),
    }


def classify(parsed: ParsedRun, rc: int | None, timed_out: bool, stderr: str) -> str:
    if timed_out:
        return "timeout"
    if looks_rate_limited(parsed, stderr):
        return "rate_limited"
    if not parsed.has_result:
        return "crash"
    if parsed.is_error:
        return {"error_max_turns": "max_turns", "error_max_budget_usd": "budget_exceeded"}.get(
            parsed.result_subtype or "", "error"
        )
    return "ok"


def _new_run_id(spec_: RunSpec) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    task = spec_.task.id if spec_.task else "baseline"
    raw = f"{stamp}-{spec_.variant.name}-{spec_.model}-{task}-r{spec_.repeat}"
    return re.sub(r"[^A-Za-z0-9._-]+", "_", raw) + "-" + uuid.uuid4().hex[:6]


def _tail(text: str, n: int = 400) -> str:
    return text.strip()[-n:]


# -------------------------------------------------------------------------------- attempts
def attempt(spec_: RunSpec, cfg: RunConfig, claude_version: str | None) -> dict[str, Any]:
    run_id = _new_run_id(spec_)
    started = datetime.now(UTC)
    base: dict[str, Any] = {
        "run_id": run_id,
        "ts": started.isoformat(timespec="seconds"),
        **git_info(),
        "claude_version": claude_version,
        "env_flags": {
            "ENABLE_TOOL_SEARCH": spec_.variant.tool_search_env,
            "tools": spec_.variant.builtin_tools,
            "setting_sources": "",
            "strict_mcp_config": True,
            "max_turns": cfg.max_turns,
            "max_budget_usd": cfg.budget_for(spec_.model),
            "timeout_s": cfg.timeout_s,
            "dry_run": cfg.dry_run,
        },
        "kind": "task" if spec_.task else "baseline",
        "variant": spec_.variant.name,
        "tool_count": spec_.variant.tool_count,
        "client_tool_search": spec_.variant.client_tool_search,
        "model_requested": spec_.model,
        "task_id": spec_.task.id if spec_.task else "baseline",
        "band": spec_.task.band if spec_.task else None,
        "repeat": spec_.repeat,
    }
    try:
        with run_env(spec_.variant.mcp_args, seed_db=cfg.seed_db, scratch=cfg.scratch) as env:
            return {**base, **_run_in_env(spec_, cfg, env, run_id)}
    except Exception as exc:  # provisioning failed: nothing ran
        return {
            **base,
            "status": "infra_error",
            "pass": None,
            "error": _tail(f"{type(exc).__name__}: {exc}"),
        }


def _run_in_env(spec_: RunSpec, cfg: RunConfig, env: RunEnv, run_id: str) -> dict[str, Any]:
    cmd = build_command(spec_, cfg, env.mcp_config_path)
    t0 = time.monotonic()
    out, err, rc, timed_out = run_process(cmd, env.cwd, build_env(spec_, cfg), cfg.timeout_s)
    elapsed_ms = round((time.monotonic() - t0) * 1000)
    parsed = parse_stream(out.splitlines())
    status = classify(parsed, rc, timed_out, err)
    if out.strip():
        write_raw_stream(cfg.raw_dir, run_id, out)
    row: dict[str, Any] = {
        "status": status,
        "exit_code": rc,
        "pass": None,
        "grade_reason": None,
        "wall_ms": parsed.wall_ms if parsed.wall_ms is not None else elapsed_ms,
        "process_ms": elapsed_ms,
        "api_ms": parsed.api_ms,
        "ttft_ms": parsed.ttft_ms,
        "num_turns": parsed.num_turns,
        "model_resolved": parsed.model_resolved,
        "input_tokens": parsed.input_tokens,
        "output_tokens": parsed.output_tokens,
        "cache_read_tokens": parsed.cache_read_tokens,
        "cache_creation_tokens": parsed.cache_creation_tokens,
        "input_tokens_total": parsed.input_tokens_total,
        "first_request_input_tokens": parsed.first_request_input_tokens,
        "total_cost_usd": parsed.total_cost_usd,
        "tool_calls": [c.as_dict() for c in parsed.tool_calls],
        "n_tool_calls": len(parsed.tool_calls),
        "init_tool_count": len(parsed.tools),
        "init_tools_sha": hashlib.sha256("\n".join(sorted(parsed.tools)).encode()).hexdigest()[:12],
        "isolation_problems": isolation_problems(parsed, spec_.variant)
        if parsed.has_init
        else ["no init event"],
        "result_subtype": parsed.result_subtype,
        "error": None,
        "final_answer": parsed.final_text[:2000],
        **analyse_calls(parsed, spec_.task),
    }
    if status in INFRA_STATUSES or status == "timeout":
        row["error"] = _tail(err) or _tail(parsed.final_text) or status
    elif status != "ok":
        row["error"] = _tail(parsed.final_text) or status
    if spec_.task is not None and status in GRADED_STATUSES:
        try:
            ctx = GradeContext(Api(env.client()), load_fixtures(), parsed.final_text)
            grade = spec_.task.grader(ctx)
            row["pass"], row["grade_reason"] = grade.passed, grade.reason
        except Exception as exc:  # a grader or API problem is an infrastructure problem
            row["status"] = "infra_error"
            row["error"] = _tail(f"grader failed: {type(exc).__name__}: {exc}")
    return row


def execute_run(
    spec_: RunSpec, cfg: RunConfig, claude_version: str | None = None
) -> dict[str, Any]:
    """Run once, retrying only infrastructure failures (crash, rate limit, provisioning)."""
    retry_log: list[str] = []
    row: dict[str, Any] = {}
    for n in range(cfg.retries + 1):
        row = attempt(spec_, cfg, claude_version)
        if row["status"] not in INFRA_STATUSES or n == cfg.retries:
            break
        retry_log.append(f"{row['status']}: {row.get('error') or ''}"[:200])
        cfg.sleep(cfg.backoff_s * (2**n) * (3 if row["status"] == "rate_limited" else 1))
    row["retries"] = len(retry_log)
    row["retry_log"] = retry_log
    return row
