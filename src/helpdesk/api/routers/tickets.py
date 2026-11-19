from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from helpdesk.api.deps import Pagination, scoped, service
from helpdesk.api.routers._common import ERROR_RESPONSES, to_page
from helpdesk.api.schemas.common import Paginated
from helpdesk.api.schemas.config import SlaEvaluationOut, TargetStatusOut
from helpdesk.api.schemas.platform import AuditEventOut
from helpdesk.api.schemas.support import (
    CommentCreate,
    CommentOut,
    CsatOut,
    CsatSubmit,
    StatusChange,
    TagCreate,
    TagOut,
    TicketAssign,
    TicketClose,
    TicketCreate,
    TicketEscalate,
    TicketMerge,
    TicketOut,
    TicketReopen,
    TicketTagAdd,
    TicketUpdate,
)
from helpdesk.domain import Priority, TicketStatus
from helpdesk.repositories.support import TicketFilters
from helpdesk.services.comments import CommentService
from helpdesk.services.sla import SlaService
from helpdesk.services.tags import TagService
from helpdesk.services.tickets import TicketService

router = APIRouter(prefix="/tickets", tags=["tickets"], responses=ERROR_RESPONSES)
comments_router = APIRouter(tags=["comments"], responses=ERROR_RESPONSES)
tags_router = APIRouter(prefix="/tags", tags=["tags"], responses=ERROR_RESPONSES)

Tickets = Annotated[TicketService, Depends(service(TicketService))]
Comments = Annotated[CommentService, Depends(service(CommentService))]
Tags = Annotated[TagService, Depends(service(TagService))]
Sla = Annotated[SlaService, Depends(service(SlaService))]


@router.get(
    "",
    operation_id="tickets_list",
    summary="List and filter tickets",
    description=(
        "Lists tickets, newest first, one page at a time. Combine filters freely: `status` "
        "(new, open, pending, solved, closed), `priority`, `assignee_id`, `unassigned=true`, "
        "`team_id`, `customer_id`, `tag`, `sla_breached` (true = missed an SLA target), "
        "`created_after` / `created_before` (ISO 8601) and `q` (substring of subject, "
        "description or ticket id). Use this to find ticket ids; use `tickets_get` for one ticket."
    ),
    response_model=Paginated[TicketOut],
    dependencies=scoped("tickets:read"),
)
async def tickets_list(
    svc: Tickets,
    page: Pagination,
    status: TicketStatus | None = None,
    priority: Priority | None = None,
    assignee_id: str | None = None,
    unassigned: Annotated[bool, Query(description="Only tickets with no assignee.")] = False,
    team_id: str | None = None,
    customer_id: str | None = None,
    tag: Annotated[str | None, Query(description="Exact tag name.")] = None,
    sla_breached: Annotated[
        bool | None, Query(description="true: breached an SLA target; false: has not.")
    ] = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    q: Annotated[str | None, Query(description="Search subject, description or id.")] = None,
) -> Paginated[TicketOut]:
    filters = TicketFilters(
        status=status,
        priority=priority,
        assignee_id=assignee_id,
        unassigned=unassigned,
        team_id=team_id,
        customer_id=customer_id,
        tag=tag,
        sla_breached=sla_breached,
        created_after=created_after,
        created_before=created_before,
        q=q,
    )
    return to_page(await svc.list(filters, limit=page.limit, cursor=page.cursor), TicketOut)


@router.post(
    "",
    operation_id="tickets_create",
    summary="Create a ticket",
    description=(
        "Opens a ticket for an existing customer. Required: `subject`, `description`, "
        "`customer_id`. Optional: `priority` (default normal), `channel`, `assignee_id`, "
        "`team_id`, `order_id` (must belong to the customer) and `tags`. New tickets start in "
        "status `new` (or `open` if assigned) and get SLA due times from the priority's policy. "
        "Supports the Idempotency-Key header."
    ),
    response_model=TicketOut,
    status_code=201,
    dependencies=scoped("tickets:write"),
)
async def tickets_create(body: TicketCreate, svc: Tickets) -> TicketOut:
    return TicketOut.model_validate(await svc.create(body.model_dump()))


@router.get(
    "/{ticket_id}",
    operation_id="tickets_get",
    summary="Get a ticket by id",
    description=(
        "Returns one ticket (for example `T-1042`) with status, priority, assignee, tags, "
        "customer and SLA due times. For the conversation use `comments_list`; for SLA state "
        "use `sla_evaluate_ticket`."
    ),
    response_model=TicketOut,
    dependencies=scoped("tickets:read"),
)
async def tickets_get(ticket_id: str, svc: Tickets) -> TicketOut:
    return TicketOut.model_validate(await svc.get(ticket_id))


