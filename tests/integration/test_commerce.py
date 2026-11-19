"""Customers, orders and refunds."""

from __future__ import annotations

from typing import Any

import httpx

from tests.conftest import create_ticket

H = dict[str, str]


async def refund(
    client: httpx.AsyncClient, headers: H, order: str, amount: str, **extra: Any
) -> httpx.Response:
    return await client.post(
        "/v1/refunds",
        json={"order_id": order, "amount": amount, "reason": "damaged", **extra},
        headers=headers,
    )


# ---------------------------------------------------------------------------- refunds


async def test_small_refund_is_auto_approved(client: httpx.AsyncClient, agent: H) -> None:
    r = await refund(client, agent, "O-0001", "100.00")
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "approved" and body["amount"] == "100.00"
    assert body["requested_by_agent_id"] == "A-0003" and body["decided_at"]
    assert body["customer_id"] == "C-0001"


async def test_refund_at_threshold_is_auto_but_above_is_pending(
    client: httpx.AsyncClient, agent: H
) -> None:
    exact = await refund(client, agent, "O-0003", "500.00")
    assert exact.json()["status"] == "approved"
    above = await refund(client, agent, "O-0003", "300.00")
    assert above.status_code == 201 and above.json()["status"] == "approved"
    # 800 total, 800 committed -> nothing left
    assert (await refund(client, agent, "O-0003", "0.01")).status_code == 422


async def test_large_refund_needs_lead_approval(
    client: httpx.AsyncClient, agent: H, lead: H
) -> None:
    created = await refund(client, agent, "O-0003", "650.00")
    assert created.status_code == 201 and created.json()["status"] == "pending_approval"
    rid = created.json()["id"]
    denied = await client.post(f"/v1/refunds/{rid}/approve", json={}, headers=agent)
    assert denied.status_code == 403 and denied.json()["type"].endswith("/agent-role-required")
    ok = await client.post(f"/v1/refunds/{rid}/approve", json={"note": "verified"}, headers=lead)
    assert ok.status_code == 200
    assert ok.json()["status"] == "approved" and ok.json()["decided_by_agent_id"] == "A-0002"
    again = await client.post(f"/v1/refunds/{rid}/approve", headers=lead)
    assert again.status_code == 409


