"""Domain vocabulary shared by services and API schemas."""

from __future__ import annotations

from typing import Literal

Priority = Literal["low", "normal", "high", "urgent"]
TicketStatus = Literal["new", "open", "pending", "solved", "closed"]
Channel = Literal["email", "web", "chat", "phone"]
AgentRole = Literal["agent", "lead", "admin"]
CustomerTier = Literal["standard", "plus", "vip"]
OrderStatus = Literal["placed", "shipped", "delivered", "cancelled"]
RefundStatus = Literal["pending_approval", "approved", "rejected", "cancelled"]
ArticleStatus = Literal["draft", "published"]

PRIORITIES: tuple[str, ...] = ("low", "normal", "high", "urgent")
ACTIVE_STATUSES: tuple[str, ...] = ("new", "open", "pending")
MAX_ESCALATION_LEVEL = 3

WEBHOOK_EVENTS: tuple[str, ...] = (
    "ticket.created",
    "ticket.updated",
    "ticket.escalated",
    "ticket.closed",
    "refund.created",
    "refund.approved",
    "refund.rejected",
    "webhook.test",
)

WebhookEvent = Literal[
    "ticket.created",
    "ticket.updated",
    "ticket.escalated",
    "ticket.closed",
    "refund.created",
    "refund.approved",
    "refund.rejected",
    "webhook.test",
]