@router.patch(
    "/{ticket_id}",
    operation_id="tickets_update",
    summary="Edit ticket fields",
    description=(
        "Partially updates `subject`, `description`, `priority`, `channel` or `order_id`. "
        "Changing priority recalculates the SLA due times. Does not change status or assignee: "
        "use `tickets_change_status` and `tickets_assign`. Closed tickets return 409."
    ),
    response_model=TicketOut,
    dependencies=scoped("tickets:write"),
)
async def tickets_update(ticket_id: str, body: TicketUpdate, svc: Tickets) -> TicketOut:
    return TicketOut.model_validate(
        await svc.update(ticket_id, body.model_dump(exclude_unset=True))
    )


@router.post(
    "/{ticket_id}/assign",
    operation_id="tickets_assign",
    summary="Assign a ticket to an agent and/or team",
    description=(
        "Sets who owns the ticket. Send `assignee_id` (agent), `team_id`, or both; send null to "
        "clear one; omit a field to leave it unchanged. Assigning a `new` ticket moves it to "
        "`open`. Use this to reassign. To raise urgency instead, use `tickets_escalate`."
    ),
    response_model=TicketOut,
    dependencies=scoped("tickets:write"),
)
async def tickets_assign(ticket_id: str, body: TicketAssign, svc: Tickets) -> TicketOut:
    return TicketOut.model_validate(
        await svc.assign(ticket_id, body.model_dump(exclude_unset=True))
    )


@router.post(
    "/{ticket_id}/status",
    operation_id="tickets_change_status",
    summary="Move a ticket to a new status",
    description=(
        "Applies one step of the status machine: new->open, open->pending, open->solved, "
        "pending->open, pending->solved, solved->closed. Anything else returns 409 with the "
        "allowed next statuses. Solved tickets come back with `tickets_reopen`, not this call. "
        "Optional `note` is saved as an internal note. To solve and close in one go use "
        "`tickets_close`."
    ),
    response_model=TicketOut,
    dependencies=scoped("tickets:write"),
)
async def tickets_change_status(ticket_id: str, body: StatusChange, svc: Tickets) -> TicketOut:
    return TicketOut.model_validate(await svc.change_status(ticket_id, body.status, body.note))


@router.post(
    "/{ticket_id}/close",
    operation_id="tickets_close",
    summary="Close a ticket",
    description=(
        "Solves and closes the ticket in one step. Allowed from open, pending or solved; a `new` "
        "ticket must be opened first (409). Optional `note` is stored as an internal note "
        "(use it for 'resolved by phone' style remarks). Closed tickets cannot be edited or "
        "reopened."
    ),
    response_model=TicketOut,
    dependencies=scoped("tickets:write"),
)
async def tickets_close(ticket_id: str, svc: Tickets, body: TicketClose | None = None) -> TicketOut:
    return TicketOut.model_validate(await svc.close(ticket_id, body.note if body else None))


@router.post(
    "/{ticket_id}/reopen",
    operation_id="tickets_reopen",
    summary="Reopen a solved ticket",
    description=(
        "Moves a `solved` ticket back to `open`. Allowed only within 7 days of being solved "
        "(configurable); later attempts return 409 `reopen-window-expired`, and tickets in any "
        "other status return 409. `reason` is required and saved as an internal note."
    ),
    response_model=TicketOut,
    dependencies=scoped("tickets:write"),
)
async def tickets_reopen(ticket_id: str, body: TicketReopen, svc: Tickets) -> TicketOut:
    return TicketOut.model_validate(await svc.reopen(ticket_id, body.reason))


@router.post(
    "/{ticket_id}/escalate",
    operation_id="tickets_escalate",
    summary="Escalate a ticket",
    description=(
        "Raises urgency: increases the escalation level (max 3), bumps priority one step "
        "(low->normal->high->urgent) which tightens SLA targets, and records `reason` as an "
        "internal note. Optional `team_id` routes it to another team. Not allowed on solved or "
        "closed tickets. This does NOT change the assignee; to hand the ticket to someone else "
        "use `tickets_assign`."
    ),
    response_model=TicketOut,
    dependencies=scoped("tickets:write"),
)
async def tickets_escalate(ticket_id: str, body: TicketEscalate, svc: Tickets) -> TicketOut:
    return TicketOut.model_validate(await svc.escalate(ticket_id, body.reason, body.team_id))


