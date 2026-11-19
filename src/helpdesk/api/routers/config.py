from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field

from helpdesk.api.deps import Pagination, scoped, service
from helpdesk.api.routers._common import ERROR_RESPONSES, to_page
from helpdesk.api.schemas.common import InModel, Paginated
from helpdesk.api.schemas.config import (
    ArticleCreate,
    ArticleHit,
    ArticleOut,
    ArticleUpdate,
    DeliveryOut,
    MacroCreate,
    MacroOut,
    MacroUpdate,
    SlaPolicyCreate,
    SlaPolicyOut,
    SlaPolicyUpdate,
    WebhookCreate,
    WebhookCreated,
    WebhookOut,
    WebhookUpdate,
)
from helpdesk.api.schemas.support import CommentOut, TicketOut
from helpdesk.domain import ArticleStatus, Priority
from helpdesk.services.knowledge import KnowledgeService
from helpdesk.services.macros import MacroService
from helpdesk.services.sla import SlaService
from helpdesk.services.webhooks import WebhookService

sla_router = APIRouter(prefix="/sla", tags=["sla"], responses=ERROR_RESPONSES)
macros_router = APIRouter(prefix="/macros", tags=["macros"], responses=ERROR_RESPONSES)
kb_router = APIRouter(prefix="/kb/articles", tags=["knowledge-base"], responses=ERROR_RESPONSES)
webhooks_router = APIRouter(prefix="/webhooks", tags=["webhooks"], responses=ERROR_RESPONSES)

Sla = Annotated[SlaService, Depends(service(SlaService))]
Macros = Annotated[MacroService, Depends(service(MacroService))]
Kb = Annotated[KnowledgeService, Depends(service(KnowledgeService))]
Hooks = Annotated[WebhookService, Depends(service(WebhookService))]


# ----------------------------------------------------------------------------- SLA


@sla_router.get(
    "/policies",
    operation_id="sla_policies_list",
    summary="List SLA policies",
    description=(
        "Lists the SLA policies, one per ticket priority, with first-response and resolution "
        "targets in minutes. Tickets get their due times from the policy matching their priority."
    ),
    response_model=list[SlaPolicyOut],
    dependencies=scoped("sla:read"),
)
async def sla_policies_list(svc: Sla) -> list[SlaPolicyOut]:
    return [SlaPolicyOut.model_validate(p) for p in await svc.list()]


@sla_router.post(
    "/policies",
    operation_id="sla_policies_create",
    summary="Create an SLA policy",
    description=(
        "Defines targets for one priority (low, normal, high, urgent). Only one policy per "
        "priority (409 otherwise). `resolution_minutes` must be >= `first_response_minutes`. "
        "Applies to tickets created or re-prioritised afterwards."
    ),
    response_model=SlaPolicyOut,
    status_code=201,
    dependencies=scoped("sla:write"),
)
async def sla_policies_create(body: SlaPolicyCreate, svc: Sla) -> SlaPolicyOut:
    return SlaPolicyOut.model_validate(await svc.create(body.model_dump()))


@sla_router.get(
    "/policies/{policy_id}",
    operation_id="sla_policies_get",
    summary="Get an SLA policy",
    description="Returns one SLA policy (for example `SLA-002`): its priority, first-response target and resolution target in minutes, and whether it is active.",
    response_model=SlaPolicyOut,
    dependencies=scoped("sla:read"),
)
async def sla_policies_get(policy_id: str, svc: Sla) -> SlaPolicyOut:
    return SlaPolicyOut.model_validate(await svc.get(policy_id))


@sla_router.patch(
    "/policies/{policy_id}",
    operation_id="sla_policies_update",
    summary="Update an SLA policy",
    description=(
        "Changes a policy's name, targets or `active` flag. Existing tickets keep their due "
        "times; new and re-prioritised tickets use the new targets."
    ),
    response_model=SlaPolicyOut,
    dependencies=scoped("sla:write"),
)
async def sla_policies_update(policy_id: str, body: SlaPolicyUpdate, svc: Sla) -> SlaPolicyOut:
    return SlaPolicyOut.model_validate(
        await svc.update(policy_id, body.model_dump(exclude_unset=True))
    )


