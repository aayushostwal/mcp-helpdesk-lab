from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends

from helpdesk.api.deps import Pagination, scoped, service
from helpdesk.api.routers._common import ERROR_RESPONSES, to_page
from helpdesk.api.schemas.commerce import (
    OrderCreate,
    OrderDetail,
    OrderItemOut,
    OrderOut,
    RefundApprove,
    RefundCreate,
    RefundOut,
    RefundReject,
)
from helpdesk.api.schemas.common import Paginated
from helpdesk.domain import OrderStatus, RefundStatus
from helpdesk.services.orders import OrderService
from helpdesk.services.refunds import RefundService

orders_router = APIRouter(prefix="/orders", tags=["orders"], responses=ERROR_RESPONSES)
refunds_router = APIRouter(prefix="/refunds", tags=["refunds"], responses=ERROR_RESPONSES)
Orders = Annotated[OrderService, Depends(service(OrderService))]
Refunds = Annotated[RefundService, Depends(service(RefundService))]


@orders_router.get(
    "",
    operation_id="orders_list",
    summary="List or filter orders",
    description=(
        "Lists orders, most recent first. Filter by `customer_id`, `status` (placed, shipped, "
        "delivered, cancelled) and `placed_after` / `placed_before` (ISO 8601). Use "
        "`customers_list_orders` if you simply want one customer's orders."
    ),
    response_model=Paginated[OrderOut],
    dependencies=scoped("orders:read"),
)
async def orders_list(
    svc: Orders,
    page: Pagination,
    customer_id: str | None = None,
    status: OrderStatus | None = None,
    placed_after: datetime | None = None,
    placed_before: datetime | None = None,
) -> Paginated[OrderOut]:
    result = await svc.list(
        customer_id=customer_id,
        status=status,
        placed_after=placed_after,
        placed_before=placed_before,
        limit=page.limit,
        cursor=page.cursor,
    )
    return to_page(result, OrderOut)


@orders_router.post(
    "",
    operation_id="orders_create",
    summary="Create an order",
    description=(
        "Records an order for a customer with at least one line item "
        "(`sku`, `name`, `quantity`, `unit_price`). The total is computed. New orders are "
        "`placed`. Supports Idempotency-Key."
    ),
    response_model=OrderDetail,
    status_code=201,
    dependencies=scoped("orders:write"),
)
async def orders_create(body: OrderCreate, svc: Orders) -> OrderDetail:
    order = await svc.create(body.customer_id, body.currency, [i.model_dump() for i in body.items])
    return OrderDetail.model_validate(order)


@orders_router.get(
    "/{order_id}",
    operation_id="orders_get",
    summary="Get an order by id",
    description=(
        "Returns one order (for example `O-5520`) with status, total, key timestamps "
        "(`delivered_at` drives the refund window) and its line items."
    ),
    response_model=OrderDetail,
    dependencies=scoped("orders:read"),
)
async def orders_get(order_id: str, svc: Orders) -> OrderDetail:
    return OrderDetail.model_validate(await svc.get(order_id))


@orders_router.get(
    "/{order_id}/items",
    operation_id="orders_list_items",
    summary="List an order's line items",
    description="Returns the products on the order: sku, name, quantity, unit price and line total.",
    response_model=list[OrderItemOut],
    dependencies=scoped("orders:read"),
)
async def orders_list_items(order_id: str, svc: Orders) -> list[OrderItemOut]:
    order = await svc.get(order_id)
    return [OrderItemOut.model_validate(item) for item in order.item_rows]


@orders_router.get(
    "/{order_id}/refunds",
    operation_id="orders_list_refunds",
    summary="List refunds on an order",
    description=(
        "Returns every refund raised against the order, oldest first. Use it to work out how "
        "much is still refundable (order total minus approved and pending refunds)."
    ),
    response_model=list[RefundOut],
    dependencies=scoped("orders:read", "refunds:read"),
)
async def orders_list_refunds(order_id: str, svc: Orders) -> list[RefundOut]:
    return [RefundOut.model_validate(r) for r in await svc.list_refunds(order_id)]


@orders_router.post(
    "/{order_id}/ship",
    operation_id="orders_ship",
    summary="Mark an order shipped",
    description="Moves a `placed` order to `shipped` and stamps `shipped_at`. Other states: 409.",
    response_model=OrderOut,
    dependencies=scoped("orders:write"),
)
async def orders_ship(order_id: str, svc: Orders) -> OrderOut:
    return OrderOut.model_validate(await svc.ship(order_id))


@orders_router.post(
    "/{order_id}/deliver",
    operation_id="orders_deliver",
    summary="Mark an order delivered",
    description=(
        "Moves a `shipped` order to `delivered` and stamps `delivered_at`, which starts the "
        "30-day refund window. Other states: 409."
    ),
    response_model=OrderOut,
    dependencies=scoped("orders:write"),
)
async def orders_deliver(order_id: str, svc: Orders) -> OrderOut:
    return OrderOut.model_validate(await svc.deliver(order_id))


