"""FastAPI dependencies: sessions, authentication, scope checks and service factories."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import timedelta
from typing import Annotated, Any

from fastapi import Depends, Query, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer, SecurityScopes
from sqlalchemy.ext.asyncio import AsyncSession

from helpdesk.errors import InsufficientScopeError, UnauthorizedError
from helpdesk.repositories.people import AgentRepository
from helpdesk.repositories.platform import ApiKeyRepository
from helpdesk.security import scopes_allow, split_token, verify_secret
from helpdesk.services.base import BaseService, Context, Principal

bearer_scheme = HTTPBearer(
    auto_error=False,
    description="API key as `Authorization: Bearer hd_<prefix>_<secret>`. "
    "Keys carry scopes such as `tickets:read`; `admin` grants everything.",
)

LAST_USED_REFRESH = timedelta(minutes=5)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        yield session


async def authorize(
    security_scopes: SecurityScopes,
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Principal:
    """Authenticate the API key and check it holds every scope the route requires."""
    if credentials is None:
        raise UnauthorizedError("Send your API key as 'Authorization: Bearer <key>'.")
    parts = split_token(credentials.credentials)
    key = await ApiKeyRepository(session).get_by_prefix(parts[0]) if parts else None
    if parts is None or key is None or not verify_secret(key.salt, parts[1], key.key_hash):
        raise UnauthorizedError("The API key is not valid.")
    now = request.app.state.clock.now()
    if key.revoked_at is not None:
        raise UnauthorizedError("The API key has been revoked.")
    if key.expires_at is not None and key.expires_at <= now:
        raise UnauthorizedError("The API key has expired.")
    granted = frozenset(key.scopes)
    missing = [s for s in security_scopes.scopes if not scopes_allow(granted, s)]
    if missing:
        raise InsufficientScopeError(
            f"This operation requires scope(s) {missing}; the key has {sorted(granted)}.",
            required_scopes=list(security_scopes.scopes),
            missing_scopes=missing,
        )
    agent = await AgentRepository(session).get(key.agent_id) if key.agent_id else None
    principal = Principal(
        api_key_id=key.id,
        name=key.name,
        scopes=granted,
        agent_id=agent.id if agent and agent.active else None,
        agent_role=agent.role if agent and agent.active else None,
    )
    request.state.principal = principal
    request.state.api_key_id = key.id
    if key.last_used_at is None or now - key.last_used_at > LAST_USED_REFRESH:
        key.last_used_at = now
        await session.commit()
    return principal


def scoped(*scopes: str) -> list[Any]:
    """Route-level dependency list: authenticate, then require the given scopes."""
    return [Security(authorize, scopes=list(scopes))]


async def get_context(
    request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> Context:
    state = request.app.state
    return Context(
        session=session,
        clock=state.clock,
        settings=state.settings,
        principal=getattr(request.state, "principal", None),
        request_id=getattr(request.state, "request_id", None),
        webhook_sender=state.webhook_sender,
    )


def service[S: BaseService](cls: type[S]) -> Callable[..., S]:
    def factory(ctx: Annotated[Context, Depends(get_context)]) -> S:
        return cls(ctx)

    return factory


def current_principal(request: Request) -> Principal:
    principal: Principal = request.state.principal
    return principal


class PageParams:
    def __init__(
        self,
        limit: Annotated[
            int, Query(ge=1, le=100, description="Page size (1-100).", examples=[25])
        ] = 25,
        cursor: Annotated[
            str | None,
            Query(description="Opaque cursor from a previous response's `next_cursor`."),
        ] = None,
    ) -> None:
        self.limit = limit
        self.cursor = cursor


Pagination = Annotated[PageParams, Depends()]