@router.post(
    "/{ticket_id}/merge",
    operation_id="tickets_merge",
    summary="Merge a duplicate ticket into another",
    description=(
        "Merges the ticket in the path (the duplicate) into `target_ticket_id`. Comments and tags "
        "move to the target and the duplicate is closed with `merged_into_id` set. Both tickets "
        "must belong to the same customer. Safe to repeat. Returns the surviving target ticket."
    ),
    response_model=TicketOut,
    dependencies=scoped("tickets:write"),
)
async def tickets_merge(ticket_id: str, body: TicketMerge, svc: Tickets) -> TicketOut:
    return TicketOut.model_validate(await svc.merge(ticket_id, body.target_ticket_id))


@router.get(
    "/{ticket_id}/history",
    operation_id="tickets_history",
    summary="Get a ticket's change history",
    description=(
        "Lists audit events recorded for this ticket (create, assign, status changes, escalation, "
        "merge...), newest first, each with who did it and what changed."
    ),
    response_model=Paginated[AuditEventOut],
    dependencies=scoped("tickets:read", "audit:read"),
)
async def tickets_history(
    ticket_id: str, svc: Tickets, page: Pagination
) -> Paginated[AuditEventOut]:
    return to_page(
        await svc.history(ticket_id, limit=page.limit, cursor=page.cursor), AuditEventOut
    )


@router.post(
    "/{ticket_id}/csat",
    operation_id="tickets_submit_csat",
    summary="Record a customer satisfaction rating",
    description=(
        "Stores the customer's CSAT `score` (1-5) and optional `comment` on a solved or closed "
        "ticket. One rating per ticket; a second attempt returns 409."
    ),
    response_model=CsatOut,
    dependencies=scoped("tickets:write"),
)
async def tickets_submit_csat(ticket_id: str, body: CsatSubmit, svc: Tickets) -> CsatOut:
    ticket = await svc.submit_csat(ticket_id, body.score, body.comment)
    return CsatOut(
        ticket_id=ticket.id,
        score=ticket.csat_score,
        comment=ticket.csat_comment,
        submitted_at=ticket.csat_submitted_at,
    )


@router.get(
    "/{ticket_id}/csat",
    operation_id="tickets_get_csat",
    summary="Get a ticket's satisfaction rating",
    description="Returns the CSAT score and comment for the ticket; the fields are null if unrated.",
    response_model=CsatOut,
    dependencies=scoped("tickets:read"),
)
async def tickets_get_csat(ticket_id: str, svc: Tickets) -> CsatOut:
    ticket = await svc.get(ticket_id)
    return CsatOut(
        ticket_id=ticket.id,
        score=ticket.csat_score,
        comment=ticket.csat_comment,
        submitted_at=ticket.csat_submitted_at,
    )


@router.get(
    "/{ticket_id}/sla",
    operation_id="sla_evaluate_ticket",
    summary="Evaluate a ticket's SLA status",
    description=(
        "Computes, from timestamps, whether the ticket met or is about to miss its first-response "
        "and resolution targets. Each target reports `state` (met, breached, pending, at_risk, "
        "untracked), the due time and minutes remaining (negative when overdue). Use it to answer "
        "'is this ticket breaching SLA?'."
    ),
    response_model=SlaEvaluationOut,
    dependencies=scoped("tickets:read", "sla:read"),
)
async def sla_evaluate_ticket(ticket_id: str, svc: Sla) -> SlaEvaluationOut:
    ev = await svc.evaluate(ticket_id)
    return SlaEvaluationOut(
        ticket_id=ev.ticket_id,
        priority=ev.priority,
        policy_id=ev.policy_id,
        evaluated_at=ev.evaluated_at,
        first_response=TargetStatusOut.model_validate(ev.first_response),
        resolution=TargetStatusOut.model_validate(ev.resolution),
        breached=ev.breached,
    )


@router.get(
    "/{ticket_id}/tags",
    operation_id="tickets_list_tags",
    summary="List a ticket's tags",
    description="Returns just the tag names on the ticket, sorted alphabetically. Use `tickets_get` instead if you also need the ticket's other fields.",
    response_model=list[str],
    dependencies=scoped("tickets:read"),
)
async def tickets_list_tags(ticket_id: str, svc: Tags) -> list[str]:
    return list(await svc.ticket_tags(ticket_id))


@router.post(
    "/{ticket_id}/tags",
    operation_id="tickets_add_tag",
    summary="Add a tag to a ticket",
    description=(
        "Adds `tag` to the ticket, creating the tag if it does not exist. Tags are lowercase "
        "letters, digits, '-' and '_'. Safe to repeat. Returns the ticket."
    ),
    response_model=TicketOut,
    dependencies=scoped("tickets:write"),
)
async def tickets_add_tag(ticket_id: str, body: TicketTagAdd, svc: Tags) -> TicketOut:
    return TicketOut.model_validate(await svc.add_to_ticket(ticket_id, body.tag))


