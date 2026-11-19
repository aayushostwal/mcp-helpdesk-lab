"""Turn one recorded ``stream-json`` run into a timeline: what the agent said and called, when.

Time zero is the start of the CLI's own clock: the last stream timestamp minus the ``duration_ms``
the result event reports. That puts every offset within a few hundred milliseconds of the truth
(the final event is written right before the result event). A tool call starts when the model's
``tool_use`` block is emitted and ends when its ``tool_result`` arrives, so its duration is the
server round trip, not the model's thinking time before it.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from bench.harness.stream import MCP_PREFIX, operation_for, read_events
from bench.harness.tasks import operation_is_write

ARG_LIMIT = 160


@dataclass
class Call:
    n: int
    tool: str
    operation_id: str | None
    kind: str  # read | write | error
    meta: bool
    args: str
    start_ms: int | None
    end_ms: int | None
    ms: int | None
    ok: bool | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Transcript:
    calls: list[Call] = field(default_factory=list)
    texts: list[tuple[int | None, str]] = field(default_factory=list)  # (offset ms, text)
    final_text: str = ""
    wall_ms: int | None = None
    ttfc_ms: int | None = None  # time to first tool call


def _ts(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def compact_args(args: dict[str, Any], limit: int = ARG_LIMIT) -> str:
    text = json.dumps(args, ensure_ascii=False, separators=(",", ":"))
    return text if len(text) <= limit else text[: limit - 1] + "…"


def classify_kind(op: str | None, meta: bool, ok: bool | None) -> str:
    if ok is False:
        return "error"
    if meta or op is None:
        return "read"
    return "write" if operation_is_write(op) else "read"


def build_transcript(stream_text: str) -> Transcript:
    events = read_events(stream_text.splitlines())
    result: dict[str, Any] = next((e for e in reversed(events) if e.get("type") == "result"), {})
    stamps = [t for t in (_ts(e.get("timestamp")) for e in events) if t is not None]
    wall_ms = result.get("duration_ms")
    t0 = (max(stamps) - wall_ms / 1000) if stamps and isinstance(wall_ms, int) else None

    def off(stamp: float | None) -> int | None:
        return None if stamp is None or t0 is None else max(0, round((stamp - t0) * 1000))

    out = Transcript(final_text=str(result.get("result") or ""), wall_ms=wall_ms)
    open_calls: dict[str, Call] = {}
    for ev in events:
        stamp = off(_ts(ev.get("timestamp")))
        content = (ev.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if ev.get("type") == "assistant" and block.get("type") == "text":
                text = str(block.get("text", "")).strip()
                if text:
                    out.texts.append((stamp, text))
            elif ev.get("type") == "assistant" and block.get("type") == "tool_use":
                name = str(block.get("name", ""))
                raw = block.get("input")
                args: dict[str, Any] = raw if isinstance(raw, dict) else {}
                op, meta = operation_for(name, args)
                call = Call(
                    len(out.calls) + 1,
                    name.removeprefix(MCP_PREFIX),
                    op,
                    classify_kind(op, meta, None),
                    meta,
                    compact_args(args),
                    stamp,
                    None,
                    None,
                    None,
                )
                out.calls.append(call)
                open_calls[str(block.get("id"))] = call
            elif ev.get("type") == "user" and block.get("type") == "tool_result":
                done = open_calls.pop(str(block.get("tool_use_id")), None)
                if done is None:
                    continue
                call = done
                call.ok = not bool(block.get("is_error"))
                call.end_ms = stamp
                if call.start_ms is not None and stamp is not None:
                    call.ms = max(0, stamp - call.start_ms)
                call.kind = classify_kind(call.operation_id, call.meta, call.ok)
    out.ttfc_ms = out.calls[0].start_ms if out.calls else None
    return out
