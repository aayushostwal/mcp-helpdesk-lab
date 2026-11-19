"""The refund policy as a pure function, so the rules can be tested without a database."""

from __future__ import annotations

from datetime import datetime, timedelta

from helpdesk.errors import RefundExceedsBalanceError, RefundNotEligibleError
from helpdesk.models import Order

REFUNDABLE_STATUSES = ("delivered", "shipped")


def refund_reference_time(order: Order) -> datetime | None:
    """Delivery time when known, otherwise the ship time (shipped orders have no delivery yet)."""
    return order.delivered_at or order.shipped_at


def check_refund_policy(
    order: Order,
    amount_cents: int,
    committed_cents: int,
    now: datetime,
    window_days: int,
) -> None:
    """Raise a policy error unless the refund is allowed.

    Rules: the order must be delivered or shipped, the refund must be requested within
    ``window_days`` of delivery, and the amount must fit in what has not been refunded yet.
    """
    if order.status not in REFUNDABLE_STATUSES:
        raise RefundNotEligibleError(
            f"Order '{order.id}' is '{order.status}'; only delivered or shipped orders can be "
            "refunded.",
            order_status=order.status,
        )
    reference = refund_reference_time(order)
    if reference is None or now - reference > timedelta(days=window_days):
        raise RefundNotEligibleError(
            f"Order '{order.id}' is outside the {window_days}-day refund window.",
            window_days=window_days,
            reference_time=reference.isoformat() if reference else None,
        )
    refundable = order.total_cents - committed_cents
    if amount_cents > refundable:
        raise RefundExceedsBalanceError(
            f"Requested {amount_cents / 100:.2f} but only {refundable / 100:.2f} is refundable "
            f"on order '{order.id}' (total {order.total_cents / 100:.2f}, "
            f"already refunded or pending {committed_cents / 100:.2f}).",
            refundable=f"{refundable / 100:.2f}",
        )
