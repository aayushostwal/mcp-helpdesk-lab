"""Pure business rules: the ticket status machine and reopen window. No I/O, easy to test."""

from __future__ import annotations

from datetime import datetime, timedelta

from helpdesk.domain import PRIORITIES
from helpdesk.errors import InvalidTransitionError, ReopenWindowExpiredError

TRANSITIONS: dict[str, frozenset[str]] = {
    "new": frozenset({"open"}),
    "open": frozenset({"pending", "solved"}),
    "pending": frozenset({"open", "solved"}),
    "solved": frozenset({"closed"}),  # solved -> open only through the reopen operation
    "closed": frozenset(),
}

CLOSEABLE = frozenset({"open", "pending", "solved"})


def check_transition(current: str, target: str) -> None:
    if target in TRANSITIONS.get(current, frozenset()):
        return
    allowed = sorted(TRANSITIONS.get(current, frozenset()))
    hint = ""
    if current == "solved" and target == "open":
        hint = " Use the reopen operation for solved tickets."
    raise InvalidTransitionError(
        f"A ticket in status '{current}' cannot move to '{target}'. "
        f"Allowed next statuses: {allowed or 'none'}.{hint}",
        current=current,
        target=target,
        allowed=allowed,
    )


def check_reopen(status: str, solved_at: datetime | None, now: datetime, window_days: int) -> None:
    if status != "solved":
        raise InvalidTransitionError(
            f"Only solved tickets can be reopened; this ticket is '{status}'.", current=status
        )
    if solved_at is None or now - solved_at > timedelta(days=window_days):
        raise ReopenWindowExpiredError(
            f"Tickets can only be reopened within {window_days} days of being solved.",
            window_days=window_days,
        )


def next_priority(priority: str) -> str:
    index = PRIORITIES.index(priority)
    return PRIORITIES[min(index + 1, len(PRIORITIES) - 1)]
