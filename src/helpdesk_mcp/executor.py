"""The one place that turns an operation id and a flat argument dict into an HTTP call.

All three tool variants call :meth:`Executor.call`, so the same operation with the same
arguments returns the same result whichever variant asked. Failures never raise: they come back
as an :class:`ExecResult` with ``ok=False`` and a problem+json style document.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from helpdesk_mcp import __version__
from helpdesk_mcp.settings import McpSettings
from helpdesk_mcp.spec import Operation, Spec

PROBLEM_NS = "urn:helpdesk-mcp:problem:"


@dataclass(frozen=True)
class ExecResult:
    """Outcome of one operation: parsed JSON on success, a problem document on failure."""

    ok: bool
    status: int
    data: Any
    error_type: str | None = None
    request_id: str | None = None

    def text(self) -> str:
        """The JSON text returned to the model (compact, identical for every variant)."""
        return json.dumps(self.data, ensure_ascii=False, separators=(",", ":"))


def problem(status: int, kind: str, title: str, detail: str, **extra: Any) -> ExecResult:
    """A client-side problem document shaped like the API's own (RFC 9457)."""
    body: dict[str, Any] = {
        "type": PROBLEM_NS + kind,
        "title": title,
        "status": status,
        "detail": detail,
        **extra,
    }
    return ExecResult(False, status, body, kind)


def _query_value(value: Any) -> Any:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list | tuple):
        return [_query_value(item) for item in value]
    return value


class Executor:
    """Executes operations against the Helpdesk API over a shared ``httpx.AsyncClient``."""

    def __init__(
        self, spec: Spec, settings: McpSettings, client: httpx.AsyncClient | None = None
    ) -> None:
        self.spec = spec
        self.settings = settings
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            base_url=settings.base_url, timeout=settings.timeout_seconds
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    def _headers(self, op: Operation, request_id: str) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": f"helpdesk-mcp/{__version__}",
            "X-Request-ID": request_id,
        }
        if self.settings.api_key is not None:
            headers["Authorization"] = f"Bearer {self.settings.api_key.get_secret_value()}"
        if op.method == "POST":
            # One key per tool call, reused if the call is retried after a network failure.
            headers["Idempotency-Key"] = f"mcp-{uuid.uuid4().hex}"
        return headers

    def _split(self, op: Operation, arguments: Mapping[str, Any]) -> ExecResult | dict[str, Any]:
        """Validate argument names and return ``{path, query, body}``, or a problem."""
        allowed = set(op.argument_names)
        unknown = sorted(set(arguments) - allowed)
        if unknown:
            return problem(
                422,
                "invalid-arguments",
                "Unknown arguments",
                f"{op.operation_id} does not accept {unknown}. Accepted arguments: "
                f"{sorted(allowed)}.",
                allowed=sorted(allowed),
            )
        path_values: dict[str, str] = {}
        query: dict[str, Any] = {}
        for param in op.parameters:
            value = arguments.get(param.name)
            if param.location == "path":
                if value is None or value == "":
                    return problem(
                        422,
                        "invalid-arguments",
                        "Missing path argument",
                        f"{op.operation_id} requires '{param.name}'.",
                    )
                path_values[param.name] = quote(str(value), safe="")
            elif value is not None:
                query[param.name] = _query_value(value)
        body = {k: arguments[k] for k in op.body_fields if k in arguments}
        return {"path": path_values, "query": query, "body": body}

    async def call(
        self, operation_id: str, arguments: Mapping[str, Any] | None = None
    ) -> ExecResult:
        op = self.spec.get(operation_id)
        if op is None:
            return problem(
                404,
                "unknown-operation",
                "Unknown operation",
                f"There is no operation '{operation_id}'.",
            )
        arguments = arguments or {}
        if not isinstance(arguments, Mapping):
            return problem(
                422, "invalid-arguments", "Invalid arguments", "Arguments must be an object."
            )
        parts = self._split(op, arguments)
        if isinstance(parts, ExecResult):
            return parts

        url = op.path.format(**parts["path"])
        request_id = f"mcp-{uuid.uuid4().hex[:24]}"
        headers = self._headers(op, request_id)
        send_body = op.has_body and (bool(parts["body"]) or op.body_required)
        attempts = self.settings.retries + 1
        for attempt in range(1, attempts + 1):
            try:
                response = await self.client.request(
                    op.method,
                    url,
                    params=parts["query"] or None,
                    json=parts["body"] if send_body else None,
                    headers=headers,
                )
            except httpx.TimeoutException as exc:
                if attempt < attempts:
                    continue
                return problem(
                    504,
                    "upstream-timeout",
                    "Helpdesk API timed out",
                    f"No answer from the Helpdesk API within {self.settings.timeout_seconds:g}s "
                    f"({exc.__class__.__name__}). The operation may or may not have run.",
                )
            except httpx.TransportError as exc:
                if attempt < attempts:
                    continue
                return problem(
                    502,
                    "upstream-unreachable",
                    "Helpdesk API unreachable",
                    f"Could not reach the Helpdesk API at {self.settings.base_url} "
                    f"({exc.__class__.__name__}).",
                )
            return self._result(response)
        raise AssertionError("unreachable")  # pragma: no cover

    @staticmethod
    def _result(response: httpx.Response) -> ExecResult:
        request_id = response.headers.get("x-request-id")
        parsed: Any = None
        if response.content:
            try:
                parsed = response.json()
            except ValueError:
                parsed = None
        if response.is_success:
            if parsed is None and not response.content:
                parsed = {"ok": True, "status": response.status_code}
            return ExecResult(True, response.status_code, parsed, None, request_id)
        if isinstance(parsed, dict):
            kind = str(parsed.get("type", "about:blank")).rstrip("/").rsplit("/", 1)[-1]
            return ExecResult(False, response.status_code, parsed, kind, request_id)
        body = {
            "type": "about:blank",
            "title": response.reason_phrase or "HTTP error",
            "status": response.status_code,
            "detail": response.text[:500] or "The API returned an error without a body.",
        }
        return ExecResult(False, response.status_code, body, "about:blank", request_id)