@orders_router.post(
    "/{order_id}/cancel",
    operation_id="orders_cancel",
    summary="Cancel an order",
    description=(
        "Cancels an order that has not shipped yet (`placed` only). Shipped or delivered orders "
        "cannot be cancelled (409); refund them instead with `refunds_create`."
    ),
    response_model=OrderOut,
    dependencies=scoped("orders:write"),
)
async def orders_cancel(order_id: str, svc: Orders) -> OrderOut:
    return OrderOut.model_validate(await svc.cancel(order_id))


@refunds_router.get(
    "",
    operation_id="refunds_list",
    summary="List refunds",
    description=(
        "Lists refunds, newest first. Filter by `status` (pending_approval, approved, rejected, "
        "cancelled), `order_id` or `customer_id`. Use `status=pending_approval` to see what is "
        "waiting for a lead."
    ),
    response_model=Paginated[RefundOut],
    dependencies=scoped("refunds:read"),
)
async def refunds_list(
    svc: Refunds,
    page: Pagination,
    status: RefundStatus | None = None,
    order_id: str | None = None,
    customer_id: str | None = None,
) -> Paginated[RefundOut]:
    result = await svc.list(
        status=status,
        order_id=order_id,
        customer_id=customer_id,
        limit=page.limit,
        cursor=page.cursor,
    )
    return to_page(result, RefundOut)


@refunds_router.post(
    "",
    operation_id="refunds_create",
    summary="Request a refund for an order",
    description=(
        "Creates a refund, enforcing the refund policy: the order must be delivered or shipped, "
        "within 30 days of delivery, and `amount` must not exceed the order total minus refunds "
        "already approved or pending. Violations return 422 (`refund-not-eligible` or "
        "`refund-exceeds-balance`). Amounts up to 500.00 are approved immediately; larger ones "
        "are created `pending_approval` and a lead must approve them with `refunds_approve`. "
        "Required: `order_id`, `amount`, `reason`. Optional `ticket_id`. Supports Idempotency-Key "
        "(strongly recommended: it prevents double refunds on retry)."
    ),
    response_model=RefundOut,
    status_code=201,
    dependencies=scoped("refunds:write"),
)
async def refunds_create(body: RefundCreate, svc: Refunds) -> RefundOut:
    refund = await svc.create(body.order_id, body.amount, body.reason, body.ticket_id)
    return RefundOut.model_validate(refund)


@refunds_router.get(
    "/{refund_id}",
    operation_id="refunds_get",
    summary="Get a refund by id",
    description="Returns one refund (for example `R-0042`) with amount, status (pending_approval, approved, rejected, cancelled) and who decided it and why.",
    response_model=RefundOut,
    dependencies=scoped("refunds:read"),
)
async def refunds_get(refund_id: str, svc: Refunds) -> RefundOut:
    return RefundOut.model_validate(await svc.get(refund_id))


@refunds_router.post(
    "/{refund_id}/approve",
    operation_id="refunds_approve",
    summary="Approve a pending refund",
    description=(
        "Approves a `pending_approval` refund. The API key must be linked to an agent with role "
        "`lead`, otherwise 403 `agent-role-required`. Refunds in any other status "
        "return 409. Optional `note`."
    ),
    response_model=RefundOut,
    dependencies=scoped("refunds:write"),
)
async def refunds_approve(
    refund_id: str, svc: Refunds, body: RefundApprove | None = None
) -> RefundOut:
    return RefundOut.model_validate(await svc.approve(refund_id, body.note if body else None))


@refunds_router.post(
    "/{refund_id}/reject",
    operation_id="refunds_reject",
    summary="Reject a pending refund",
    description=(
        "Rejects a `pending_approval` refund and releases the reserved amount. Requires a lead "
        "agent key (403 otherwise). `note` (the reason) is required. Other statuses: 409."
    ),
    response_model=RefundOut,
    dependencies=scoped("refunds:write"),
)
async def refunds_reject(refund_id: str, body: RefundReject, svc: Refunds) -> RefundOut:
    return RefundOut.model_validate(await svc.reject(refund_id, body.note))


@refunds_router.post(
    "/{refund_id}/cancel",
    operation_id="refunds_cancel",
    summary="Withdraw a pending refund request",
    description=(
        "Cancels a refund that is still `pending_approval` (for example it was raised by "
        "mistake). Any agent with refunds:write can do this; approved or rejected refunds "
        "return 409."
    ),
    response_model=RefundOut,
    dependencies=scoped("refunds:write"),
)
async def refunds_cancel(refund_id: str, svc: Refunds) -> RefundOut:
    return RefundOut.model_validate(await svc.cancel(refund_id))
