"""Find which ``ENABLE_TOOL_SEARCH`` setting loads all tool definitions up front.

Runs "Reply with the word ok" against the per-endpoint server with 100 tools for each
combination of built-in tools (``""`` or ``ToolSearch``) and environment value, and records the
first-request input tokens. Deferred loading shows up as a first request far below the no-server
baseline plus the size of the tool list. The result is in docs/benchmark.md.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from bench.harness.runner import RunConfig, RunSpec, execute_run
from bench.harness.variants import ALL_VARIANTS

PROBE_CASES: tuple[tuple[str, str | None], ...] = (
    ("", None),
    ("", "false"),
    ("", "true"),
    ("", "force"),
    ("ToolSearch", None),
    ("ToolSearch", "false"),
    ("ToolSearch", "true"),
    ("ToolSearch", "auto"),
    ("ToolSearch", "force"),
)


def probe_variants() -> list[tuple[str, str | None, RunSpec]]:
    out: list[tuple[str, str | None, RunSpec]] = []
    for tools, env in PROBE_CASES:
        base = ALL_VARIANTS["per-endpoint-100"]
        variant = dataclasses.replace(
            base,
            name=f"probe[tools={tools or 'none'},env={env or 'unset'}]",
            tools_override=tools,
            env_override=env if env is not None else "",
        )
        out.append((tools, env, RunSpec(variant, "sonnet", None, 1)))
    return out


def run_probe(cfg: RunConfig, model: str = "sonnet") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for tools, env, spec in probe_variants():
        spec = dataclasses.replace(spec, model=model)
        row = execute_run(spec, cfg)
        row.update({"probe_tools": tools or "none", "probe_env": env or "unset"})
        rows.append(row)
    return rows
