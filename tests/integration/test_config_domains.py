"""SLA, knowledge base, webhooks, API keys and reports."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx

from tests.conftest import NOW, FakeSender, create_ticket

H = dict[str, str]


# ------------------------------------------------------------------------------- SLA


async def test_sla_policy_crud(client: httpx.AsyncClient, admin: H, session_factory: Any) -> None:
    listing = (await client.get("/v1/sla/policies", headers=admin)).json()
    assert [p["priority"] for p in listing] == ["urgent", "high", "normal", "low"]
    assert (await client.get("/v1/sla/policies/SLA-001", headers=admin)).json()[
        "first_response_minutes"
    ] == 30
    assert (await client.get("/v1/sla/policies/SLA-999", headers=admin)).status_code == 404
    dup = await client.post(
        "/v1/sla/policies",
        json={"name": "d", "priority": "low", "first_response_minutes": 1, "resolution_minutes": 2},
        headers=admin,
    )
    assert dup.status_code == 409
    upd = await client.patch(
        "/v1/sla/policies/SLA-004", json={"first_response_minutes": 100}, headers=admin
    )
    assert upd.json()["first_response_minutes"] == 100
    bad = await client.patch(
        "/v1/sla/policies/SLA-004", json={"resolution_minutes": 5}, headers=admin
    )
    assert bad.status_code == 422


async def test_sla_policy_target_validation(client: httpx.AsyncClient, admin: H) -> None:
    bad = await client.post(
        "/v1/sla/policies",
        json={
            "name": "d",
            "priority": "low",
            "first_response_minutes": 50,
            "resolution_minutes": 10,
        },
        headers=admin,
    )
    assert bad.status_code == 422
    r = await client.patch(
        "/v1/sla/policies/SLA-001", json={"first_response_minutes": 999}, headers=admin
    )
    assert r.status_code == 422


async def test_sla_evaluation_states_over_time(
    client: httpx.AsyncClient, admin: H, clock: Any
) -> None:
    t = await create_ticket(client, admin, priority="high", assignee_id="A-0003")  # 60 / 480 min
    tid = t["id"]

    async def evaluate() -> dict[str, Any]:
        r = await client.get(f"/v1/tickets/{tid}/sla", headers=admin)
        assert r.status_code == 200
        data: dict[str, Any] = r.json()
        return data

    ev = await evaluate()
    assert (
        ev["first_response"]["state"] == "pending"
        and ev["first_response"]["minutes_remaining"] == 60
    )
    assert ev["breached"] is False and ev["policy_id"] == "SLA-002"
    clock.set(NOW + timedelta(minutes=55))
    assert (await evaluate())["first_response"]["state"] == "at_risk"
    await client.post(f"/v1/tickets/{tid}/replies", json={"body": "hi"}, headers=admin)
    ev = await evaluate()
    assert (
        ev["first_response"]["state"] == "met" and ev["first_response"]["minutes_remaining"] is None
    )
    clock.set(NOW + timedelta(minutes=500))
    ev = await evaluate()
    assert ev["resolution"]["state"] == "breached" and ev["resolution"]["minutes_remaining"] < 0
    assert ev["breached"] is True
    await client.post(f"/v1/tickets/{tid}/close", headers=admin)
    ev = await evaluate()
    assert ev["resolution"]["state"] == "breached" and ev["resolution"]["achieved_at"]
    assert (await client.get("/v1/tickets/T-9999/sla", headers=admin)).status_code == 404


async def test_sla_late_first_response_is_a_breach(
    client: httpx.AsyncClient, admin: H, clock: Any
) -> None:
    t = await create_ticket(client, admin, priority="urgent")
    clock.set(NOW + timedelta(minutes=45))
    await client.post(f"/v1/tickets/{t['id']}/replies", json={"body": "late"}, headers=admin)
    ev = (await client.get(f"/v1/tickets/{t['id']}/sla", headers=admin)).json()
    assert ev["first_response"]["state"] == "breached" and ev["first_response"]["achieved_at"]


async def test_sla_breach_listing_and_report(
    client: httpx.AsyncClient, admin: H, clock: Any
) -> None:
    a = await create_ticket(client, admin, priority="urgent", team_id="TM-001")
    await create_ticket(client, admin, priority="low")
    clock.set(NOW + timedelta(hours=1))
    breaches = (await client.get("/v1/sla/breaches", headers=admin)).json()["items"]
    assert [t["id"] for t in breaches] == [a["id"]]
    assert (await client.get("/v1/sla/breaches", params={"priority": "low"}, headers=admin)).json()[
        "items"
    ] == []
    assert (
        len(
            (
                await client.get("/v1/sla/breaches", params={"team_id": "TM-001"}, headers=admin)
            ).json()["items"]
        )
        == 1
    )
    report = (await client.get("/v1/reports/sla-compliance", headers=admin)).json()
    rows = {r["priority"]: r for r in report["by_priority"]}
    assert (
        rows["urgent"]["first_response_breached"] == 1
        and rows["urgent"]["first_response_compliance_pct"] == 0.0
    )
    assert rows["low"]["first_response_compliance_pct"] == 100.0
    assert report["overall_first_response_compliance_pct"] == 50.0


async def test_ticket_without_policy_is_untracked(client: httpx.AsyncClient, admin: H) -> None:
    for pid in ("SLA-001", "SLA-002", "SLA-003", "SLA-004"):
        await client.patch(f"/v1/sla/policies/{pid}", json={"active": False}, headers=admin)
    t = await create_ticket(client, admin)
    assert t["first_response_due_at"] is None
    ev = (await client.get(f"/v1/tickets/{t['id']}/sla", headers=admin)).json()
    assert ev["first_response"]["state"] == "untracked"


# ---------------------------------------------------------------------------------- KB


async def test_kb_lifecycle_and_search(client: httpx.AsyncClient, admin: H, agent: H) -> None:
    a = await client.post(
        "/v1/kb/articles",
        json={
            "title": "Reset your password",
            "category": "account",
            "body": "Use the reset link. Passwords expire.",
        },
        headers=agent,
    )
    assert (
        a.status_code == 201
        and a.json()["status"] == "draft"
        and a.json()["slug"] == "reset-your-password"
    )
    assert a.json()["author_id"] == "A-0003"
    aid = a.json()["id"]
    b = await client.post(
        "/v1/kb/articles",
        json={"title": "Reset your password", "category": "account", "body": "Second."},
        headers=admin,
    )
    assert b.json()["slug"] == "reset-your-password-2"
    hidden = (
        await client.get("/v1/kb/articles/search", params={"q": "reset"}, headers=admin)
    ).json()
    assert hidden == []
    drafts = (
        await client.get(
            "/v1/kb/articles/search", params={"q": "reset", "include_drafts": True}, headers=admin
        )
    ).json()
    assert drafts[0]["id"] == aid and drafts[0]["score"] > drafts[1]["score"]
    published = await client.post(f"/v1/kb/articles/{aid}/publish", headers=admin)
    assert published.json()["status"] == "published" and published.json()["published_at"]
    assert (await client.post(f"/v1/kb/articles/{aid}/publish", headers=admin)).status_code == 200
    found = (
        await client.get(
            "/v1/kb/articles/search",
            params={"q": "password reset", "category": "account"},
            headers=admin,
        )
    ).json()
    assert [h["id"] for h in found] == [aid]
    assert "reset link" in found[0]["snippet"]
    assert (
        await client.get("/v1/kb/articles/search", params={"q": "!!"}, headers=admin)
    ).json() == []
    assert (
        await client.get("/v1/kb/articles/search", params={"q": "zzzz"}, headers=admin)
    ).json() == []
    upd = await client.patch(f"/v1/kb/articles/{aid}", json={"body": "New body"}, headers=admin)
    assert upd.json()["body"] == "New body" and upd.json()["slug"] == "reset-your-password"
    listing = (
        await client.get("/v1/kb/articles", params={"status": "published"}, headers=admin)
    ).json()["items"]
    assert [x["id"] for x in listing] == [aid]
    assert (
        len(
            (
                await client.get(
                    "/v1/kb/articles", params={"q": "second", "category": "account"}, headers=admin
                )
            ).json()["items"]
        )
        == 1
    )
    unpub = await client.post(f"/v1/kb/articles/{aid}/unpublish", headers=admin)
    assert unpub.json()["status"] == "draft" and unpub.json()["published_at"] is None
    assert (await client.get(f"/v1/kb/articles/{aid}", headers=admin)).status_code == 200
    assert (await client.delete(f"/v1/kb/articles/{aid}", headers=admin)).status_code == 204
    assert (await client.get(f"/v1/kb/articles/{aid}", headers=admin)).status_code == 404
    assert (await client.delete(f"/v1/kb/articles/{aid}", headers=admin)).status_code == 404


# --------------------------------------------------------------------------- webhooks


async def test_webhook_lifecycle_and_test_delivery(
    client: httpx.AsyncClient, admin: H, sender: FakeSender
) -> None:
    created = await client.post(
        "/v1/webhooks",
        json={
            "url": "https://hooks.example.com/a",
            "events": ["ticket.created", "refund.approved", "ticket.created"],
        },
        headers=admin,
    )
    assert created.status_code == 201
    hook = created.json()
    assert hook["secret"].startswith("whsec_") and hook["events"] == [
        "refund.approved",
        "ticket.created",
    ]
    fetched = (await client.get(f"/v1/webhooks/{hook['id']}", headers=admin)).json()
    assert "secret" not in fetched
    assert "secret" not in (await client.get("/v1/webhooks", headers=admin)).json()[0]

    delivery = await client.post(f"/v1/webhooks/{hook['id']}/test", headers=admin)
    assert delivery.status_code == 200 and delivery.json()["success"] is True
    call = sender.calls[0]
    assert call["url"] == "https://hooks.example.com/a"
    assert call["headers"]["X-Helpdesk-Signature"].startswith("sha256=")
    assert b'"webhook.test"' in call["body"]
    from helpdesk.services.webhooks import sign_payload

    assert call["headers"]["X-Helpdesk-Signature"] == sign_payload(hook["secret"], call["body"])

    from helpdesk.integrations.webhook_sender import DeliveryResult

    sender.result = DeliveryResult(False, 500, "Receiver answered HTTP 500", 7)
    failed = await client.post(f"/v1/webhooks/{hook['id']}/test", headers=admin)
    assert (
        failed.status_code == 200
        and failed.json()["success"] is False
        and failed.json()["status_code"] == 500
    )
    history = (await client.get(f"/v1/webhooks/{hook['id']}/deliveries", headers=admin)).json()[
        "items"
    ]
    assert [d["success"] for d in history] == [False, True]

    upd = await client.patch(
        f"/v1/webhooks/{hook['id']}",
        json={"active": False, "events": ["ticket.closed"], "url": "https://hooks.example.com/b"},
        headers=admin,
    )
    assert upd.json()["active"] is False and upd.json()["events"] == ["ticket.closed"]
    assert (await client.delete(f"/v1/webhooks/{hook['id']}", headers=admin)).status_code == 204
    assert (await client.get(f"/v1/webhooks/{hook['id']}", headers=admin)).status_code == 404
    assert (await client.post("/v1/webhooks/WH-999/test", headers=admin)).status_code == 404
    assert (await client.get("/v1/webhooks/WH-999/deliveries", headers=admin)).status_code == 404


async def test_webhook_validation_and_ssrf_guard(client: httpx.AsyncClient, admin: H) -> None:
    async def create(url: str) -> httpx.Response:
        return await client.post(
            "/v1/webhooks", json={"url": url, "events": ["ticket.created"]}, headers=admin
        )

    for bad in (
        "http://localhost/x",
        "http://127.0.0.1/x",
        "http://10.0.0.5/x",
        "http://169.254.169.254/latest",
        "http://db.internal/x",
        "http://printer.local/x",
    ):
        assert (await create(bad)).status_code == 422, bad
    assert (await create("ftp://example.com/x")).status_code == 422
    assert (await create("https://8.8.8.8/hook")).status_code == 201
    assert (
        await client.post(
            "/v1/webhooks", json={"url": "https://example.com", "events": ["nope"]}, headers=admin
        )
    ).status_code == 422
    assert (
        await client.post(
            "/v1/webhooks", json={"url": "https://example.com", "events": []}, headers=admin
        )
    ).status_code == 422
    hook = (await create("https://example.com/ok")).json()
    assert (
        await client.patch(
            f"/v1/webhooks/{hook['id']}", json={"url": "http://localhost/y"}, headers=admin
        )
    ).status_code == 422


# ------------------------------------------------------------------------------ keys


async def test_api_key_admin(client: httpx.AsyncClient, admin: H) -> None:
    created = await client.post(
        "/v1/admin/api-keys",
        json={
            "name": "ro",
            "scopes": ["tickets:read"],
            "agent_id": "A-0003",
            "expires_in_days": 30,
        },
        headers=admin,
    )
    assert created.status_code == 201
    body = created.json()
    assert body["token"].startswith("hd_") and body["scopes"] == ["tickets:read"]
    assert body["expires_at"].startswith("2026-07-01")
    got = (await client.get(f"/v1/admin/api-keys/{body['id']}", headers=admin)).json()
    assert "token" not in got and got["prefix"] in body["token"]
    listing = (await client.get("/v1/admin/api-keys", headers=admin)).json()
    assert body["id"] in [k["id"] for k in listing]
    assert (await client.get("/v1/admin/api-keys/K-999", headers=admin)).status_code == 404
    ro = {"Authorization": f"Bearer {body['token']}"}
    assert (await client.get("/v1/tickets", headers=ro)).status_code == 200
    assert (await client.get("/v1/customers", headers=ro)).status_code == 403
    bad = await client.post(
        "/v1/admin/api-keys", json={"name": "x", "scopes": ["root"]}, headers=admin
    )
    assert bad.status_code == 422 and bad.json()["unknown"] == ["root"]
    no_agent = await client.post(
        "/v1/admin/api-keys",
        json={"name": "x", "scopes": ["admin"], "agent_id": "A-9"},
        headers=admin,
    )
    assert no_agent.status_code == 404


# ----------------------------------------------------------------------------- reports


async def test_reports(client: httpx.AsyncClient, admin: H, clock: Any) -> None:
    a = await create_ticket(client, admin, priority="high", assignee_id="A-0003", team_id="TM-001")
    clock.set(NOW + timedelta(days=1))
    b = await create_ticket(client, admin, priority="low", assignee_id="A-0003")
    c = await create_ticket(client, admin, priority="low", assignee_id="A-0004")
    await create_ticket(client, admin, priority="normal")
    await client.post(f"/v1/tickets/{c['id']}/close", headers=admin)
    await client.post(f"/v1/tickets/{b['id']}/close", headers=admin)
    await client.post(f"/v1/tickets/{b['id']}/csat", json={"score": 5}, headers=admin)
    await client.post(f"/v1/tickets/{c['id']}/csat", json={"score": 2}, headers=admin)

    by_day = (await client.get("/v1/reports/ticket-volume", headers=admin)).json()
    assert by_day["total"] == 4
    assert [(x["key"], x["count"]) for x in by_day["buckets"]] == [
        ("2026-06-01", 1),
        ("2026-06-02", 3),
    ]
    by_prio = (
        await client.get(
            "/v1/reports/ticket-volume", params={"group_by": "priority"}, headers=admin
        )
    ).json()
    assert {x["key"]: x["count"] for x in by_prio["buckets"]} == {"high": 1, "low": 2, "normal": 1}
    by_status = (
        await client.get("/v1/reports/ticket-volume", params={"group_by": "status"}, headers=admin)
    ).json()
    assert {x["key"]: x["count"] for x in by_status["buckets"]} == {
        "closed": 2,
        "new": 1,
        "open": 1,
    }
    by_team = (
        await client.get("/v1/reports/ticket-volume", params={"group_by": "team"}, headers=admin)
    ).json()
    assert {x["key"]: x["count"] for x in by_team["buckets"]} == {"TM-001": 1, "unassigned": 3}
    ranged = (
        await client.get(
            "/v1/reports/ticket-volume",
            params={
                "start": (NOW + timedelta(hours=1)).isoformat(),
                "end": (NOW + timedelta(days=2)).isoformat(),
            },
            headers=admin,
        )
    ).json()
    assert ranged["total"] == 3
    assert (
        await client.get("/v1/reports/ticket-volume", params={"group_by": "mood"}, headers=admin)
    ).status_code == 422

    load = (await client.get("/v1/reports/agent-workload", headers=admin)).json()
    assert load["total_active_tickets"] == 1
    assert load["agents"][0]["agent_id"] == "A-0003" and load["agents"][0]["by_status"] == {
        "open": 1
    }
    assert a["id"]

    csat = (await client.get("/v1/reports/csat", headers=admin)).json()
    assert (
        csat["responses"] == 2
        and csat["average_score"] == 3.5
        and csat["distribution"] == {"2": 1, "5": 1}
    )
    empty = (
        await client.get("/v1/reports/csat", params={"end": NOW.isoformat()}, headers=admin)
    ).json()
    assert empty["average_score"] is None
    backlog = (await client.get("/v1/reports/backlog", headers=admin)).json()
    assert backlog["total_active"] == 2
    assert {(x["status"], x["priority"]) for x in backlog["cells"]} == {
        ("open", "high"),
        ("new", "normal"),
    }
