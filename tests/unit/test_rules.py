"""Pure business rules: no database, no HTTP."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from helpdesk.errors import (
    InvalidTransitionError,
    RefundExceedsBalanceError,
    RefundNotEligibleError,
    ReopenWindowExpiredError,
)
from helpdesk.models import Order, SlaPolicy, Ticket
from helpdesk.models.base import cents, to_decimal
from helpdesk.services.refund_policy import check_refund_policy
from helpdesk.services.sla import compute_due_dates, evaluate_target, evaluate_ticket
from helpdesk.services.workflow import TRANSITIONS, check_reopen, check_transition, next_priority

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


# ------------------------------------------------------------------ status machine


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("new", "open"),
        ("open", "pending"),
        ("open", "solved"),
        ("pending", "open"),
        ("pending", "solved"),
        ("solved", "closed"),
    ],
)
def test_allowed_transitions(current: str, target: str) -> None:
    check_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("new", "solved"),
        ("new", "closed"),
        ("new", "pending"),
        ("open", "new"),
        ("open", "closed"),
        ("pending", "closed"),
        ("solved", "open"),
        ("solved", "pending"),
        ("closed", "open"),
        ("closed", "solved"),
        ("open", "open"),
    ],
)
def test_rejected_transitions(current: str, target: str) -> None:
    with pytest.raises(InvalidTransitionError) as exc:
        check_transition(current, target)
    assert exc.value.status_code == 409
    assert exc.value.extra["current"] == current


def test_every_status_is_covered() -> None:
    assert set(TRANSITIONS) == {"new", "open", "pending", "solved", "closed"}
    assert TRANSITIONS["closed"] == frozenset()


def test_reopen_window_boundaries() -> None:
    check_reopen("solved", NOW - timedelta(days=7), NOW, 7)  # exactly 7 days is still allowed
    with pytest.raises(ReopenWindowExpiredError):
        check_reopen("solved", NOW - timedelta(days=7, seconds=1), NOW, 7)
    with pytest.raises(ReopenWindowExpiredError):
        check_reopen("solved", None, NOW, 7)
    for status in ("new", "open", "pending", "closed"):
        with pytest.raises(InvalidTransitionError):
            check_reopen(status, NOW, NOW, 7)


def test_next_priority_saturates() -> None:
    assert [next_priority(p) for p in ("low", "normal", "high", "urgent")] == [
        "normal",
        "high",
        "urgent",
        "urgent",
    ]


# ------------------------------------------------------------------- refund policy


def make_order(
    status: str = "delivered",
    total: str = "100.00",
    delivered_days: float | None = 5,
    shipped_days: float | None = 6,
) -> Order:
    return Order(
        id="O-1",
        customer_id="C-1",
        status=status,
        currency="USD",
        total_cents=cents(Decimal(total)),
        placed_at=NOW - timedelta(days=10),
        shipped_at=NOW - timedelta(days=shipped_days) if shipped_days is not None else None,
        delivered_at=NOW - timedelta(days=delivered_days) if delivered_days is not None else None,
    )


def test_refund_ok_up_to_total() -> None:
    check_refund_policy(make_order(), 10000, 0, NOW, 30)
    check_refund_policy(make_order(), 4000, 6000, NOW, 30)


def test_refund_window_boundary() -> None:
    check_refund_policy(make_order(delivered_days=30), 100, 0, NOW, 30)
    with pytest.raises(RefundNotEligibleError):
        check_refund_policy(make_order(delivered_days=30.01), 100, 0, NOW, 30)


@pytest.mark.parametrize("status", ["placed", "cancelled"])
def test_refund_rejects_unfulfilled_orders(status: str) -> None:
    with pytest.raises(RefundNotEligibleError) as exc:
        check_refund_policy(
            make_order(status=status, delivered_days=None, shipped_days=None), 100, 0, NOW, 30
        )
    assert exc.value.extra["order_status"] == status


def test_shipped_order_uses_ship_date_for_window() -> None:
    shipped = make_order(status="shipped", delivered_days=None, shipped_days=3)
    check_refund_policy(shipped, 100, 0, NOW, 30)
    stale = make_order(status="shipped", delivered_days=None, shipped_days=45)
    with pytest.raises(RefundNotEligibleError):
        check_refund_policy(stale, 100, 0, NOW, 30)
    unknown = make_order(status="shipped", delivered_days=None, shipped_days=None)
    with pytest.raises(RefundNotEligibleError):
        check_refund_policy(unknown, 100, 0, NOW, 30)


def test_refund_balance_error_reports_remaining_amount() -> None:
    with pytest.raises(RefundExceedsBalanceError) as exc:
        check_refund_policy(make_order(), 5001, 5000, NOW, 30)
    assert exc.value.extra["refundable"] == "50.00"


def test_money_helpers_round_trip() -> None:
    assert cents(Decimal("19.99")) == 1999
    assert to_decimal(1999) == Decimal("19.99")
    assert cents(Decimal("0.10") * 3) == 30


# --------------------------------------------------------------------------- SLA


POLICY = SlaPolicy(
    id="SLA-1",
    name="n",
    priority="high",
    first_response_minutes=60,
    resolution_minutes=480,
    active=True,
    created_at=NOW,
)


def test_due_dates_from_policy() -> None:
    fr, res = compute_due_dates(NOW, POLICY)
    assert fr == NOW + timedelta(minutes=60) and res == NOW + timedelta(minutes=480)
    assert compute_due_dates(NOW, None) == (None, None)


def test_evaluate_target_states() -> None:
    due = NOW + timedelta(minutes=60)
    assert evaluate_target(None, None, NOW, None).state == "untracked"
    assert evaluate_target(due, None, NOW, 60).state == "pending"
    assert evaluate_target(due, None, NOW + timedelta(minutes=50), 60).state == "at_risk"
    assert evaluate_target(due, None, due, 60).state == "at_risk"  # on the line is not yet late
    assert evaluate_target(due, None, due + timedelta(minutes=1), 60).state == "breached"
    assert evaluate_target(due, None, due + timedelta(minutes=1), 60).minutes_remaining == -1
    assert evaluate_target(due, due, NOW + timedelta(days=9), 60).state == "met"
    assert evaluate_target(due, due + timedelta(seconds=1), NOW, 60).state == "breached"
    assert evaluate_target(due, None, NOW, None).state == "pending"


def test_evaluate_ticket_combines_targets() -> None:
    ticket = Ticket(
        id="T-1", priority="high", created_at=NOW, first_response_at=NOW + timedelta(minutes=10)
    )
    ticket.first_response_due_at, ticket.resolution_due_at = compute_due_dates(NOW, POLICY)
    early = evaluate_ticket(ticket, POLICY, NOW + timedelta(minutes=20))
    assert (
        early.first_response.state == "met"
        and early.resolution.state == "pending"
        and not early.breached
    )
    late = evaluate_ticket(ticket, POLICY, NOW + timedelta(minutes=500))
    assert late.breached and late.policy_id == "SLA-1"
    assert evaluate_ticket(ticket, None, NOW).policy_id is None
