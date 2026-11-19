"""Grader plumbing: the context a grader sees, the registry, and small shared checks.

A grader looks at the database through the API (and at the agent's final answer for read tasks)
and returns :class:`Grade`. Rules every grader follows:

* Extra *reads* never matter (they leave no trace).
* Any *write* the task does not allow fails the run. Writes are found through the audit log:
  the seeded database has no audit events, so every event is something the agent did.
* No LLM judge. Answers are checked with plain string and number matching.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, NamedTuple

from bench.harness.api import Api
from bench.harness.paths import FIXTURES


class Grade(NamedTuple):
    passed: bool
    reason: str


@dataclass
class GradeContext:
    api: Api
    fixtures: dict[str, Any]
    answer: str = ""
    _writes: list[dict[str, Any]] | None = field(default=None, repr=False)

    @property
    def writes(self) -> list[dict[str, Any]]:
        if self._writes is None:
            self._writes = self.api.agent_writes()
        return self._writes

    @property
    def scenarios(self) -> dict[str, Any]:
        return dict(self.fixtures["scenarios"])


Grader = Callable[[GradeContext], Grade]
Oracle = Callable[[Api, dict[str, Any]], str]

GRADERS: dict[str, Grader] = {}
ORACLES: dict[str, Oracle] = {}


def grader(name: str) -> Callable[[Grader], Grader]:
    def register(fn: Grader) -> Grader:
        GRADERS[name] = fn
        return fn

    return register


def oracle(name: str) -> Callable[[Oracle], Oracle]:
    def register(fn: Oracle) -> Oracle:
        ORACLES[name] = fn
        return fn

    return register


def load_fixtures(path: Path = FIXTURES) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text())
    return data


# ------------------------------------------------------------------ shared checks
def only_writes(ctx: GradeContext, allowed: set[str], required: set[str] | None = None) -> Grade:
    """Pass when every write is in ``allowed`` and every ``required`` action happened."""
    actions = [str(e["action"]) for e in ctx.writes]
    extra = sorted({a for a in actions if a not in allowed})
    if extra:
        return Grade(False, f"unexpected write(s): {', '.join(extra)}")
    missing = sorted((required or set()) - set(actions))
    if missing:
        return Grade(False, f"missing write(s): {', '.join(missing)}")
    return Grade(True, "ok")


def no_writes(ctx: GradeContext) -> Grade:
    return only_writes(ctx, set())


def combine(*checks: Grade) -> Grade:
    for check in checks:
        if not check.passed:
            return check
    return Grade(True, "ok")


def contains_any(text: str, needles: list[str]) -> bool:
    lowered = text.lower()
    return any(n.lower() in lowered for n in needles)


def has_number(text: str, number: int | str | Decimal) -> bool:
    """True when ``number`` appears as a whole number in ``text`` (thousands commas ignored)."""
    cleaned = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)
    return re.search(rf"(?<![\d.]){re.escape(str(number))}(?![\d]|\.\d)", cleaned) is not None
