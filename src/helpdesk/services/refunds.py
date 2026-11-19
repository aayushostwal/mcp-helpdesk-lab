"""Refunds: policy-checked creation and the lead approval workflow."""

from __future__ import annotations

from decimal import Decimal

from helpdesk.errors import (
    AgentRoleRequiredError,
    ConflictError,
    NotFoundError,
)
from helpdesk.models import Refund
from helpdesk.models.base import cents
from helpdesk.repositories.base import Page, next_id
from helpdesk.repositories.commerce import OrderRepository, RefundRepository
from helpdesk.repositories.support import TicketRepository
from helpdesk.services.base import BaseService, Context, snapshot
from helpdesk.services.refund_policy import check_refund_policy
from helpdesk.services.runtime_settings import load_effective

APPROVER_ROLE = "lead"


class RefundService(BaseService):
    def __init__(self, ctx: Context) -> None:
        super().__init__(ctx)
        self.refunds = RefundRepository(ctx.session)
        self.orders = OrderRepository(ctx.session)
        self.tickets = TicketRepository(ctx.session)

    async def get(self, refund_id: str) -> Refund:
        refund = await self.refunds.get(refund_id)
        if refund is None:
            raise NotFoundError("Refund", refund_id)
        return refund

    async def list(
        self,
        *,
        status: str | None,
        order_id: str | None,
        customer_id: str | None,
        limit: int,
        cursor: str | None,
    ) -> Page[Refund]:
        return await self.refunds.list(
            status=status, order_id=order_id, customer_id=customer_id, limit=limit, cursor=cursor
        )

    async def create(
        self, order_id: str, amount: Decimal, reason: str, ticket_id: str | None
    ) -> Refund:
        order = await self.orders.get(order_id)
        if order is None:
            raise NotFoundError("Order", order_id)
        if ticket_id and await self.tickets.get(ticket_id) is None:
            raise NotFoundError("Ticket", ticket_id)
        effective = await load_effective(self.session, self.ctx.settings)
        amount_cents = cents(amount)
        check_refund_policy(
            order,
            amount_cents,
            await self.refunds.committed_cents(order_id),
            self.now,
            effective.refund_window_days,
        )
        needs_approval = amount_cents > cents(effective.refund_approval_threshold)
        principal = self.ctx.principal
        refund = Refund(
            id=await next_id(self.session, "refund", "R"),
            order_id=order_id,
            customer_id=order.customer_id,
            ticket_id=ticket_id,
            amount_cents=amount_cents,
            reason=reason,
            status="pending_approval" if needs_approval else "approved",
            requested_by_agent_id=principal.agent_id if principal else None,
            created_at=self.now,
        )
        if not needs_approval:
            refund.decided_at = self.now
            refund.decision_note = "Auto-approved: at or below the approval threshold."
        self.refunds.add(refund)
        await self.session.flush()
        self.audit("refunds.create", "refund", refund.id, None, snapshot(refund))
        await self.commit()
        return refund

    def _require_approver(self) -> str:
        principal = self.ctx.principal
        if principal is None or principal.agent_id is None:
            raise AgentRoleRequiredError(
                "Deciding on a refund requires an API key linked to an agent with role 'lead'."
            )
        if principal.agent_role != APPROVER_ROLE:
            raise AgentRoleRequiredError(
                f"Agent '{principal.agent_id}' has role '{principal.agent_role}'; "
                "only a 'lead' can approve or reject refunds.",
                required_role=APPROVER_ROLE,
            )
        return principal.agent_id

    async def _decide(self, refund_id: str, status: str, note: str | None, action: str) -> Refund:
        approver = self._require_approver()
        refund = await self.get(refund_id)
        if refund.status != "pending_approval":
            raise ConflictError(
                f"Refund '{refund_id}' is '{refund.status}'; only pending_approval refunds can "
                f"be {action}.",
                current=refund.status,
            )
        before = snapshot(refund)
        refund.status = status
        refund.decided_by_agent_id = approver
        refund.decided_at = self.now
        refund.decision_note = note
        self.audit(f"refunds.{action}", "refund", refund.id, before, snapshot(refund))
        await self.commit()
        return refund

    async def approve(self, refund_id: str, note: str | None) -> Refund:
        return await self._decide(refund_id, "approved", note, "approve")

    async def reject(self, refund_id: str, note: str) -> Refund:
        return await self._decide(refund_id, "rejected", note, "reject")

    async def cancel(self, refund_id: str) -> Refund:
        """Withdraw a refund that is still waiting for approval (releases the reserved amount)."""
        refund = await self.get(refund_id)
        if refund.status != "pending_approval":
            raise ConflictError(
                f"Refund '{refund_id}' is '{refund.status}'; only pending_approval refunds can "
                "be cancelled.",
                current=refund.status,
            )
        before = snapshot(refund)
        refund.status = "cancelled"
        refund.decided_at = self.now
        self.audit("refunds.cancel", "refund", refund.id, before, snapshot(refund))
        await self.commit()
        return refund