@sla_router.get(
    "/breaches",
    operation_id="sla_list_breaches",
    summary="List tickets that breached an SLA target",
    description=(
        "Lists tickets that missed, or are currently past, their first-response or resolution "
        "due time, newest first. Filter by `priority` or `team_id`. Use it for escalation "
        "reviews; for a single ticket use `sla_evaluate_ticket`."
    ),
    response_model=Paginated[TicketOut],
    dependencies=scoped("sla:read", "tickets:read"),
)
async def sla_list_breaches(
    svc: Sla, page: Pagination, priority: Priority | None = None, team_id: str | None = None
) -> Paginated[TicketOut]:
    result = await svc.list_breaches(
        priority=priority, team_id=team_id, limit=page.limit, cursor=page.cursor
    )
    return to_page(result, TicketOut)


# --------------------------------------------------------------------------- macros


class MacroApply(InModel):
    ticket_id: str = Field(description="Ticket to apply the macro to.")


class MacroApplied(BaseModel):
    ticket: TicketOut
    comment: CommentOut


@macros_router.get(
    "",
    operation_id="macros_list",
    summary="List macros (canned replies)",
    description="Lists macros (canned replies) ordered by id. Search the name and text with `q` to find the right one, then use `macros_apply` to post it on a ticket.",
    response_model=Paginated[MacroOut],
    dependencies=scoped("macros:read"),
)
async def macros_list(
    svc: Macros, page: Pagination, q: Annotated[str | None, Query()] = None
) -> Paginated[MacroOut]:
    return to_page(await svc.list(q=q, limit=page.limit, cursor=page.cursor), MacroOut)


@macros_router.post(
    "",
    operation_id="macros_create",
    summary="Create a macro",
    description=(
        "Creates a reusable reply. `body` may contain {{customer_name}}, {{ticket_id}} and "
        "{{agent_name}}. `actions` optionally sets status, priority, add_tags and whether the "
        "reply is public (default) or an internal note."
    ),
    response_model=MacroOut,
    status_code=201,
    dependencies=scoped("macros:write"),
)
async def macros_create(body: MacroCreate, svc: Macros) -> MacroOut:
    data = body.model_dump()
    return MacroOut.model_validate(await svc.create(data))


@macros_router.get(
    "/{macro_id}",
    operation_id="macros_get",
    summary="Get a macro",
    description="Returns one macro (for example `M-004`) with its reply text, placeholders and the actions it runs (status, priority, tags, public or internal).",
    response_model=MacroOut,
    dependencies=scoped("macros:read"),
)
async def macros_get(macro_id: str, svc: Macros) -> MacroOut:
    return MacroOut.model_validate(await svc.get(macro_id))


@macros_router.patch(
    "/{macro_id}",
    operation_id="macros_update",
    summary="Update a macro",
    description="Partially updates a macro's name, description, body or actions. Send only the fields to change. Names must stay unique (409 otherwise).",
    response_model=MacroOut,
    dependencies=scoped("macros:write"),
)
async def macros_update(macro_id: str, body: MacroUpdate, svc: Macros) -> MacroOut:
    return MacroOut.model_validate(await svc.update(macro_id, body.model_dump(exclude_unset=True)))


@macros_router.delete(
    "/{macro_id}",
    operation_id="macros_delete",
    summary="Delete a macro",
    description="Permanently deletes a macro. Tickets it was applied to keep the comments it posted. This cannot be undone.",
    status_code=204,
    dependencies=scoped("macros:write"),
)
async def macros_delete(macro_id: str, svc: Macros) -> Response:
    await svc.delete(macro_id)
    return Response(status_code=204)


@macros_router.post(
    "/{macro_id}/apply",
    operation_id="macros_apply",
    summary="Apply a macro to a ticket",
    description=(
        "Posts the macro text on the ticket (public reply unless the macro is internal) and runs "
        "its actions (status, priority, tags) atomically. Variables are filled from the ticket's "
        "customer and the calling agent. Status steps obey the ticket status machine. Returns the "
        "updated ticket and the posted comment."
    ),
    response_model=MacroApplied,
    dependencies=scoped("macros:write", "tickets:write"),
)
async def macros_apply(macro_id: str, body: MacroApply, svc: Macros) -> MacroApplied:
    ticket, comment = await svc.apply(macro_id, body.ticket_id)
    return MacroApplied(
        ticket=TicketOut.model_validate(ticket), comment=CommentOut.model_validate(comment)
    )


