"""A stand-in for ``claude`` used by ``--dry-run`` and the tests. It never calls a model.

Modes (environment variable ``BENCH_FAKE_MODE``):

* ``replay`` (default): print a recorded stream, unchanged.
* ``oracle``: first perform the scripted solution of the task named in ``BENCH_FAKE_TASK``
  against the API (found in the ``--mcp-config`` file), then print the recorded stream with the
  final answer replaced by the oracle's answer. This exercises the whole pipeline, graders
  included, without a model.
* ``crash``: exit with status 1 and no output. ``hang``: sleep until killed.
  ``ratelimit``: print a rate-limit style error result.

``BENCH_FAKE_STREAM`` selects the recorded stream (default: ``bench/harness/data/replay.stream.jsonl``).
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from bench.harness.api import Api
from bench.harness.grading import load_fixtures

DEFAULT_STREAM = Path(__file__).parent / "data" / "replay.stream.jsonl"


def _arg(argv: list[str], flag: str) -> str | None:
    return argv[argv.index(flag) + 1] if flag in argv else None


def _api_from_config(path: str) -> Api | None:
    servers = json.loads(Path(path).read_text()).get("mcpServers", {})
    if not servers:
        return None
    env = next(iter(servers.values()))["env"]
    client = httpx.Client(
        base_url=env["HELPDESK_MCP_API_URL"],
        headers={"Authorization": f"Bearer {env['HELPDESK_MCP_API_KEY']}"},
        timeout=30,
    )
    return Api(client)


def _replay(answer: str | None, *, server: bool = True) -> None:
    stream = Path(os.environ.get("BENCH_FAKE_STREAM", DEFAULT_STREAM))
    for line in stream.read_text().splitlines():
        if not line.strip():
            continue
        event: dict[str, Any] = json.loads(line)
        if not server and event.get("subtype") == "init":
            event["tools"], event["mcp_servers"] = [], []
        if answer is not None and event.get("type") == "result":
            event["result"] = answer
        print(json.dumps(event), flush=True)


def main(argv: list[str]) -> int:
    mode = os.environ.get("BENCH_FAKE_MODE", "replay")
    if mode == "crash":
        print("fake claude: simulated crash", file=sys.stderr)
        return 1
    if mode == "hang":
        time.sleep(3600)
        return 0
    if mode == "ratelimit":
        print(
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": True,
                    "api_error_status": 429,
                    "result": "API Error: 429 rate limit",
                    "num_turns": 1,
                }
            )
        )
        return 1
    answer: str | None = None
    config = _arg(argv, "--mcp-config")
    has_server = bool(config and json.loads(Path(config).read_text()).get("mcpServers"))
    if mode == "oracle":
        from bench.harness.tasks import load_tasks

        task_id = os.environ.get("BENCH_FAKE_TASK", "")
        tasks = {t.id: t for t in load_tasks()}
        if task_id in tasks and config:
            api = _api_from_config(config)
            if api is not None:
                answer = tasks[task_id].oracle(api, load_fixtures())
    _replay(answer, server=has_server)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
