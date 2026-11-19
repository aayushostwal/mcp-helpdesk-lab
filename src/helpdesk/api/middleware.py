"""Pure-ASGI middleware: request ids, access logs, metrics and idempotent replay."""

from __future__ import annotations

import hashlib
import re
import time
import uuid
from datetime import timedelta
from typing import Any

import structlog
from sqlalchemy.exc import IntegrityError
from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from helpdesk.api.problems import problem_response
from helpdesk.errors import IdempotencyKeyReusedError
from helpdesk.metrics import Metrics
from helpdesk.models import IdempotencyRecord
from helpdesk.repositories.platform import IdempotencyRepository

REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
logger = structlog.get_logger("helpdesk.access")


def route_template(scope: Scope) -> str:
    """The matched route as a template, e.g. ``/v1/tickets/{ticket_id}``.

    Rebuilt from the path and the matched path parameters so it does not depend on router
    internals (FastAPI includes routers lazily, so ``scope['route'].path`` lacks the prefix).
    Unmatched requests share one label so scanners cannot blow up metric cardinality.
    """
    if scope.get("route") is None:
        return "unmatched"
    names_by_value = {str(v): k for k, v in scope.get("path_params", {}).items()}
    segments = [
        "{" + names_by_value[seg] + "}" if seg in names_by_value else seg
        for seg in scope["path"].split("/")
    ]
    return "/".join(segments)


class RequestContextMiddleware:
    """Assigns a request id, writes one access log line and records Prometheus metrics."""

    def __init__(self, app: ASGIApp, metrics: Metrics) -> None:
        self.app = app
        self.metrics = metrics

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = Headers(scope=scope).get("x-request-id", "")
        request_id = incoming if REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = time.perf_counter()
        status = 500
        response_started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                status = message["status"]
                response_started = True
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            # Last line of defence: never leak internals, always answer with a problem document.
            logger.error("unhandled_exception", exc_info=exc)
            if not response_started:
                response = problem_response(
                    Request(scope),
                    status=500,
                    slug="internal-error",
                    title="Internal server error",
                    detail="Something went wrong. Quote the request_id when reporting this.",
                )
                await response(scope, receive, send_wrapper)
        finally:
            elapsed = time.perf_counter() - started
            template = route_template(scope)
            method = scope["method"]
            self.metrics.observe(method, template, status, elapsed)
            logger.info(
                "request",
                method=method,
                route=template,
                status=status,
                duration_ms=round(elapsed * 1000, 2),
                api_key_id=scope.get("state", {}).get("api_key_id"),
            )
            structlog.contextvars.clear_contextvars()


class IdempotencyMiddleware:
    """Replays the stored response for a repeated ``Idempotency-Key`` on POST requests.

    Records are scoped to the exact ``Authorization`` header (hashed), so one caller's keys never
    collide with another's. Only successful (2xx) responses are stored, so failed attempts can be
    retried with the same key. Reusing a key with a different request body is rejected with 422.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        headers = Headers(scope=scope) if scope["type"] == "http" else None
        key = headers.get("idempotency-key") if headers else None
        if headers is None or scope["method"] != "POST" or not key:
            await self.app(scope, receive, send)
            return

        body = await self._read_body(receive)
        scope_hash = hashlib.sha256(headers.get("authorization", "").encode()).hexdigest()
        request_hash = hashlib.sha256(
            scope["method"].encode() + scope["path"].encode() + scope["query_string"] + body
        ).hexdigest()
        app = scope["app"]
        now = app.state.clock.now()
        ttl = timedelta(hours=app.state.settings.idempotency_ttl_hours)

        if len(key) > 255:
            response = problem_response(
                Request(scope),
                status=400,
                slug="bad-request",
                title="Bad request",
                detail="Idempotency-Key must be at most 255 characters.",
            )
            await response(scope, receive, send)
            return

        async with app.state.session_factory() as session:
            repo = IdempotencyRepository(session)
            record = await repo.find(scope_hash, key)
            if record is not None and now - record.created_at > ttl:
                await session.delete(record)
                await session.commit()
                record = None
        if record is not None:
            if record.request_hash != request_hash:
                err = IdempotencyKeyReusedError(
                    "This Idempotency-Key was already used with a different request."
                )
                response = problem_response(
                    Request(scope),
                    status=err.status_code,
                    slug=err.slug,
                    title=err.title,
                    detail=err.detail,
                )
                await response(scope, receive, send)
                return
            await self._replay(record, send)
            return

        consumed = False

        async def replay_receive() -> Message:
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        start: Message | None = None
        chunks: list[bytes] = []

        async def capture_send(message: Message) -> None:
            nonlocal start
            if message["type"] == "http.response.start":
                start = message
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))

        await self.app(scope, replay_receive, capture_send)
        assert start is not None
        response_body = b"".join(chunks)
        status = int(start["status"])
        if 200 <= status < 300:
            content_type = Headers(raw=start["headers"]).get("content-type", "application/json")
            await self._store(
                app, scope_hash, key, request_hash, status, response_body, content_type, now
            )
        await send(start)
        await send({"type": "http.response.body", "body": response_body})

    @staticmethod
    async def _read_body(receive: Receive) -> bytes:
        body = b""
        more = True
        while more:
            message = await receive()
            body += message.get("body", b"")
            more = message.get("more_body", False)
        return body

    @staticmethod
    async def _replay(record: IdempotencyRecord, send: Send) -> None:
        body = record.response_body.encode()
        await send(
            {
                "type": "http.response.start",
                "status": record.status_code,
                "headers": [
                    (b"content-type", record.content_type.encode()),
                    (b"content-length", str(len(body)).encode()),
                    (b"idempotent-replayed", b"true"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    async def _store(
        app: Any,
        scope_hash: str,
        key: str,
        request_hash: str,
        status: int,
        body: bytes,
        content_type: str,
        now: Any,
    ) -> None:
        async with app.state.session_factory() as session:
            session.add(
                IdempotencyRecord(
                    scope=scope_hash,
                    key=key,
                    request_hash=request_hash,
                    status_code=status,
                    response_body=body.decode(),
                    content_type=content_type,
                    created_at=now,
                )
            )
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()  # a concurrent request stored it first
