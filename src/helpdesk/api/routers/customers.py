from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from helpdesk.api.deps import Pagination, scoped, service
from helpdesk.api.routers._common import ERROR_RESPONSES, to_page
from helpdesk.api.schemas.commerce import OrderOut, RefundOut
from helpdesk.api.schemas.common import Paginated
from helpdesk.api.schemas.people import (
    CustomerCreate,
    CustomerMerge,
    CustomerOut,
    CustomerSummary,
    CustomerUpdate,
)
from helpdesk.api.schemas.support import TicketOut
from helpdesk.domain import CustomerTier
from helpdesk.services.customers import CustomerService

router = APIRouter(prefix="/customers", tags=["customers"], responses=ERROR_RESPONSES)
Svc = Annotated[CustomerService, Depends(service(CustomerService))]


@router.get(
    "",
    operation_id="customers_list",
    summary="List or search customers",
    description=(
        "Returns customers, newest first, one page at a time. Use `q` to search by name, email, "
        "company or customer id (case-insensitive substring). Use this to find a customer id "
        "before looking at their tickets or orders. Filter with `tier` (standard, plus, vip) or "
        "`status` (active, merged). Merged customers are duplicates folded into another record."
    ),
    response_model=Paginated[CustomerOut],
    dependencies=scoped("customers:read"),
)
async def customers_list(
    svc: Svc,
    page: Pagination,
    q: Annotated[str | None, Query(description="Search name, email, company or id.")] = None,
    tier: CustomerTier | None = None,
    status: Annotated[str | None, Query(description="`active` or `merged`.")] = None,
) -> Paginated[CustomerOut]:
    result = await svc.list(q=q, tier=tier, status=status, limit=page.limit, cursor=page.cursor)
    return to_page(result, CustomerOut)


@router.post(
    "",
    operation_id="customers_create",
    summary="Create a customer",
    description=(
        "Creates a customer record. Email must be unique; if a customer with that email already "
        "exists the call fails with 409, so search with `customers_list` first. Required: `name`, "
        "`email`. Optional: phone, company, tier, notes. Supports the Idempotency-Key header."
    ),
    response_model=CustomerOut,
    status_code=201,
    dependencies=scoped("customers:write"),
)
async def customers_create(body: CustomerCreate, svc: Svc) -> CustomerOut:
    return CustomerOut.model_validate(await svc.create(body.model_dump()))


@router.get(
    "/{customer_id}",
    operation_id="customers_get",
    summary="Get a customer by id",
    description=(
        "Returns one customer by id (for example `C-0311`). Use it to read contact details, "
        "tier and whether the record was merged into another customer (`merged_into_id`)."
    ),
    response_model=CustomerOut,
    dependencies=scoped("customers:read"),
)
async def customers_get(customer_id: str, svc: Svc) -> CustomerOut:
    return CustomerOut.model_validate(await svc.get(customer_id))


@router.patch(
    "/{customer_id}",
    operation_id="customers_update",
    summary="Update a customer",
    description=(
        "Partially updates a customer: send only the fields to change (name, email, phone, "
        "company, tier, notes). Merged customers cannot be edited (409); edit the surviving "
        "customer instead."
    ),
    response_model=CustomerOut,
    dependencies=scoped("customers:write"),
)
async def customers_update(customer_id: str, body: CustomerUpdate, svc: Svc) -> CustomerOut:
    return CustomerOut.model_validate(
        await svc.update(customer_id, body.model_dump(exclude_unset=True))
    )


@router.post(
    "/{customer_id}/merge",
    operation_id="customers_merge",
    summary="Merge a duplicate customer into another",
    description=(
        "Folds the customer in the path (the duplicate) into `target_customer_id` (the survivor). "
        "All tickets, orders and refunds move to the survivor; the duplicate is marked `merged`. "
        "Safe to repeat: merging the same pair again returns the survivor without changes. "
        "Returns the surviving customer. Supports the Idempotency-Key header."
    ),
    response_model=CustomerOut,
    dependencies=scoped("customers:write"),
)
async def customers_merge(customer_id: str, body: CustomerMerge, svc: Svc) -> CustomerOut:
    return CustomerOut.model_validate(await svc.merge(customer_id, body.target_customer_id))


@router.get(
    "/{customer_id}/summary",
    operation_id="customers_get_summary",
    summary="Get a customer with ticket and order counts",
    description=(
        "Returns the customer plus quick counts: total tickets, open tickets (new/open/pending) "
        "and orders. Use it for a fast overview before drilling into tickets or orders."
    ),
    response_model=CustomerSummary,
    dependencies=scoped("customers:read"),
)
async def customers_get_summary(customer_id: str, svc: Svc) -> CustomerSummary:
    customer, counts = await svc.summary(customer_id)
    return CustomerSummary(
        customer=CustomerOut.model_validate(customer),
        ticket_count=counts["tickets"],
        open_ticket_count=counts["open_tickets"],
        order_count=counts["orders"],
    )


@router.get(
    "/{customer_id}/tickets",
    operation_id="customers_list_tickets",
    summary="List a customer's tickets",
    description=(
        "Lists tickets opened by this customer, newest first. Use it to find 'the ticket this "
        "customer opened' or to see their history. Optional `status` filter "
        "(new, open, pending, solved, closed)."
    ),
    response_model=Paginated[TicketOut],
    dependencies=scoped("customers:read", "tickets:read"),
)
async def customers_list_tickets(
    customer_id: str, svc: Svc, page: Pagination, status: str | None = None
) -> Paginated[TicketOut]:
    result = await svc.list_tickets(
        customer_id, status=status, limit=page.limit, cursor=page.cursor
    )
    return to_page(result, TicketOut)


@router.get(
    "/{customer_id}/orders",
    operation_id="customers_list_orders",
    summary="List a customer's orders",
    description=(
        "Lists orders placed by this customer, most recent first. Use it to find the order to "
        "refund (check `status` and `delivered_at`). Optional `status` filter "
        "(placed, shipped, delivered, cancelled)."
    ),
    response_model=Paginated[OrderOut],
    dependencies=scoped("customers:read", "orders:read"),
)
async def customers_list_orders(
    customer_id: str, svc: Svc, page: Pagination, status: str | None = None
) -> Paginated[OrderOut]:
    result = await svc.list_orders(customer_id, status=status, limit=page.limit, cursor=page.cursor)
    return to_page(result, OrderOut)


@router.get(
    "/{customer_id}/refunds",
    operation_id="customers_list_refunds",
    summary="List a customer's refunds",
    description=(
        "Lists refunds raised for this customer's orders, newest first. Optional `status` filter "
        "(pending_approval, approved, rejected, cancelled)."
    ),
    response_model=Paginated[RefundOut],
    dependencies=scoped("customers:read", "refunds:read"),
)
async def customers_list_refunds(
    customer_id: str, svc: Svc, page: Pagination, status: str | None = None
) -> Paginated[RefundOut]:
    result = await svc.list_refunds(
        customer_id, status=status, limit=page.limit, cursor=page.cursor
    )
    return to_page(result, RefundOut)
