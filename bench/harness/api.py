"""A small synchronous client for the Helpdesk API, used by graders and scripted oracles."""

from __future__ import annotations

from typing import Any

import httpx


class ApiError(RuntimeError):
    def __init__(self, status: int, body: Any) -> None:
        super().__init__(f"HTTP {status}: {body}")
        self.status = status
        self.body = body


class Api:
    """JSON in, JSON out. Raises :class:`ApiError` on any non-2xx answer."""

    def __init__(self, client: httpx.Client) -> None:
        self._c = client

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        resp = self._c.request(method, path, **kwargs)
        body: Any
        try:
            body = resp.json() if resp.content else None
        except ValueError:
            body = resp.text
        if resp.status_code >= 400:
            raise ApiError(resp.status_code, body)
        return body

    def get(self, path: str, **params: Any) -> Any:
        return self.request("GET", path, params={k: v for k, v in params.items() if v is not None})

    def post(self, path: str, body: dict[str, Any] | None = None) -> Any:
        return self.request("POST", path, json=body or {})

    def items(self, path: str, **params: Any) -> list[dict[str, Any]]:
        """Follow ``next_cursor`` until the list is exhausted."""
        out: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            page = self.get(path, limit=100, cursor=cursor, **params)
            if isinstance(page, list):  # unpaginated collections (teams, kb search)
                return list(page)
            out.extend(page["items"])
            cursor = page.get("next_cursor")
            if not cursor:
                return out

    def agent_writes(self) -> list[dict[str, Any]]:
        """Audit events written during the run. The seed has none; key creation is ignored."""
        events = self.items("/v1/audit/events")
        return [e for e in events if not str(e["action"]).startswith("api_keys.")]
