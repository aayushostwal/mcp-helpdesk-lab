"""Remove personal and machine-specific text from anything that is committed.

Applied to every raw stream and result row before it is written. Replaces home directories,
user names, temp directories, host names, e-mail addresses, API keys and session ids.
"""

from __future__ import annotations

import getpass
import os
import re
import socket
from pathlib import Path

_PATH_RULES: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"-private-var-folders-[A-Za-z0-9_-]*?-T-run-[A-Za-z0-9_-]*?-cwd"),
        "-tmp-run-xxxx-cwd",
    ),
    (re.compile(r"-var-folders-[A-Za-z0-9_-]*?-T-run-[A-Za-z0-9_-]*?-cwd"), "-tmp-run-xxxx-cwd"),
    (re.compile(r"/Users/[^/\s\"']+"), "/home/user"),
    (re.compile(r"/home/(?!user\b)[^/\s\"']+"), "/home/user"),
    (re.compile(r"C:\\Users\\[^\\\s\"']+"), "/home/user"),
    (re.compile(r"/private/var/folders/[^\s\"']*?/T(?=/|\b)"), "/tmp"),
    (re.compile(r"/var/folders/[^\s\"']*?/T(?=/|\b)"), "/tmp"),
    (re.compile(r"/private/tmp\b"), "/tmp"),
    (re.compile(r"/tmp/(?:claude|cc-socks)[^\s\"']*"), "/tmp/x"),
    (re.compile(r"run-[a-z0-9_]{6,}"), "run-xxxx"),
]
_TEXT_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"hd_[A-Za-z0-9_\-]{8,}"), "hd_REDACTED"),
    (re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{8,}"), "sk-ant-REDACTED"),
    (
        re.compile(
            r"[A-Za-z0-9._%+-]+@(?!helpdesk\.example\.com|example\.com)[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
        ),
        "user@example.invalid",
    ),
    (
        re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"),
        "00000000-0000-0000-0000-000000000000",
    ),
]


def _identity_terms() -> list[str]:
    terms: set[str] = set()
    for value in (getpass.getuser(), os.environ.get("USER", ""), Path.home().name):
        if len(value) >= 3:
            terms.add(value)
    host = socket.gethostname()
    for value in (host, host.split(".")[0]):
        if len(value) >= 3:
            terms.add(value)
    return sorted(terms, key=len, reverse=True)


def scrub_text(text: str, extra_terms: list[str] | None = None) -> str:
    """Return ``text`` with personal data replaced. Safe to apply more than once."""
    out = text
    for pattern, repl in _PATH_RULES:
        out = pattern.sub(repl, out)
    for pattern, repl in _TEXT_RULES:
        out = pattern.sub(repl, out)
    for term in [*_identity_terms(), *(extra_terms or [])]:
        out = re.sub(re.escape(term), "user", out, flags=re.I)
    return out
