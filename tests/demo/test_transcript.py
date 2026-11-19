from __future__ import annotations

import json

from demo.transcript import Call, build_transcript, classify_kind, compact_args

from tests.bench.conftest import DATA


def test_real_recorded_stream_gives_timed_calls() -> None:
    t = build_transcript((DATA / "search_call_close_ticket.jsonl").read_text())
    assert t.calls and t.calls[0].tool == "search_api" and t.calls[0].kind == "read"
    assert t.wall_ms and t.ttfc_ms is not None and 0 <= t.ttfc_ms < t.wall_ms
    assert all(c.ms is not None and c.ms >= 0 for c in t.calls)
    assert any(c.kind == "write" for c in t.calls)
    assert "T-1042" in t.final_text and t.texts
    starts = [c.start_ms for c in t.calls if c.start_ms is not None]
    assert starts == sorted(starts)
    assert t.calls[0].as_dict()["n"] == 1


def test_empty_stream_is_empty() -> None:
    t = build_transcript("")
    assert t.calls == [] and t.ttfc_ms is None and t.final_text == ""


def test_error_result_marks_the_call_as_error() -> None:
    lines = [
        {
            "type": "assistant",
            "timestamp": "2026-01-01T00:00:01.000Z",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "a",
                        "name": "mcp__helpdesk__refunds_create",
                        "input": {"x": 1},
                    }
                ]
            },
        },
        {
            "type": "user",
            "timestamp": "2026-01-01T00:00:01.500Z",
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": "a", "is_error": True, "content": "no"}
                ]
            },
        },
        {"type": "user", "timestamp": "2026-01-01T00:00:02.000Z", "message": {"content": "plain"}},
        {"type": "result", "duration_ms": 3000, "result": "done"},
    ]
    t = build_transcript("\n".join(json.dumps(x) for x in lines))
    assert t.calls[0].kind == "error" and t.calls[0].ms == 500
    assert t.ttfc_ms == 2000  # t0 = last stamp (2 s) minus the 3 s wall time
    assert t.wall_ms == 3000


def test_helpers() -> None:
    assert classify_kind("tickets_close", False, True) == "write"
    assert classify_kind("tickets_get", False, True) == "read"
    assert classify_kind(None, True, True) == "read"
    assert classify_kind("tickets_close", False, False) == "error"
    assert len(compact_args({"a": "x" * 500})) == 160
    assert isinstance(Call(1, "t", None, "read", True, "{}", 0, 1, 1, True).as_dict(), dict)
