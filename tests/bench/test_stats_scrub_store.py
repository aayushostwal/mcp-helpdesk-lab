from __future__ import annotations

import getpass
import gzip
import json
from pathlib import Path

import pytest
from bench.harness.scrub import scrub_text
from bench.harness.stats import median, quantile, wilson_interval
from bench.harness.store import append_row, read_raw_stream, read_rows, write_raw_stream


def test_wilson_interval_known_values() -> None:
    lo, hi = wilson_interval(8, 10)
    assert lo == pytest.approx(0.4902, abs=1e-3) and hi == pytest.approx(0.9433, abs=1e-3)
    lo, hi = wilson_interval(0, 5)
    assert lo == 0.0 and hi == pytest.approx(0.4345, abs=1e-3)
    lo, hi = wilson_interval(5, 5)
    assert hi == 1.0 and lo == pytest.approx(0.5655, abs=1e-3)
    assert wilson_interval(0, 0) == (0.0, 1.0)
    lo, hi = wilson_interval(50, 100)
    assert lo < 0.5 < hi and hi - lo < 0.21


def test_median_and_quantile() -> None:
    assert median([]) is None and median([3, 1, 2]) == 2.0
    assert quantile([], 0.5) is None
    assert quantile([1, 2, 3, 4], 0.5) == 2.5
    assert quantile([1, 2, 3, 4], 0.0) == 1 and quantile([1, 2, 3, 4], 1.0) == 4


def test_scrub_removes_personal_data() -> None:
    user = getpass.getuser()
    text = (
        f"/Users/{user}/work/x /home/someone/y /private/var/folders/ab/cd_ef/T/run-abc123xy/cwd "
        "/private/tmp/foo token hd_abcdefgh12345 sk-ant-abcdefgh1234 mail me@corp.io "
        "ok seed@helpdesk.example.com 123e4567-e89b-12d3-a456-426614174000 "
        "memory -private-var-folders-c--42-xyz-T-run-ab-cd-cwd"
    )
    out = scrub_text(text, ["secretword"])
    assert user.lower() not in out.lower()
    for needle in (
        "/Users/",
        "someone",
        "/private/",
        "var/folders",
        "abcdefgh12345",
        "me@corp.io",
        "123e4567",
    ):
        assert needle not in out
    assert "seed@helpdesk.example.com" in out
    assert scrub_text(out) == out  # idempotent
    assert "secretword" not in scrub_text("a secretword b", ["secretword"])


def test_store_roundtrip_and_scrubbing(tmp_path: Path) -> None:
    path = tmp_path / "runs.jsonl"
    assert read_rows(path) == []
    append_row(path, {"a": 1, "path": "/Users/alice/x"})
    append_row(path, {"a": 2})
    rows = read_rows(path)
    assert [r["a"] for r in rows] == [1, 2] and "alice" not in json.dumps(rows)
    stream = write_raw_stream(tmp_path / "raw", "run1", '{"cwd": "/Users/alice/p"}\n')
    assert stream.name == "run1.stream.jsonl.gz"
    assert "alice" not in read_raw_stream(stream)
    assert gzip.open(stream, "rt").read().startswith("{")