@router.delete(
    "/{ticket_id}/tags/{tag}",
    operation_id="tickets_remove_tag",
    summary="Remove a tag from a ticket",
    description="Removes the tag from the ticket (404 if the ticket does not have it). Returns the ticket.",
    response_model=TicketOut,
    dependencies=scoped("tickets:write"),
)
async def tickets_remove_tag(ticket_id: str, tag: str, svc: Tags) -> TicketOut:
    return TicketOut.model_validate(await svc.remove_from_ticket(ticket_id, tag))


@comments_router.get(
    "/tickets/{ticket_id}/comments",
    operation_id="comments_list",
    summary="List a ticket's comments",
    description=(
        "Returns the conversation on a ticket in chronological order (oldest first). Each comment "
        "has `is_internal`: true for internal notes, false for public replies the customer sees. "
        "Set `include_internal=false` to see only what the customer sees."
    ),
    response_model=Paginated[CommentOut],
    dependencies=scoped("tickets:read"),
)
async def comments_list(
    ticket_id: str,
    svc: Comments,
    page: Pagination,
    include_internal: Annotated[bool, Query(description="Include internal notes.")] = True,
) -> Paginated[CommentOut]:
    result = await svc.list(
        ticket_id, include_internal=include_internal, limit=page.limit, cursor=page.cursor
    )
    return to_page(result, CommentOut)


@comments_router.post(
    "/tickets/{ticket_id}/replies",
    operation_id="comments_add_reply",
    summary="Post a public reply to the customer",
    description=(
        "Adds a PUBLIC comment that the customer will see. Use this to talk to the customer. The "
        "first public reply sets the ticket's first-response time for SLA. For private remarks "
        "between agents use `comments_add_note` instead. Closed tickets return 409. Supports "
        "Idempotency-Key."
    ),
    response_model=CommentOut,
    status_code=201,
    dependencies=scoped("tickets:write"),
)
async def comments_add_reply(ticket_id: str, body: CommentCreate, svc: Comments) -> CommentOut:
    return CommentOut.model_validate(await svc.add(ticket_id, body.body, internal=False))


@comments_router.post(
    "/tickets/{ticket_id}/notes",
    operation_id="comments_add_note",
    summary="Add an internal note",
    description=(
        "Adds an INTERNAL comment visible only to staff; the customer never sees it and it does "
        "not count as a first response. Use it for handover details or context. To answer the "
        "customer use `comments_add_reply`. Closed tickets return 409."
    ),
    response_model=CommentOut,
    status_code=201,
    dependencies=scoped("tickets:write"),
)
async def comments_add_note(ticket_id: str, body: CommentCreate, svc: Comments) -> CommentOut:
    return CommentOut.model_validate(await svc.add(ticket_id, body.body, internal=True))


@comments_router.get(
    "/comments/{comment_id}",
    operation_id="comments_get",
    summary="Get a comment by id",
    description="Returns one comment (for example `CM-00123`): body, author and whether it is an internal note or a public reply.",
    response_model=CommentOut,
    dependencies=scoped("tickets:read"),
)
async def comments_get(comment_id: str, svc: Comments) -> CommentOut:
    return CommentOut.model_validate(await svc.get(comment_id))


@tags_router.get(
    "",
    operation_id="tags_list",
    summary="List all tags",
    description="Lists every tag with the number of tickets using it, sorted by name. Use it to discover valid tags before filtering tickets by `tag`.",
    response_model=list[TagOut],
    dependencies=scoped("tickets:read"),
)
async def tags_list(svc: Tags) -> list[TagOut]:
    return [
        TagOut(name=tag.name, ticket_count=count, created_at=tag.created_at)
        for tag, count in await svc.list()
    ]


@tags_router.post(
    "",
    operation_id="tags_create",
    summary="Create a tag",
    description="Creates a tag name (lowercase letters, digits, '-', '_'). 409 if it exists.",
    response_model=TagOut,
    status_code=201,
    dependencies=scoped("tickets:write"),
)
async def tags_create(body: TagCreate, svc: Tags) -> TagOut:
    tag = await svc.create(body.name)
    return TagOut(name=tag.name, ticket_count=0, created_at=tag.created_at)


@tags_router.delete(
    "/{name}",
    operation_id="tags_delete",
    summary="Delete a tag",
    description="Deletes the tag and removes it from every ticket that carried it. Use `tickets_remove_tag` to remove it from a single ticket instead.",
    status_code=204,
    dependencies=scoped("tickets:write"),
)
async def tags_delete(name: str, svc: Tags) -> Response:
    await svc.delete(name)
    return Response(status_code=204)
