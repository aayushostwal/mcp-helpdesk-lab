"""RFC 9457 problem+json responses and the exception handlers that produce them."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from helpdesk.errors import DomainError

PROBLEM_JSON = "application/problem+json"

_HTTP_SLUGS = {
    400: ("bad-request", "Bad request"),
    404: ("route-not-found", "Not found"),
    405: ("method-not-allowed", "Method not allowed"),
    415: ("unsupported-media-type", "Unsupported media type"),
}


def problem_response(
    request: Request,
    *,
    status: int,
    slug: str,
    title: str,
    detail: str,
    extra: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    base = request.app.state.settings.problem_base_uri
    request_id = getattr(request.state, "request_id", None)
    body: dict[str, Any] = {
        "type": f"{base}{slug}",
        "title": title,
        "status": status,
        "detail": detail,
        "instance": request.url.path,
        "request_id": request_id,
    }
    if extra:
        body.update(extra)
    out_headers = dict(headers or {})
    if status == 401:
        out_headers["WWW-Authenticate"] = "Bearer"
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON, headers=out_headers)


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain(request: Request, exc: DomainError) -> JSONResponse:
        return problem_response(
            request,
            status=exc.status_code,
            slug=exc.slug,
            title=exc.title,
            detail=exc.detail,
            extra=exc.extra,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {
                "loc": [str(p) for p in err["loc"]],
                "msg": err["msg"],
                "type": err["type"],
            }
            for err in exc.errors()
        ]
        return problem_response(
            request,
            status=422,
            slug="validation-failed",
            title="Request is not valid",
            detail="One or more request fields are invalid. See 'errors'.",
            extra={"errors": errors},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        slug, title = _HTTP_SLUGS.get(exc.status_code, ("http-error", "HTTP error"))
        return problem_response(
            request,
            status=exc.status_code,
            slug=slug,
            title=title,
            detail=str(exc.detail),
            headers=dict(exc.headers) if exc.headers else None,
        )
