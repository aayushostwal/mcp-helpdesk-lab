"""A stand-in for ``claude`` for the demo's dry run and tests. It never calls a model.

Like the benchmark's fake (``bench.harness.fake_claude``) but looks tasks up in the demo's list,
so ``oracle`` mode performs the scripted solution of the demo task named in ``BENCH_FAKE_TASK``.
"""

from __future__ import annotations

import os
import sys

from bench.harness import fake_claude as base
from bench.harness.grading import load_fixtures

from demo.tasks import load_demo_tasks


def main(argv: list[str]) -> int:
    if os.environ.get("BENCH_FAKE_MODE", "replay") == "oracle":
        config = base._arg(argv, "--mcp-config")
        tasks = {t.id: t for t in load_demo_tasks()}
        task = tasks.get(os.environ.get("BENCH_FAKE_TASK", ""))
        answer = None
        if task is not None and config:
            api = base._api_from_config(config)
            if api is not None:
                answer = task.oracle(api, load_fixtures())
        base._replay(answer, server=bool(config))
        return 0
    return base.main(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
