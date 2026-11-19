"""Measure how big each MCP tool design is, as a markdown table.

Starts ``helpdesk-mcp`` over stdio once per variant, lists its tools with the MCP client, and
reports the tool count, the byte size of the serialised tool list and an approximate token count.

The token count is ``bytes / 4``. That is a rough rule of thumb for English text and JSON, not a
tokenizer: phase 3 measures real tokens, and this table is only a sanity check for them.

    uv run python scripts/mcp_token_report.py                      # print to stdout
    uv run python scripts/mcp_token_report.py -o docs/tool-list-sizes.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from mcp.client.stdio import stdio_client

from mcp import ClientSession, StdioServerParameters

CONFIGS: list[tuple[str, list[str]]] = [
    ("per-endpoint, 10 tools", ["--variant", "per-endpoint", "--tool-count", "10"]),
    ("per-endpoint, 25 tools", ["--variant", "per-endpoint", "--tool-count", "25"]),
    ("per-endpoint, 50 tools", ["--variant", "per-endpoint", "--tool-count", "50"]),
    ("per-endpoint, 100 tools", ["--variant", "per-endpoint", "--tool-count", "100"]),
    ("per-endpoint, all tools", ["--variant", "per-endpoint"]),
    ("toolsets", ["--variant", "toolsets"]),
    ("search-call", ["--variant", "search-call"]),
]


@dataclass(frozen=True)
class Row:
    label: str
    tools: int
    full_bytes: int
    model_bytes: int


def approx_tokens(size: int) -> int:
    return round(size / 4)


async def measure(label: str, args: list[str]) -> Row:
    env = {
        **os.environ,
        "HELPDESK_MCP_API_URL": "http://127.0.0.1:9",  # no API calls are made
        "HELPDESK_MCP_API_KEY": "unused",
    }
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "helpdesk_mcp", *args], env=env
    )
    with Path(os.devnull).open("w") as errlog:  # noqa: ASYNC230
        async with (
            stdio_client(params, errlog=errlog) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            tools = (await session.list_tools()).tools
    full = [t.model_dump(mode="json", exclude_none=True, by_alias=True) for t in tools]
    # What a client normally puts in the model's context: name, description, input schema.
    visible = [
        {
            "name": t["name"],
            "description": t.get("description", ""),
            "input_schema": t["inputSchema"],
        }
        for t in full
    ]
    return Row(
        label,
        len(tools),
        len(_dump(full).encode()),
        len(_dump(visible).encode()),
    )


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def render(rows: list[Row]) -> str:
    lines = [
        "# Tool list sizes",
        "",
        "Size of the serialised `tools/list` result for each variant, measured by "
        "`scripts/mcp_token_report.py`. Regenerate with `make tool-sizes`.",
        "",
        "- **Tokens are approximate: bytes / 4.** They are not tokenizer counts. Phase 3 measures "
        "the real numbers; use this table as a sanity check against them.",
        "- *Full list* is the whole `tools/list` JSON, annotations included. *Model-visible* keeps "
        "only name, description and input schema, which is what a client normally shows the model.",
        "- Server instructions, resources and prompts are not included here.",
        "",
        "| Variant | Tools | Full list (bytes) | Full list (~tokens) "
        "| Model-visible (bytes) | Model-visible (~tokens) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(
            f"| {r.label} | {r.tools} | {r.full_bytes:,} | {approx_tokens(r.full_bytes):,} "
            f"| {r.model_bytes:,} | {approx_tokens(r.model_bytes):,} |"
        )
    return "\n".join(lines) + "\n"


async def collect() -> list[Row]:
    return [await measure(label, args) for label, args in CONFIGS]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("-o", "--output", type=Path, help="write the markdown here")
    options = parser.parse_args()
    text = render(asyncio.run(collect()))
    if options.output:
        options.output.write_text(text, encoding="utf-8")
        print(f"Wrote {options.output}")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
