"""Cross-cutting behaviour: health, auth, errors, pagination, idempotency, audit, metrics."""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from helpdesk.clock import FrozenClock
from helpdesk.models import ApiKey
from tests.conftest import NOW, KeyFactory, create_ticket

# --------------------------------------------------------------------------- health


async def test_healthz_and_readyz(client: httpx.AsyncClient) -> None:
    assert (await client.get("/healthz")).json() == {"status": "ok"}
    ready = await client.get("/readyz")
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready", "database": "ok"}


async def test_readyz_reports_503_when_database_is_down(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    await app.state.engine.dispose()
    original = app.state.session_factory

    class Broken:
        def __call__(self) -> object:
            raise RuntimeError("db down")

    app.state.session_factory = Broken()
    r = await client.get("/readyz")
    app.state.session_factory = original
    assert r.status_code == 503
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["type"].endswith("/not-ready")


async def test_metrics_count_requests_by_route_template(
    client: httpx.AsyncClient, admin: dict[str, str]
) -> None:
    await client.get("/v1/customers/C-0001", headers=admin)
    await client.get("/v1/customers/C-0002", headers=admin)
    text = (await client.get("/metrics")).text
    assert 'route="/v1/customers/{customer_id}"' in text
    assert "helpdesk_http_request_duration_seconds_bucket" in text
    assert "C-0001" not in text  # templates only, no raw ids


async def test_request_id_is_generated_echoed_and_in_errors(client: httpx.AsyncClient) -> None:
    r = await client.get("/v1/customers/C-0001")
    rid = r.headers["x-request-id"]
    assert len(rid) >= 8
    assert r.json()["request_id"] == rid
    r2 = await client.get("/v1/customers/C-0001", headers={"X-Request-ID": "my-trace-12345"})
    assert r2.headers["x-request-id"] == "my-trace-12345"
    r3 = await client.get("/v1/customers/C-0001", headers={"X-Request-ID": "bad id with spaces"})
    assert r3.headers["x-request-id"] != "bad id with spaces"


# ----------------------------------------------------------------------------- auth


async def test_missing_key_is_401_problem(client: httpx.AsyncClient, world: dict[str, str]) -> None:
    r = await client.get("/v1/customers")
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.headers["www-authenticate"] == "Bearer"
    body = r.json()
    assert body["type"].endswith("/unauthorized") and body["status"] == 401


async def test_invalid_key_is_401(client: httpx.AsyncClient, world: dict[str, str]) -> None:
    for token in ("garbage", "hd_nope_nope", "hd_abcd1234_wrongsecret"):
        r = await client.get("/v1/customers", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401, token


async def test_wrong_secret_with_valid_prefix_is_401(
    client: httpx.AsyncClient, make_key: KeyFactory, world: dict[str, str]
) -> None:
    token = await make_key(["admin"])
    prefix = token.split("_")[1]
    r = await client.get("/v1/customers", headers={"Authorization": f"Bearer hd_{prefix}_forged"})
    assert r.status_code == 401


async def test_keys_are_stored_hashed_with_salt(
    make_key: KeyFactory, session_factory: async_sessionmaker[AsyncSession], world: dict[str, str]
) -> None:
    token = await make_key(["admin"])
    secret = token.split("_", 2)[2]
    async with session_factory() as s:
        row = (await s.scalars(select(ApiKey))).one()
    assert secret not in (row.key_hash, row.salt, row.prefix)
    assert len(row.key_hash) == 64 and len(row.salt) == 32
    assert token not in str(row.__dict__)


async def test_revoked_and_expired_keys_are_rejected(
    client: httpx.AsyncClient,
    make_key: KeyFactory,
    admin: dict[str, str],
    clock: FrozenClock,
    world: dict[str, str],
) -> None:
    token = await make_key(["admin"], expires_in_days=1, name="short")
    headers = {"Authorization": f"Bearer {token}"}
    assert (await client.get("/v1/customers", headers=headers)).status_code == 200
    clock.set(NOW + timedelta(days=2))
    r = await client.get("/v1/customers", headers=headers)
    assert r.status_code == 401 and "expired" in r.json()["detail"]
    clock.set(NOW)
    keys = (await client.get("/v1/admin/api-keys", headers=admin)).json()
    short = next(k for k in keys if k["name"] == "short")
    assert (
        await client.post(f"/v1/admin/api-keys/{short['id']}/revoke", headers=admin)
    ).status_code == 200
    r = await client.get("/v1/customers", headers=headers)
    assert r.status_code == 401 and "revoked" in r.json()["detail"]
    # revoking again is a no-op
    again = await client.post(f"/v1/admin/api-keys/{short['id']}/revoke", headers=admin)
    assert again.status_code == 200


async def test_insufficient_scope_is_403_with_details(
    client: httpx.AsyncClient, make_key: KeyFactory, world: dict[str, str]
) -> None:
    token = await make_key(["tickets:read"])
    headers = {"Authorization": f"Bearer {token}"}
    assert (await client.get("/v1/tickets", headers=headers)).status_code == 200
    r = await client.post(
        "/v1/tickets",
        json={"subject": "x", "description": "y", "customer_id": "C-0001"},
        headers=headers,
    )
    assert r.status_code == 403
    body = r.json()
    assert body["type"].endswith("/insufficient-scope")
    assert body["required_scopes"] == ["tickets:write"]
    assert (await client.get("/v1/admin/settings", headers=headers)).status_code == 403


async def test_whoami_and_last_used(
    client: httpx.AsyncClient,
    lead: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    body = (await client.get("/v1/admin/whoami", headers=lead)).json()
    assert body["agent_id"] == "A-0002" and body["agent_role"] == "lead"
    assert body["scopes"] == ["admin"]
    async with session_factory() as s:
        used = (await s.scalars(select(ApiKey).where(ApiKey.name == "lead"))).one()
    assert used.last_used_at is not None


async def test_deactivated_agent_key_loses_agent_powers(
    client: httpx.AsyncClient, lead: dict[str, str], admin: dict[str, str]
) -> None:
    await client.post("/v1/agents/A-0002/deactivate", headers=admin)
    body = (await client.get("/v1/admin/whoami", headers=lead)).json()
    assert body["agent_id"] is None


# ---------------------------------------------------------------------------- errors


async def test_error_shapes(client: httpx.AsyncClient, admin: dict[str, str]) -> None:
    nf = await client.get("/v1/customers/C-9999", headers=admin)
    assert nf.status_code == 404
    assert nf.headers["content-type"].startswith("application/problem+json")
    body = nf.json()
    assert body["type"].endswith("/not-found")
    assert body["instance"] == "/v1/customers/C-9999"
    assert body["entity"] == "Customer"

    invalid = await client.post("/v1/customers", json={"name": "", "email": "nope"}, headers=admin)
    assert invalid.status_code == 422
    assert invalid.json()["type"].endswith("/validation-failed")
    assert {e["loc"][-1] for e in invalid.json()["errors"]} == {"name", "email"}

    extra = await client.post(
        "/v1/customers", json={"name": "A", "email": "a@b.co", "bogus": 1}, headers=admin
    )
    assert extra.status_code == 422

    unknown = await client.get("/v1/nothing-here", headers=admin)
    assert unknown.status_code == 404
    assert unknown.headers["content-type"].startswith("application/problem+json")
    assert unknown.json()["type"].endswith("/route-not-found")

    method = await client.delete("/v1/customers", headers=admin)
    assert method.status_code == 405
    assert method.json()["type"].endswith("/method-not-allowed")


async def test_unhandled_exception_becomes_500_problem(
    app: FastAPI,
    admin: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from helpdesk.services.customers import CustomerService

    async def boom(self: CustomerService, customer_id: str) -> None:
        raise RuntimeError("secret internals")

    monkeypatch.setattr(CustomerService, "get", boom)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/v1/customers/C-0001", headers=admin)
    assert r.status_code == 500
    assert "secret internals" not in r.text
    assert r.json()["type"].endswith("/internal-error")
    assert r.json()["request_id"] == r.headers["x-request-id"]


# ------------------------------------------------------------------------ pagination


async def test_cursor_pagination_walks_every_item_once(
    client: httpx.AsyncClient, admin: dict[str, str]
) -> None:
    for i in range(7):
        await create_ticket(client, admin, subject=f"Ticket {i}")
    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        params: dict[str, str | int] = {"limit": 3}
        if cursor:
            params["cursor"] = cursor
        body = (await client.get("/v1/tickets", params=params, headers=admin)).json()
        seen += [t["id"] for t in body["items"]]
        pages += 1
        cursor = body["next_cursor"]
        if not cursor:
            break
    assert pages == 3
    assert len(seen) == len(set(seen)) == 7


async def test_pagination_is_stable_when_rows_are_inserted(
    client: httpx.AsyncClient, admin: dict[str, str]
) -> None:
    for i in range(4):
        await create_ticket(client, admin, subject=f"T{i}")
    first = (await client.get("/v1/tickets", params={"limit": 2}, headers=admin)).json()
    await create_ticket(client, admin, subject="late arrival")
    second = (
        await client.get(
            "/v1/tickets", params={"limit": 2, "cursor": first["next_cursor"]}, headers=admin
        )
    ).json()
    assert not {t["id"] for t in first["items"]} & {t["id"] for t in second["items"]}


async def test_pagination_limits_and_bad_cursor(
    client: httpx.AsyncClient, admin: dict[str, str]
) -> None:
    assert (
        await client.get("/v1/tickets", params={"limit": 101}, headers=admin)
    ).status_code == 422
    assert (await client.get("/v1/tickets", params={"limit": 0}, headers=admin)).status_code == 422
    bad = await client.get("/v1/tickets", params={"cursor": "!!!"}, headers=admin)
    assert bad.status_code == 400 and bad.json()["type"].endswith("/invalid-cursor")
    forged = await client.get("/v1/tickets", params={"cursor": "eyJ4IjoxfQ"}, headers=admin)
    assert forged.status_code == 400


# ---------------------------------------------------------------------- idempotency


async def test_idempotent_post_replays_and_creates_once(
    client: httpx.AsyncClient, admin: dict[str, str]
) -> None:
    headers = {**admin, "Idempotency-Key": "abc-123"}
    body = {"subject": "Once", "description": "only once", "customer_id": "C-0001"}
    first = await client.post("/v1/tickets", json=body, headers=headers)
    second = await client.post("/v1/tickets", json=body, headers=headers)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert second.headers["idempotent-replayed"] == "true"
    assert "idempotent-replayed" not in first.headers
    listing = (await client.get("/v1/tickets", headers=admin)).json()["items"]
    assert len(listing) == 1


async def test_idempotency_key_reuse_with_different_body_is_422(
    client: httpx.AsyncClient, admin: dict[str, str]
) -> None:
    headers = {**admin, "Idempotency-Key": "same-key"}
    ok = await client.post("/v1/tags", json={"name": "alpha"}, headers=headers)
    assert ok.status_code == 201
    clash = await client.post("/v1/tags", json={"name": "beta"}, headers=headers)
    assert clash.status_code == 422
    assert clash.json()["type"].endswith("/idempotency-key-reuse")


async def test_idempotency_keys_are_scoped_per_caller(
    client: httpx.AsyncClient, admin: dict[str, str], lead: dict[str, str]
) -> None:
    body = {"name": "scoped"}
    a = await client.post("/v1/tags", json=body, headers={**admin, "Idempotency-Key": "k1"})
    b = await client.post("/v1/tags", json=body, headers={**lead, "Idempotency-Key": "k1"})
    assert a.status_code == 201
    assert b.status_code == 409  # not replayed: it is a fresh request from another caller


async def test_failed_requests_are_not_stored_so_retry_works(
    client: httpx.AsyncClient, admin: dict[str, str]
) -> None:
    headers = {**admin, "Idempotency-Key": "retry-me"}
    bad = await client.post(
        "/v1/tickets",
        json={"subject": "x", "description": "y", "customer_id": "C-9999"},
        headers=headers,
    )
    assert bad.status_code == 404
    good = await client.post(
        "/v1/tickets",
        json={"subject": "x", "description": "y", "customer_id": "C-0001"},
        headers=headers,
    )
    assert good.status_code == 201


async def test_idempotency_records_expire_after_ttl(
    client: httpx.AsyncClient,
    admin: dict[str, str],
    clock: FrozenClock,
) -> None:
    headers = {**admin, "Idempotency-Key": "ttl-key"}
    body = {"name": "ttl-tag"}
    assert (await client.post("/v1/tags", json=body, headers=headers)).status_code == 201
    clock.set(NOW + timedelta(hours=25))
    again = await client.post("/v1/tags", json=body, headers=headers)
    assert again.status_code == 409  # processed afresh; the tag already exists


async def test_overlong_idempotency_key_is_rejected(
    client: httpx.AsyncClient, admin: dict[str, str]
) -> None:
    r = await client.post(
        "/v1/tags", json={"name": "x1"}, headers={**admin, "Idempotency-Key": "k" * 300}
    )
    assert r.status_code == 400


# ----------------------------------------------------------------------------- audit


async def test_every_write_is_audited_with_diff_and_request_id(
    client: httpx.AsyncClient, admin: dict[str, str]
) -> None:
    ticket = await create_ticket(client, admin)
    r = await client.post(
        f"/v1/tickets/{ticket['id']}/assign", json={"assignee_id": "A-0003"}, headers=admin
    )
    rid = r.headers["x-request-id"]
    events = (
        await client.get("/v1/audit/events", params={"request_id": rid}, headers=admin)
    ).json()["items"]
    assert len(events) == 1
    ev = events[0]
    assert ev["action"] == "tickets.assign" and ev["entity_id"] == ticket["id"]
    assert ev["before"]["assignee_id"] is None and ev["after"]["assignee_id"] == "A-0003"
    assert ev["after"]["status"] == "open"
    assert ev["api_key_id"] and ev["agent_id"] == "A-0001"
    one = (await client.get(f"/v1/audit/events/{ev['id']}", headers=admin)).json()
    assert one == ev
    assert (await client.get("/v1/audit/events/AE-nope", headers=admin)).status_code == 404


async def test_audit_filters(client: httpx.AsyncClient, admin: dict[str, str]) -> None:
    t = await create_ticket(client, admin)
    await client.post(f"/v1/tickets/{t['id']}/notes", json={"body": "hello"}, headers=admin)
    by_action = (
        await client.get("/v1/audit/events", params={"action": "comments.add_note"}, headers=admin)
    ).json()
    assert len(by_action["items"]) == 1
    by_entity = (
        await client.get(
            "/v1/audit/events",
            params={"entity_type": "ticket", "entity_id": t["id"]},
            headers=admin,
        )
    ).json()
    assert [e["action"] for e in by_entity["items"]] == ["tickets.create"]
    by_actor = (
        await client.get("/v1/audit/events", params={"actor": "A-0001"}, headers=admin)
    ).json()
    assert len(by_actor["items"]) >= 2
    window = (
        await client.get(
            "/v1/audit/events",
            params={"since": (NOW + timedelta(days=1)).isoformat()},
            headers=admin,
        )
    ).json()
    assert window["items"] == []
    until = (
        await client.get(
            "/v1/audit/events",
            params={"until": (NOW + timedelta(days=1)).isoformat()},
            headers=admin,
        )
    ).json()
    assert len(until["items"]) >= 2


async def test_reads_do_not_write_audit_events(
    client: httpx.AsyncClient, admin: dict[str, str]
) -> None:
    await client.get("/v1/customers", headers=admin)
    await client.get("/v1/tickets", headers=admin)
    events = (await client.get("/v1/audit/events", headers=admin)).json()["items"]
    assert {e["action"] for e in events} == {"api_keys.create"}  # only the fixture's key creation


async def test_access_log_has_route_template_and_no_secrets(
    client: httpx.AsyncClient,
    admin: dict[str, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    from helpdesk.logging import configure_logging

    configure_logging("INFO", json_logs=True)
    token = admin["Authorization"]
    await client.get("/v1/customers/C-0001", headers=admin)
    out = capsys.readouterr().out
    line = next(ln for ln in out.splitlines() if '"request"' in ln)
    import json

    record = json.loads(line)
    assert record["route"] == "/v1/customers/{customer_id}"
    assert record["method"] == "GET" and record["status"] == 200
    assert record["api_key_id"].startswith("K-") and record["request_id"]
    assert "duration_ms" in record
    assert token.split(" ")[1] not in out