async def test_approval_needs_a_key_linked_to_an_agent(
    client: httpx.AsyncClient, agent: H, make_key: Any
) -> None:
    rid = (await refund(client, agent, "O-0003", "650.00")).json()["id"]
    token = await make_key(["refunds:write"])
    r = await client.post(
        f"/v1/refunds/{rid}/approve", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 403 and "linked to an agent" in r.json()["detail"]


async def test_reject_and_cancel_release_the_reserved_amount(
    client: httpx.AsyncClient, agent: H, lead: H
) -> None:
    first = (await refund(client, agent, "O-0003", "700.00")).json()["id"]
    blocked = await refund(client, agent, "O-0003", "200.00")
    assert blocked.status_code == 422 and blocked.json()["type"].endswith("/refund-exceeds-balance")
    assert blocked.json()["refundable"] == "100.00"
    rejected = await client.post(
        f"/v1/refunds/{first}/reject", json={"note": "not damaged"}, headers=lead
    )
    assert (
        rejected.json()["status"] == "rejected"
        and rejected.json()["decision_note"] == "not damaged"
    )
    second = await refund(client, agent, "O-0003", "800.00")
    assert second.json()["status"] == "pending_approval"
    cancelled = await client.post(f"/v1/refunds/{second.json()['id']}/cancel", headers=agent)
    assert cancelled.json()["status"] == "cancelled"
    assert (await refund(client, agent, "O-0003", "800.00")).status_code == 201
    assert (
        await client.post(f"/v1/refunds/{first}/reject", json={"note": "x"}, headers=lead)
    ).status_code == 409
    assert (await client.post(f"/v1/refunds/{first}/cancel", headers=agent)).status_code == 409
    assert (
        await client.post(f"/v1/refunds/{first}/reject", json={}, headers=lead)
    ).status_code == 422
    assert (
        await client.post(f"/v1/refunds/{first}/reject", json={"note": "x"}, headers=agent)
    ).status_code == 403


async def test_refund_policy_window_and_status(client: httpx.AsyncClient, agent: H) -> None:
    old = await refund(client, agent, "O-0002", "10.00")  # delivered 40 days ago
    assert old.status_code == 422 and old.json()["type"].endswith("/refund-not-eligible")
    assert old.json()["window_days"] == 30
    placed = await refund(client, agent, "O-0004", "10.00")
    assert placed.status_code == 422 and placed.json()["order_status"] == "placed"
    shipped = await refund(client, agent, "O-0005", "20.00")
    assert shipped.status_code == 201  # shipped orders are refundable
    assert (await refund(client, agent, "O-9999", "1.00")).status_code == 404
    assert (await refund(client, agent, "O-0001", "1.00", ticket_id="T-9999")).status_code == 404
    over = await refund(client, agent, "O-0001", "100.01")
    assert over.status_code == 422 and over.json()["type"].endswith("/refund-exceeds-balance")
    assert (await refund(client, agent, "O-0001", "0")).status_code == 422
    assert (await refund(client, agent, "O-0001", "-5")).status_code == 422
    assert (await refund(client, agent, "O-0001", "1.234")).status_code == 422


async def test_refund_prior_refunds_count_against_balance(
    client: httpx.AsyncClient, agent: H
) -> None:
    assert (await refund(client, agent, "O-0001", "60.00")).status_code == 201
    assert (await refund(client, agent, "O-0001", "40.00")).status_code == 201
    assert (await refund(client, agent, "O-0001", "0.01")).status_code == 422
    listing = (await client.get("/v1/orders/O-0001/refunds", headers=agent)).json()
    assert [r["amount"] for r in listing] == ["60.00", "40.00"]


async def test_refund_links_to_ticket_and_lists_filter(client: httpx.AsyncClient, agent: H) -> None:
    t = await create_ticket(client, agent)
    r = await refund(client, agent, "O-0001", "10.00", ticket_id=t["id"])
    assert r.json()["ticket_id"] == t["id"]
    await refund(client, agent, "O-0003", "700.00")
    by_status = (
        await client.get("/v1/refunds", params={"status": "pending_approval"}, headers=agent)
    ).json()["items"]
    assert [x["order_id"] for x in by_status] == ["O-0003"]
    by_order = (
        await client.get("/v1/refunds", params={"order_id": "O-0001"}, headers=agent)
    ).json()["items"]
    assert len(by_order) == 1
    by_customer = (
        await client.get("/v1/refunds", params={"customer_id": "C-0001"}, headers=agent)
    ).json()["items"]
    assert len(by_customer) == 2
    assert (await client.get(f"/v1/refunds/{r.json()['id']}", headers=agent)).status_code == 200
    assert (await client.get("/v1/refunds/R-9999", headers=agent)).status_code == 404


async def test_refund_idempotency_prevents_double_refund(
    client: httpx.AsyncClient, agent: H
) -> None:
    headers = {**agent, "Idempotency-Key": "refund-once"}
    body = {"order_id": "O-0001", "amount": "100.00", "reason": "damaged"}
    a = await client.post("/v1/refunds", json=body, headers=headers)
    b = await client.post("/v1/refunds", json=body, headers=headers)
    assert a.json() == b.json() and b.headers["idempotent-replayed"] == "true"
    metrics = (await client.get("/metrics")).text
    assert 'route="idempotent_replay"' in metrics
    assert len((await client.get("/v1/refunds", headers=agent)).json()["items"]) == 1


async def test_refund_settings_change_policy(client: httpx.AsyncClient, agent: H, admin: H) -> None:
    updated = await client.patch(
        "/v1/admin/settings",
        json={"refund_approval_threshold": "50.00", "refund_window_days": 60},
        headers=admin,
    )
    assert updated.json()["refund_approval_threshold"] == "50.00"
    assert updated.json()["overridden"] == ["refund_approval_threshold", "refund_window_days"]
    assert (await refund(client, agent, "O-0001", "60.00")).json()["status"] == "pending_approval"
    assert (await refund(client, agent, "O-0002", "10.00")).status_code == 201  # 40 days < 60
    current = (await client.get("/v1/admin/settings", headers=admin)).json()
    assert current["refund_window_days"] == 60 and current["reopen_window_days"] == 7
    again = await client.patch("/v1/admin/settings", json={"refund_window_days": 45}, headers=admin)
    assert again.json()["refund_window_days"] == 45
    assert (
        await client.patch("/v1/admin/settings", json={"refund_window_days": 0}, headers=admin)
    ).status_code == 422


# ----------------------------------------------------------------------------- orders


async def test_order_reads(client: httpx.AsyncClient, admin: H) -> None:
    o = (await client.get("/v1/orders/O-0001", headers=admin)).json()
    assert o["total"] == "100.00" and o["status"] == "delivered" and len(o["items"]) == 2
    items = (await client.get("/v1/orders/O-0001/items", headers=admin)).json()
    assert [i["line_total"] for i in items] == ["60.00", "40.00"]
    assert (await client.get("/v1/orders/O-9999", headers=admin)).status_code == 404
    assert (await client.get("/v1/orders/O-9999/refunds", headers=admin)).status_code == 404
    mine = (
        await client.get(
            "/v1/orders", params={"customer_id": "C-0001", "status": "delivered"}, headers=admin
        )
    ).json()["items"]
    assert [x["id"] for x in mine] == ["O-0003", "O-0002", "O-0001"]  # same placed_at: id desc
    window = (
        await client.get(
            "/v1/orders",
            params={
                "placed_after": "2026-01-01T00:00:00Z",
                "placed_before": "2026-12-01T00:00:00Z",
            },
            headers=admin,
        )
    ).json()
    assert len(window["items"]) == 6


async def test_order_create_and_fulfilment(client: httpx.AsyncClient, admin: H) -> None:
    body = {
        "customer_id": "C-0001",
        "items": [{"sku": "A", "name": "Thing", "quantity": 2, "unit_price": "19.99"}],
    }
    created = await client.post("/v1/orders", json=body, headers=admin)
    assert created.status_code == 201
    oid = created.json()["id"]
    assert created.json()["total"] == "39.98" and created.json()["status"] == "placed"
    assert (await client.post(f"/v1/orders/{oid}/deliver", headers=admin)).status_code == 409
    shipped = await client.post(f"/v1/orders/{oid}/ship", headers=admin)
    assert shipped.json()["status"] == "shipped" and shipped.json()["shipped_at"]
    assert (await client.post(f"/v1/orders/{oid}/cancel", headers=admin)).status_code == 409
    assert (await client.post(f"/v1/orders/{oid}/ship", headers=admin)).status_code == 409
    delivered = await client.post(f"/v1/orders/{oid}/deliver", headers=admin)
    assert delivered.json()["delivered_at"]
    cancel = await client.post("/v1/orders", json=body, headers=admin)
    cancelled = await client.post(f"/v1/orders/{cancel.json()['id']}/cancel", headers=admin)
    assert cancelled.json()["status"] == "cancelled" and cancelled.json()["cancelled_at"]
    assert (
        await client.post("/v1/orders", json={**body, "customer_id": "C-9"}, headers=admin)
    ).status_code == 404
    assert (
        await client.post("/v1/orders", json={**body, "items": []}, headers=admin)
    ).status_code == 422
    await client.post(
        "/v1/customers/C-0002/merge", json={"target_customer_id": "C-0001"}, headers=admin
    )
    assert (
        await client.post("/v1/orders", json={**body, "customer_id": "C-0002"}, headers=admin)
    ).status_code == 409


# --------------------------------------------------------------------------- customers


async def test_customer_crud_and_search(client: httpx.AsyncClient, admin: H) -> None:
    created = await client.post(
        "/v1/customers",
        json={"name": "New Person", "email": "New@Example.com", "tier": "vip"},
        headers=admin,
    )
    assert created.status_code == 201
    cid = created.json()["id"]
    assert created.json()["email"] == "new@example.com" and cid == "C-0003"
    dup = await client.post(
        "/v1/customers", json={"name": "X", "email": "new@example.com"}, headers=admin
    )
    assert dup.status_code == 409 and dup.json()["field"] == "email"
    upd = await client.patch(
        f"/v1/customers/{cid}", json={"company": "Acme", "phone": "123"}, headers=admin
    )
    assert upd.json()["company"] == "Acme"
    taken = await client.patch(
        f"/v1/customers/{cid}", json={"email": "cora@example.com"}, headers=admin
    )
    assert taken.status_code == 409
    same = await client.patch(
        f"/v1/customers/{cid}", json={"email": "new@example.com"}, headers=admin
    )
    assert same.status_code == 200
    found = (await client.get("/v1/customers", params={"q": "acme"}, headers=admin)).json()["items"]
    assert [c["id"] for c in found] == [cid]
    assert (await client.get("/v1/customers", params={"tier": "vip"}, headers=admin)).json()[
        "items"
    ][0]["id"] == cid
    assert (
        len(
            (await client.get("/v1/customers", params={"status": "active"}, headers=admin)).json()[
                "items"
            ]
        )
        == 3
    )
    assert (await client.get("/v1/customers/C-9999", headers=admin)).status_code == 404
    assert (
        await client.patch(f"/v1/customers/{cid}", json={"email": "bad"}, headers=admin)
    ).status_code == 422


async def test_customer_related_lists_and_summary(client: httpx.AsyncClient, admin: H) -> None:
    t = await create_ticket(client, admin)
    await client.post(f"/v1/tickets/{t['id']}/status", json={"status": "open"}, headers=admin)
    await create_ticket(client, admin)
    await client.post(
        "/v1/refunds", json={"order_id": "O-0001", "amount": "5.00", "reason": "r"}, headers=admin
    )
    tickets = (await client.get("/v1/customers/C-0001/tickets", headers=admin)).json()["items"]
    assert len(tickets) == 2
    only_open = (
        await client.get("/v1/customers/C-0001/tickets", params={"status": "open"}, headers=admin)
    ).json()["items"]
    assert len(only_open) == 1
    assert (
        len((await client.get("/v1/customers/C-0001/orders", headers=admin)).json()["items"]) == 5
    )
    assert (
        len(
            (
                await client.get(
                    "/v1/customers/C-0001/orders", params={"status": "placed"}, headers=admin
                )
            ).json()["items"]
        )
        == 1
    )
    assert (
        len((await client.get("/v1/customers/C-0001/refunds", headers=admin)).json()["items"]) == 1
    )
    summary = (await client.get("/v1/customers/C-0001/summary", headers=admin)).json()
    assert (summary["ticket_count"], summary["open_ticket_count"], summary["order_count"]) == (
        2,
        2,
        5,
    )
    assert (await client.get("/v1/customers/C-9/tickets", headers=admin)).status_code == 404
    assert (await client.get("/v1/customers/C-9/orders", headers=admin)).status_code == 404
    assert (await client.get("/v1/customers/C-9/refunds", headers=admin)).status_code == 404


async def test_customer_merge_moves_everything_and_is_idempotent(
    client: httpx.AsyncClient, admin: H
) -> None:
    t = await create_ticket(client, admin, customer_id="C-0002")
    merged = await client.post(
        "/v1/customers/C-0002/merge", json={"target_customer_id": "C-0001"}, headers=admin
    )
    assert merged.status_code == 200 and merged.json()["id"] == "C-0001"
    source = (await client.get("/v1/customers/C-0002", headers=admin)).json()
    assert source["status"] == "merged" and source["merged_into_id"] == "C-0001"
    assert (await client.get(f"/v1/tickets/{t['id']}", headers=admin)).json()[
        "customer_id"
    ] == "C-0001"
    assert (await client.get("/v1/orders/O-0006", headers=admin)).json()["customer_id"] == "C-0001"
    again = await client.post(
        "/v1/customers/C-0002/merge", json={"target_customer_id": "C-0001"}, headers=admin
    )
    assert again.status_code == 200
    assert (
        await client.patch("/v1/customers/C-0002", json={"name": "x"}, headers=admin)
    ).status_code == 409
    assert (
        await client.post(
            "/v1/customers/C-0002/merge", json={"target_customer_id": "C-0001"}, headers=admin
        )
    ).status_code == 200
    third = await client.post(
        "/v1/customers", json={"name": "Third", "email": "third@example.com"}, headers=admin
    )
    wrong = await client.post(
        "/v1/customers/C-0002/merge", json={"target_customer_id": third.json()["id"]}, headers=admin
    )
    assert wrong.status_code == 409
    into_merged = await client.post(
        f"/v1/customers/{third.json()['id']}/merge",
        json={"target_customer_id": "C-0002"},
        headers=admin,
    )
    assert into_merged.status_code == 409
    itself = await client.post(
        "/v1/customers/C-0001/merge", json={"target_customer_id": "C-0001"}, headers=admin
    )
    assert itself.status_code == 422
    events = (
        await client.get("/v1/audit/events", params={"action": "customers.merge"}, headers=admin)
    ).json()["items"]
    assert len(events) == 1  # the idempotent repeat wrote nothing
    assert events[0]["after"]["moved"] == {"tickets": 1, "orders": 1, "refunds": 0}
