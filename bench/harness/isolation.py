"""Per-run isolation: a pristine seeded database copy, its own API process, a key and an MCP config.

The seed is built once into ``bench/.cache`` and copied for every run, so each run starts from
identical data and cannot see another run's writes. The MCP server itself is *not* started here:
Claude Code launches it from the generated config (stdio).
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from bench.harness.paths import CACHE_DIR, FROZEN_TIME

SEED_DB_NAME = "seed.db"
LEAD_AGENT = "A-0002"
MCP_SERVER_NAME = "helpdesk"


def _bin(name: str) -> str:
    """Console script from the same environment as the running interpreter."""
    candidate = Path(sys.executable).parent / name
    return str(candidate) if candidate.exists() else name


def clean_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """A small environment: no inherited HELPDESK_* or DATABASE_URL settings."""
    keep = {k: v for k, v in os.environ.items() if not k.startswith(("HELPDESK", "DATABASE_URL"))}
    keep.update(extra or {})
    return keep


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def ensure_seed_db(cache_dir: Path = CACHE_DIR, *, force: bool = False) -> Path:
    """Build the pristine seeded SQLite file once and return its path."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / SEED_DB_NAME
    if target.exists() and not force:
        return target
    tmp = cache_dir / f"{SEED_DB_NAME}.building"
    tmp.unlink(missing_ok=True)
    env = clean_env({"DATABASE_URL": f"sqlite:///{tmp}"})
    helpdesk = _bin("helpdesk")
    subprocess.run([helpdesk, "migrate"], env=env, check=True, capture_output=True)
    subprocess.run(
        [helpdesk, "seed", "--fixtures", str(cache_dir / "fixtures.generated.json")],
        env=env,
        check=True,
        capture_output=True,
    )
    tmp.replace(target)
    return target


def kill_group(proc: subprocess.Popen[bytes] | subprocess.Popen[str]) -> None:
    """Kill the process and everything it started (it runs in its own process group)."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(Exception):
        proc.wait(timeout=5)


@dataclass
class RunEnv:
    """Everything one run needs. Create with :func:`run_env`."""

    workdir: Path
    cwd: Path  # empty directory the agent process starts in
    db_path: Path
    port: int
    api_url: str
    api_key: str
    mcp_config_path: Path
    api_proc: subprocess.Popen[bytes] | None = field(default=None, repr=False)

    def client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.api_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=30,
        )


def mcp_config(
    *, command: str, args: list[str], api_url: str, api_key: str | None
) -> dict[str, object]:
    env = {"HELPDESK_MCP_API_URL": api_url, "HELPDESK_MCP_LOG_LEVEL": "WARNING"}
    if api_key:
        env["HELPDESK_MCP_API_KEY"] = api_key
    return {"mcpServers": {MCP_SERVER_NAME: {"command": command, "args": args, "env": env}}}


def wait_healthy(url: str, proc: subprocess.Popen[bytes], timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"API process exited early with code {proc.returncode}")
        try:
            if httpx.get(f"{url}/readyz", timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.15)
    raise RuntimeError("API did not become ready in time")


@contextlib.contextmanager
def run_env(
    mcp_args: list[str] | None,
    *,
    seed_db: Path | None = None,
    scratch: Path | None = None,
) -> Iterator[RunEnv]:
    """Provision a run. ``mcp_args`` None means no MCP server (baseline without a server).

    Everything lives under the system temp directory, never inside this repository: Claude Code
    adds git status and project files from its working directory to the context, and the agent
    must start from an empty directory that is not a git checkout.
    """
    workdir = Path(tempfile.mkdtemp(prefix="run-", dir=scratch))
    api_proc: subprocess.Popen[bytes] | None = None
    try:
        db = workdir / "helpdesk.db"
        shutil.copyfile(seed_db or ensure_seed_db(), db)
        cwd = workdir / "cwd"
        cwd.mkdir()
        port = free_port()
        base = {
            "DATABASE_URL": f"sqlite:///{db}",
            "HELPDESK_FROZEN_TIME": FROZEN_TIME,
            "HELPDESK_LOG_LEVEL": "WARNING",
            "HELPDESK_WEBHOOK_DELIVERY": "simulated",
        }
        env = clean_env(base)
        key = subprocess.run(
            [
                _bin("helpdesk"),
                "create-api-key",
                "--name",
                "bench",
                "--scopes",
                "admin",
                "--agent",
                LEAD_AGENT,
            ],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        api_proc = subprocess.Popen(
            [_bin("helpdesk"), "serve", "--port", str(port)],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        url = f"http://127.0.0.1:{port}"
        wait_healthy(url, api_proc)
        cfg_path = workdir / "mcp.json"
        args = mcp_args if mcp_args is not None else []
        cfg = mcp_config(command=_bin("helpdesk-mcp"), args=args, api_url=url, api_key=key)
        if mcp_args is None:
            cfg = {"mcpServers": {}}
        cfg_path.write_text(json.dumps(cfg))
        yield RunEnv(workdir, cwd, db, port, url, key, cfg_path, api_proc)
    finally:
        if api_proc is not None:
            kill_group(api_proc)
        shutil.rmtree(workdir, ignore_errors=True)
