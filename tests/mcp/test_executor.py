from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from helpdesk_mcp.executor import ExecResult, Executor
from helpdesk_mcp.settings import McpSettings
from helpdesk_mcp.spec import Spec


def make(
    spec: Spec,
    handler: Any,
    *,
    key: str | None = "hd_test_secret",
    retries: int = 0,
) -> Executor:
    settings = McpSettings(api_url="http://api.test", api_key=key, retries=retries)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://api.test")
    return Executor(spec, settings, client)


def ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"seen": str(request.url)})


async def test_path_and_query_building(spec: Spec) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    ex = make(spec, handler)
    await ex.call(
        "tickets_list",
        {"status": "open", "sla_breached": True, "limit": 3, "q": None, "tag": ["a", True]},
    )
    await ex.call("tickets_get", {"ticket_id": "T-1/../x"})
    await ex.aclose()
    assert seen[0].url.params["sla_breached"] == "true"
    assert seen[0].url.params["status"] == "open"
    assert "q" not in seen[0].url.params
    assert seen[1].url.raw_path == b"/v1/tickets/T-1%2F..%2Fx"


async def test_body_is_sent_only_for_body_fields(spec: Spec) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"id": "R-1"})

    ex = make(spec, handler)
    result = await ex.call(
        "refunds_create", {"order_id": "O-1", "amount": "5.00", "reason": "damaged"}
    )
    assert result.ok and result.status == 201
    assert json.loads(seen[0].content) == {"order_id": "O-1", "amount": "5.00", "reason": "damaged"}
    # an optional body with no fields sends no body at all
    await ex.call("tickets_close", {"ticket_id": "T-1"})
    assert seen[1].content == b""
    await ex.aclose()


async def test_headers_and_idempotency_keys(spec: Spec) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    ex = make(spec, handler)
    for _ in range(2):
        await ex.call("refunds_cancel", {"refund_id": "R-1"})
    await ex.call("refunds_get", {"refund_id": "R-1"})
    await ex.call("tags_delete", {"name": "x"})
    keys = [r.headers.get("idempotency-key") for r in seen]
    assert keys[0] and keys[1] and keys[0] != keys[1]  # one fresh key per POST call
    assert keys[2] is None and keys[3] is None  # GET and DELETE carry none
    assert seen[0].headers["authorization"] == "Bearer hd_test_secret"
    assert seen[0].headers["x-request-id"].startswith("mcp-")
    assert seen[0].headers["user-agent"].startswith("helpdesk-mcp/")
    await ex.aclose()


async def test_no_authorization_header_without_a_key(spec: Spec) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    ex = make(spec, handler, key=None)
    await ex.call("tags_list")
    assert "authorization" not in seen[0].headers
    await ex.aclose()


async def test_problem_json_is_passed_through(spec: Spec) -> None:
    problem = {
        "type": "https://helpdesk.example/problems/refund-not-eligible",
        "title": "Order is not eligible for a refund",
        "status": 422,
        "detail": "Order 'O-1' is outside the 30-day refund window.",
        "request_id": "abc",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json=problem,
            headers={"content-type": "application/problem+json", "x-request-id": "r1"},
        )

    ex = make(spec, handler)
    result = await ex.call("refunds_create", {"order_id": "O-1", "amount": "1", "reason": "x"})
    assert (result.ok, result.status, result.data) == (False, 422, problem)
    assert result.error_type == "refund-not-eligible" and result.request_id == "r1"
    assert json.loads(result.text()) == problem
    await ex.aclose()


async def test_non_json_error_bodies_become_problems(spec: Spec) -> None:
    ex = make(spec, lambda r: httpx.Response(502, text="Bad gateway from proxy"))
    result = await ex.call("tags_list")
    assert not result.ok and result.status == 502
    assert result.data["detail"] == "Bad gateway from proxy"
    assert result.error_type == "about:blank"
    empty = make(spec, lambda r: httpx.Response(500))
    result = await empty.call("tags_list")
    assert "without a body" in result.data["detail"]
    await ex.aclose()
    await empty.aclose()


async def test_no_content_success(spec: Spec) -> None:
    ex = make(spec, lambda r: httpx.Response(204))
    result = await ex.call("tags_delete", {"name": "x"})
    assert result == ExecResult(True, 204, {"ok": True, "status": 204}, None, None)
    await ex.aclose()


async def test_success_with_a_non_json_body(spec: Spec) -> None:
    ex = make(spec, lambda r: httpx.Response(200, text="plain"))
    result = await ex.call("tags_list")
    assert result.ok and result.data is None
    await ex.aclose()


async def test_argument_validation(spec: Spec) -> None:
    ex = make(spec, ok)
    unknown = await ex.call("tickets_get", {"ticket_id": "T-1", "bogus": 1})
    assert unknown.status == 422 and unknown.error_type == "invalid-arguments"
    assert "bogus" in unknown.data["detail"] and "ticket_id" in unknown.data["allowed"]
    missing = await ex.call("tickets_get", {})
    assert missing.status == 422 and "ticket_id" in missing.data["detail"]
    blank = await ex.call("tickets_get", {"ticket_id": ""})
    assert blank.status == 422
    missing_op = await ex.call("nope", {})
    assert missing_op.status == 404 and missing_op.error_type == "unknown-operation"
    bad = await ex.call("tags_list", ["not", "a", "dict"])  # type: ignore[arg-type]
    assert bad.status == 422
    await ex.aclose()


async def test_retries_reuse_the_idempotency_key(spec: Spec) -> None:
    keys: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        keys.append(request.headers.get("idempotency-key"))
        if len(keys) == 1:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(201, json={"id": "R-1"})

    ex = make(spec, handler, retries=1)
    result = await ex.call("refunds_cancel", {"refund_id": "R-1"})
    assert result.ok and len(keys) == 2 and keys[0] == keys[1] and keys[0]
    await ex.aclose()


async def test_timeout_and_unreachable_become_problems_without_leaking_the_key(spec: Spec) -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    slow = make(spec, timeout, retries=1)
    result = await slow.call("tags_list")
    assert (result.status, result.error_type) == (504, "upstream-timeout")
    down = make(spec, refused)
    result = await down.call("tags_list")
    assert (result.status, result.error_type) == (502, "upstream-unreachable")
    assert "http://api.test" in result.data["detail"]
    assert "hd_test_secret" not in result.text()
    await slow.aclose()
    await down.aclose()


async def test_owned_client_is_closed() -> None:
    from helpdesk_mcp.spec import parse_spec

    ex = Executor(parse_spec({"paths": {}}), McpSettings())
    await ex.aclose()
    assert ex.client.is_closed


def test_settings_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HELPDESK_MCP_API_URL", "http://example:9/")
    monkeypatch.setenv("HELPDESK_MCP_API_KEY", "hd_k_s")
    settings = McpSettings()
    assert settings.base_url == "http://example:9"
    assert settings.api_key is not None and settings.api_key.get_secret_value() == "hd_k_s"
    assert "hd_k_s" not in repr(settings)