# ------------------------------------------------------------------------------- KB


@kb_router.get(
    "/search",
    operation_id="kb_articles_search",
    summary="Search knowledge base articles",
    description=(
        "Ranked keyword search over article titles and bodies (title matches count triple). "
        "Returns the best matches with a text snippet. Only published articles unless "
        "`include_drafts=true`. Use it to find an answer to quote to a customer."
    ),
    response_model=list[ArticleHit],
    dependencies=scoped("kb:read"),
)
async def kb_articles_search(
    svc: Kb,
    q: Annotated[str, Query(min_length=2, description="Keywords to look for.")],
    category: str | None = None,
    include_drafts: bool = False,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> list[ArticleHit]:
    hits = await svc.search(q, category=category, include_drafts=include_drafts, limit=limit)
    return [
        ArticleHit(
            id=h.article.id,
            title=h.article.title,
            slug=h.article.slug,
            category=h.article.category,
            status=h.article.status,
            score=h.score,
            snippet=h.snippet,
        )
        for h in hits
    ]


@kb_router.get(
    "",
    operation_id="kb_articles_list",
    summary="List knowledge base articles",
    description=(
        "Lists articles, most recently updated first. Filter by `category`, `status` (draft or "
        "published) or a substring `q`. For relevance-ranked results use `kb_articles_search`."
    ),
    response_model=Paginated[ArticleOut],
    dependencies=scoped("kb:read"),
)
async def kb_articles_list(
    svc: Kb,
    page: Pagination,
    q: str | None = None,
    category: str | None = None,
    status: ArticleStatus | None = None,
) -> Paginated[ArticleOut]:
    result = await svc.list(
        q=q, category=category, status=status, limit=page.limit, cursor=page.cursor
    )
    return to_page(result, ArticleOut)


@kb_router.post(
    "",
    operation_id="kb_articles_create",
    summary="Create a knowledge base article (draft)",
    description=(
        "Creates an article as a draft. Required: `title`, `category`, `body`. A URL slug is "
        "generated from the title. Publish it with `kb_articles_publish`."
    ),
    response_model=ArticleOut,
    status_code=201,
    dependencies=scoped("kb:write"),
)
async def kb_articles_create(body: ArticleCreate, svc: Kb) -> ArticleOut:
    return ArticleOut.model_validate(await svc.create(body.model_dump()))


@kb_router.get(
    "/{article_id}",
    operation_id="kb_articles_get",
    summary="Get an article by id",
    description="Returns the full article (for example `KB-0012`) including body and status. Use it after `kb_articles_search` to read the whole text.",
    response_model=ArticleOut,
    dependencies=scoped("kb:read"),
)
async def kb_articles_get(article_id: str, svc: Kb) -> ArticleOut:
    return ArticleOut.model_validate(await svc.get(article_id))


@kb_router.patch(
    "/{article_id}",
    operation_id="kb_articles_update",
    summary="Edit an article",
    description="Partially updates `title`, `category` or `body`. Status is unchanged; the slug is stable.",
    response_model=ArticleOut,
    dependencies=scoped("kb:write"),
)
async def kb_articles_update(article_id: str, body: ArticleUpdate, svc: Kb) -> ArticleOut:
    return ArticleOut.model_validate(
        await svc.update(article_id, body.model_dump(exclude_unset=True))
    )


@kb_router.post(
    "/{article_id}/publish",
    operation_id="kb_articles_publish",
    summary="Publish an article",
    description="Makes a draft visible in default searches and stamps `published_at`. Safe to repeat.",
    response_model=ArticleOut,
    dependencies=scoped("kb:write"),
)
async def kb_articles_publish(article_id: str, svc: Kb) -> ArticleOut:
    return ArticleOut.model_validate(await svc.publish(article_id))


@kb_router.post(
    "/{article_id}/unpublish",
    operation_id="kb_articles_unpublish",
    summary="Unpublish an article",
    description="Returns a published article to draft so it disappears from default searches and customer-facing results. Safe to repeat.",
    response_model=ArticleOut,
    dependencies=scoped("kb:write"),
)
async def kb_articles_unpublish(article_id: str, svc: Kb) -> ArticleOut:
    return ArticleOut.model_validate(await svc.unpublish(article_id))


@kb_router.delete(
    "/{article_id}",
    operation_id="kb_articles_delete",
    summary="Delete an article",
    description="Permanently deletes the article. This cannot be undone; prefer `kb_articles_unpublish` if you only want to hide it from customers.",
    status_code=204,
    dependencies=scoped("kb:write"),
)
async def kb_articles_delete(article_id: str, svc: Kb) -> Response:
    await svc.delete(article_id)
    return Response(status_code=204)


# ---------------------------------------------------------------------------- webhooks


@webhooks_router.get(
    "",
    operation_id="webhooks_list",
    summary="List webhook subscriptions",
    description="Lists configured webhooks (url, subscribed events, active flag). Secrets are never shown.",
    response_model=list[WebhookOut],
    dependencies=scoped("webhooks:read"),
)
async def webhooks_list(svc: Hooks) -> list[WebhookOut]:
    return [WebhookOut.model_validate(w) for w in await svc.list()]


@webhooks_router.post(
    "",
    operation_id="webhooks_create",
    summary="Create a webhook subscription",
    description=(
        "Subscribes `url` to the listed `events` (for example ticket.created, refund.approved). "
        "The response includes a signing `secret` exactly once; deliveries carry an "
        "`X-Helpdesk-Signature: sha256=<hmac>` header. Private and loopback addresses are "
        "rejected. Use `webhooks_test` to verify the endpoint."
    ),
    response_model=WebhookCreated,
    status_code=201,
    dependencies=scoped("webhooks:write"),
)
async def webhooks_create(body: WebhookCreate, svc: Hooks) -> WebhookCreated:
    return WebhookCreated.model_validate(await svc.create(body.url, list(body.events)))


@webhooks_router.get(
    "/{webhook_id}",
    operation_id="webhooks_get",
    summary="Get a webhook",
    description="Returns one webhook subscription (for example `WH-003`): target url, subscribed events and active flag. The signing secret is not included.",
    response_model=WebhookOut,
    dependencies=scoped("webhooks:read"),
)
async def webhooks_get(webhook_id: str, svc: Hooks) -> WebhookOut:
    return WebhookOut.model_validate(await svc.get(webhook_id))


@webhooks_router.patch(
    "/{webhook_id}",
    operation_id="webhooks_update",
    summary="Update a webhook",
    description="Changes the target `url`, the subscribed `events` or pauses it with `active=false`.",
    response_model=WebhookOut,
    dependencies=scoped("webhooks:write"),
)
async def webhooks_update(webhook_id: str, body: WebhookUpdate, svc: Hooks) -> WebhookOut:
    changes = body.model_dump(exclude_unset=True)
    return WebhookOut.model_validate(await svc.update(webhook_id, changes))


@webhooks_router.delete(
    "/{webhook_id}",
    operation_id="webhooks_delete",
    summary="Delete a webhook",
    description="Deletes the webhook subscription and its delivery history. Use `webhooks_update` with `active=false` to pause it instead.",
    status_code=204,
    dependencies=scoped("webhooks:write"),
)
async def webhooks_delete(webhook_id: str, svc: Hooks) -> Response:
    await svc.delete(webhook_id)
    return Response(status_code=204)


@webhooks_router.post(
    "/{webhook_id}/test",
    operation_id="webhooks_test",
    summary="Send a test delivery",
    description=(
        "Sends a signed `webhook.test` event to the subscriber and records the outcome. The "
        "response says whether the receiver answered 2xx (`success`), the status code and any "
        "error. A failed delivery is reported in the body, not as an HTTP error."
    ),
    response_model=DeliveryOut,
    dependencies=scoped("webhooks:write"),
)
async def webhooks_test(webhook_id: str, svc: Hooks) -> DeliveryOut:
    return DeliveryOut.model_validate(await svc.test(webhook_id))


@webhooks_router.get(
    "/{webhook_id}/deliveries",
    operation_id="webhooks_list_deliveries",
    summary="List delivery attempts",
    description="Lists recorded delivery attempts for the webhook, newest first, with success flag and errors.",
    response_model=Paginated[DeliveryOut],
    dependencies=scoped("webhooks:read"),
)
async def webhooks_list_deliveries(
    webhook_id: str, svc: Hooks, page: Pagination
) -> Paginated[DeliveryOut]:
    result = await svc.list_deliveries(webhook_id, limit=page.limit, cursor=page.cursor)
    return to_page(result, DeliveryOut)
