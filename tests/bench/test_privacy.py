"""Committed benchmark output must not contain usernames, home paths, hosts or e-mail addresses."""

from __future__ import annotations

import getpass
import gzip
import re
import socket
from pathlib import Path

from bench.harness.paths import RESULTS_DIR
from demo.paths import RESULTS_DIR as DEMO_RESULTS_DIR

EMAIL = re.compile(
    r"[A-Za-z0-9._%+-]+@(?!helpdesk\.example\.com|example\.com|example\.invalid)[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
)
PATHS = re.compile(r"/Users/|/private/|/var/folders|C:\\Users|/home/(?!user\b)")
TOKEN = re.compile(r"\bhd_(?!REDACTED)[A-Za-z0-9_-]{8,}|sk-ant-(?!REDACTED)[A-Za-z0-9_-]{8,}")


def _texts(root: Path) -> list[tuple[Path, str]]:
    out: list[tuple[Path, str]] = []
    for path in sorted(root.rglob("*")):
        if path.suffix == ".gz":
            out.append((path, gzip.open(path, "rt", encoding="utf-8").read()))
        elif path.suffix in {".jsonl", ".json", ".csv", ".md"}:
            out.append((path, path.read_text(encoding="utf-8")))
    return out


def scan(root: Path) -> list[str]:
    terms = {
        t
        for t in (getpass.getuser(), Path.home().name, socket.gethostname().split(".")[0])
        if len(t) >= 3
    }
    problems: list[str] = []
    for path, text in _texts(root):
        lowered = text.lower()
        problems += [f"{path.name}: contains '{t}'" for t in terms if t.lower() in lowered]
        for name, pattern in (("e-mail", EMAIL), ("path", PATHS), ("token", TOKEN)):
            if pattern.search(text):
                problems.append(f"{path.name}: {name} pattern")
    return problems


def test_results_contain_no_personal_data() -> None:
    assert scan(RESULTS_DIR) == []


def test_demo_results_contain_no_personal_data() -> None:
    assert scan(DEMO_RESULTS_DIR) == []


def test_scanner_catches_leaks(tmp_path: Path) -> None:
    (tmp_path / "a.jsonl").write_text('{"cwd": "/Users/someone/x"}\n')
    (tmp_path / "b.md").write_text("write to boss@corp.io token hd_abcdefghijk")
    found = " ".join(scan(tmp_path))
    assert "path pattern" in found and "e-mail" in found and "token" in found
