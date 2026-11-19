"""Tickets, comments, tags, macros and the rules around them."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx

from tests.conftest import NOW, create_ticket

H = dict[str, str]


async def test_create_ticket_defaults_and_sla_due_dates(
    client: httpx.AsyncClient, admin: H
) -> None:
    t = await create_ticket(client, admin, priority="high", tags=["Billing", "vip"])
    assert t["id"] == "T-0001" and t["status"] == "new" and t["assignee_id"] is None
    assert t["tags"] == ["billing", "vip"]
    assert t["first_response_due_at"].startswith("2026-06-01T13:00")  # high: 60 minutes
    assert t["resolution_due_at"].startswith("2026-06-01T20:00")  # high: 480 minutes


async def test_create_assigned_ticket_starts_open(client: httpx.AsyncClient, admin: H) -> None:
    t = await create_ticket(
        client, admin, assignee_id="A-0003", team_id="TM-001", order_id="O-0001"
    )
    assert t["status"] == "open" and t["order_id"] == "O-0001"


async def test_create_ticket_validation_rules(client: httpx.AsyncClient, admin: H) -> None:
    base = {"subject": "s", "description": "d"}
    r = await client.post("/v1/tickets", json={**base, "customer_id": "C-9"}, headers=admin)
    assert r.status_code == 404
    r = await client.post(
        "/v1/tickets", json={**base, "customer_id": "C-0001", "assignee_id": "A-9"}, headers=admin
    )
    assert r.status_code == 404
    r = await client.post(
        "/v1/tickets", json={**base, "customer_id": "C-0001", "team_id": "TM-9"}, headers=admin
    )
    assert r.status_code == 404
    r = await client.post(
        "/v1/tickets", json={**base, "customer_id": "C-0001", "order_id": "O-0006"}, headers=admin
    )
    assert r.status_code == 422  # order belongs to someone else
    r = await client.post(
        "/v1/tickets", json={**base, "customer_id": "C-0001", "order_id": "O-9999"}, headers=admin
    )
    assert r.status_code == 404
    r = await client.post(
        "/v1/tickets", json={**base, "customer_id": "C-0001", "tags": ["Bad Tag!"]}, headers=admin
    )
    assert r.status_code == 422
    await client.post("/v1/agents/A-0003/deactivate", headers=admin)
    r = await client.post(
        "/v1/tickets",
        json={**base, "customer_id": "C-0001", "assignee_id": "A-0003"},
        headers=admin,
    )
    assert r.status_code == 422 and "deactivated" in r.json()["detail"]


async def test_cannot_create_ticket_for_merged_customer(
    client: httpx.AsyncClient, admin: H
) -> None:
    await client.post(
        "/v1/customers/C-0002/merge", json={"target_customer_id": "C-0001"}, headers=admin
    )
    r = await client.post(
        "/v1/tickets",
        json={"subject": "s", "description": "d", "customer_id": "C-0002"},
        headers=admin,
    )
    assert r.status_code == 409


async def test_update_ticket_and_priority_recomputes_sla(
    client: httpx.AsyncClient, admin: H
) -> None:
    t = await create_ticket(client, admin)
    r = await client.patch(
        f"/v1/tickets/{t['id']}", json={"priority": "urgent", "subject": "New"}, headers=admin
    )
    body = r.json()
    assert body["subject"] == "New" and body["priority"] == "urgent"
    assert body["first_response_due_at"].startswith("2026-06-01T12:30")
    ok = await client.patch(f"/v1/tickets/{t['id']}", json={"order_id": "O-0001"}, headers=admin)
    assert ok.json()["order_id"] == "O-0001"
    bad = await client.patch(f"/v1/tickets/{t['id']}", json={"order_id": "O-0006"}, headers=admin)
    assert bad.status_code == 422
    missing = await client.patch(
        f"/v1/tickets/{t['id']}", json={"order_id": "O-9999"}, headers=admin
    )
    assert missing.status_code == 404


async def test_status_machine_allowed_path(client: httpx.AsyncClient, admin: H) -> None:
    t = await create_ticket(client, admin)
    tid = t["id"]
    for target in ("open", "pending", "open", "solved", "closed"):
        r = await client.post(
            f"/v1/tickets/{tid}/status",
            json={"status": target, "note": f"to {target}"},
            headers=admin,
        )
        assert r.status_code == 200, (target, r.text)
        assert r.json()["status"] == target
    final = r.json()
    assert final["solved_at"] and final["closed_at"]


async def test_status_machine_rejects_invalid_transitions(
    client: httpx.AsyncClient, admin: H
) -> None:
    t = await create_ticket(client, admin)
    tid = t["id"]
    r = await client.post(f"/v1/tickets/{tid}/status", json={"status": "solved"}, headers=admin)
    assert r.status_code == 409
    body = r.json()
    assert body["type"].endswith("/invalid-status-transition")
    assert body["allowed"] == ["open"] and body["current"] == "new"
    await client.post(f"/v1/tickets/{tid}/status", json={"status": "open"}, headers=admin)
    await client.post(f"/v1/tickets/{tid}/status", json={"status": "solved"}, headers=admin)
    hint = await client.post(f"/v1/tickets/{tid}/status", json={"status": "open"}, headers=admin)
    assert hint.status_code == 409 and "reopen" in hint.json()["detail"]
    await client.post(f"/v1/tickets/{tid}/status", json={"status": "closed"}, headers=admin)
    again = await client.post(f"/v1/tickets/{tid}/status", json={"status": "open"}, headers=admin)
    assert again.status_code == 409
    nonsense = await client.post(
        f"/v1/tickets/{tid}/status", json={"status": "weird"}, headers=admin
    )
    assert nonsense.status_code == 422


async def test_closed_tickets_are_immutable(client: httpx.AsyncClient, admin: H) -> None:
    t = await create_ticket(client, admin, assignee_id="A-0003")
    tid = t["id"]
    assert (await client.post(f"/v1/tickets/{tid}/close", headers=admin)).status_code == 200
    assert (
        await client.patch(f"/v1/tickets/{tid}", json={"subject": "x"}, headers=admin)
    ).status_code == 409
    assert (
        await client.post(
            f"/v1/tickets/{tid}/assign", json={"assignee_id": "A-0004"}, headers=admin
        )
    ).status_code == 409
    assert (
        await client.post(f"/v1/tickets/{tid}/replies", json={"body": "hi"}, headers=admin)
    ).status_code == 409
    assert (
        await client.post(f"/v1/tickets/{tid}/escalate", json={"reason": "r"}, headers=admin)
    ).status_code == 409
    assert (await client.post(f"/v1/tickets/{tid}/close", headers=admin)).status_code == 409
    assert (
        await client.post(f"/v1/tickets/{tid}/reopen", json={"reason": "r"}, headers=admin)
    ).status_code == 409


async def test_close_requires_open_ticket_and_records_note(
    client: httpx.AsyncClient, admin: H
) -> None:
    new = await create_ticket(client, admin)
    r = await client.post(f"/v1/tickets/{new['id']}/close", json={"note": "x"}, headers=admin)
    assert r.status_code == 409
    open_t = await create_ticket(client, admin, assignee_id="A-0003")
    r = await client.post(
        f"/v1/tickets/{open_t['id']}/close", json={"note": "resolved by phone"}, headers=admin
    )
    body = r.json()
    assert body["status"] == "closed" and body["solved_at"] and body["closed_at"]
    comments = (await client.get(f"/v1/tickets/{open_t['id']}/comments", headers=admin)).json()[
        "items"
    ]
    assert comments[-1]["body"] == "resolved by phone" and comments[-1]["is_internal"] is True
    solved = await create_ticket(client, admin, assignee_id="A-0003")
    await client.post(
        f"/v1/tickets/{solved['id']}/status", json={"status": "solved"}, headers=admin
    )
    assert (await client.post(f"/v1/tickets/{solved['id']}/close", headers=admin)).json()[
        "status"
    ] == "closed"


async def test_reopen_within_and_after_window(
    client: httpx.AsyncClient, admin: H, clock: Any
) -> None:
    t = await create_ticket(client, admin, assignee_id="A-0003")
    tid = t["id"]
    await client.post(f"/v1/tickets/{tid}/status", json={"status": "solved"}, headers=admin)
    clock.set(NOW + timedelta(days=6, hours=23))
    r = await client.post(
        f"/v1/tickets/{tid}/reopen", json={"reason": "still broken"}, headers=admin
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "open" and body["solved_at"] is None and body["reopen_count"] == 1
    await client.post(f"/v1/tickets/{tid}/status", json={"status": "solved"}, headers=admin)
    clock.set(NOW + timedelta(days=15))
    late = await client.post(f"/v1/tickets/{tid}/reopen", json={"reason": "again"}, headers=admin)
    assert late.status_code == 409 and late.json()["type"].endswith("/reopen-window-expired")
    not_solved = await create_ticket(client, admin)
    r = await client.post(
        f"/v1/tickets/{not_solved['id']}/reopen", json={"reason": "x"}, headers=admin
    )
    assert r.status_code == 409


async def test_reopen_window_is_configurable(
    client: httpx.AsyncClient, admin: H, clock: Any
) -> None:
    await client.patch("/v1/admin/settings", json={"reopen_window_days": 1}, headers=admin)
    t = await create_ticket(client, admin, assignee_id="A-0003")
    await client.post(f"/v1/tickets/{t['id']}/status", json={"status": "solved"}, headers=admin)
    clock.set(NOW + timedelta(days=2))
    r = await client.post(f"/v1/tickets/{t['id']}/reopen", json={"reason": "x"}, headers=admin)
    assert r.status_code == 409


async def test_assign_and_unassign(client: httpx.AsyncClient, admin: H) -> None:
    t = await create_ticket(client, admin)
    tid = t["id"]
    r = await client.post(
        f"/v1/tickets/{tid}/assign",
        json={"assignee_id": "A-0003", "team_id": "TM-002"},
        headers=admin,
    )
    assert (
        r.json()["assignee_id"] == "A-0003"
        and r.json()["status"] == "open"
        and r.json()["team_id"] == "TM-002"
    )
    r = await client.post(f"/v1/tickets/{tid}/assign", json={"assignee_id": None}, headers=admin)
    assert r.json()["assignee_id"] is None and r.json()["team_id"] == "TM-002"
    assert (
        await client.post(f"/v1/tickets/{tid}/assign", json={}, headers=admin)
    ).status_code == 422
    assert (
        await client.post(f"/v1/tickets/{tid}/assign", json={"assignee_id": "A-9"}, headers=admin)
    ).status_code == 404
    assert (
        await client.post(f"/v1/tickets/{tid}/assign", json={"team_id": "TM-9"}, headers=admin)
    ).status_code == 404


async def test_escalate_bumps_priority_level_and_notes(client: httpx.AsyncClient, admin: H) -> None:
    t = await create_ticket(client, admin, assignee_id="A-0003", priority="low")
    tid = t["id"]
    seen = []
    for _ in range(3):
        r = await client.post(
            f"/v1/tickets/{tid}/escalate",
            json={"reason": "customer angry", "team_id": "TM-002"},
            headers=admin,
        )
        assert r.status_code == 200
        seen.append((r.json()["priority"], r.json()["escalation_level"]))
    assert seen == [("normal", 1), ("high", 2), ("urgent", 3)]
    assert r.json()["team_id"] == "TM-002"
    assert r.json()["assignee_id"] == "A-0003"  # escalation never reassigns
    over = await client.post(f"/v1/tickets/{tid}/escalate", json={"reason": "again"}, headers=admin)
    assert over.status_code == 409
    notes = (await client.get(f"/v1/tickets/{tid}/comments", headers=admin)).json()["items"]
    assert notes[0]["body"].startswith("Escalated to level 1") and notes[0]["is_internal"]
    bad_team = await client.post(
        f"/v1/tickets/{(await create_ticket(client, admin))['id']}/escalate",
        json={"reason": "r", "team_id": "TM-9"},
        headers=admin,
    )
    assert bad_team.status_code == 404


async def test_ticket_merge_moves_comments_and_is_idempotent(
    client: httpx.AsyncClient, admin: H
) -> None:
    a = await create_ticket(client, admin, subject="A", tags=["x"], assignee_id="A-0003")
    b = await create_ticket(client, admin, subject="B", tags=["y"], assignee_id="A-0003")
    await client.post(f"/v1/tickets/{a['id']}/replies", json={"body": "reply on A"}, headers=admin)
    merged = await client.post(
        f"/v1/tickets/{a['id']}/merge", json={"target_ticket_id": b["id"]}, headers=admin
    )
    assert merged.status_code == 200
    target = merged.json()
    assert target["id"] == b["id"] and target["tags"] == ["x", "y"] and target["first_response_at"]
    source = (await client.get(f"/v1/tickets/{a['id']}", headers=admin)).json()
    assert source["status"] == "closed" and source["merged_into_id"] == b["id"]
    bodies = [
        c["body"]
        for c in (await client.get(f"/v1/tickets/{b['id']}/comments", headers=admin)).json()[
            "items"
        ]
    ]
    assert "reply on A" in bodies
    again = await client.post(
        f"/v1/tickets/{a['id']}/merge", json={"target_ticket_id": b["id"]}, headers=admin
    )
    assert again.status_code == 200
    third = await create_ticket(client, admin, subject="C")
    conflict = await client.post(
        f"/v1/tickets/{a['id']}/merge", json={"target_ticket_id": third["id"]}, headers=admin
    )
    assert conflict.status_code == 409
    into_merged = await client.post(
        f"/v1/tickets/{third['id']}/merge", json={"target_ticket_id": a["id"]}, headers=admin
    )
    assert into_merged.status_code == 409
    self_merge = await client.post(
        f"/v1/tickets/{third['id']}/merge", json={"target_ticket_id": third["id"]}, headers=admin
    )
    assert self_merge.status_code == 422
    other_customer = await create_ticket(client, admin, customer_id="C-0002")
    cross = await client.post(
        f"/v1/tickets/{third['id']}/merge",
        json={"target_ticket_id": other_customer["id"]},
        headers=admin,
    )
    assert cross.status_code == 422


async def test_comments_public_vs_internal_and_first_response(
    client: httpx.AsyncClient, admin: H, agent: H, clock: Any
) -> None:
    t = await create_ticket(client, admin)
    tid = t["id"]
    note = await client.post(f"/v1/tickets/{tid}/notes", json={"body": "private"}, headers=agent)
    assert note.status_code == 201 and note.json()["is_internal"] is True
    assert note.json()["author_type"] == "agent" and note.json()["author_id"] == "A-0003"
    assert (await client.get(f"/v1/tickets/{tid}", headers=admin)).json()[
        "first_response_at"
    ] is None
    clock.set(NOW + timedelta(minutes=10))
    reply = await client.post(
        f"/v1/tickets/{tid}/replies", json={"body": "hello customer"}, headers=agent
    )
    assert reply.status_code == 201 and reply.json()["is_internal"] is False
    first = (await client.get(f"/v1/tickets/{tid}", headers=admin)).json()["first_response_at"]
    assert first.startswith("2026-06-01T12:10")
    clock.set(NOW + timedelta(minutes=50))
    await client.post(f"/v1/tickets/{tid}/replies", json={"body": "second"}, headers=agent)
    assert (await client.get(f"/v1/tickets/{tid}", headers=admin)).json()[
        "first_response_at"
    ] == first
    everything = (await client.get(f"/v1/tickets/{tid}/comments", headers=admin)).json()["items"]
    assert [c["body"] for c in everything] == ["private", "hello customer", "second"]
    public = (
        await client.get(
            f"/v1/tickets/{tid}/comments", params={"include_internal": False}, headers=admin
        )
    ).json()["items"]
    assert [c["body"] for c in public] == ["hello customer", "second"]
    one = await client.get(f"/v1/comments/{note.json()['id']}", headers=admin)
    assert one.json()["body"] == "private"
    assert (await client.get("/v1/comments/CM-99999", headers=admin)).status_code == 404
    assert (
        await client.post(f"/v1/tickets/{tid}/replies", json={"body": ""}, headers=admin)
    ).status_code == 422


async def test_ticket_filters(client: httpx.AsyncClient, admin: H, clock: Any) -> None:
    a = await create_ticket(
        client,
        admin,
        subject="Refund please",
        priority="urgent",
        tags=["refund"],
        assignee_id="A-0003",
        team_id="TM-001",
    )
    clock.set(NOW + timedelta(hours=2))
    b = await create_ticket(
        client, admin, subject="Login broken", priority="low", customer_id="C-0002"
    )
    clock.set(NOW + timedelta(hours=3))

    async def ids(**params: Any) -> list[str]:
        r = await client.get("/v1/tickets", params=params, headers=admin)
        assert r.status_code == 200, r.text
        return [t["id"] for t in r.json()["items"]]

    assert await ids() == [b["id"], a["id"]]  # newest first
    assert await ids(status="open") == [a["id"]]
    assert await ids(status="new") == [b["id"]]
    assert await ids(priority="urgent") == [a["id"]]
    assert await ids(assignee_id="A-0003") == [a["id"]]
    assert await ids(unassigned=True) == [b["id"]]
    assert await ids(team_id="TM-001") == [a["id"]]
    assert await ids(customer_id="C-0002") == [b["id"]]
    assert await ids(tag="refund") == [a["id"]]
    assert await ids(q="login") == [b["id"]]
    assert await ids(q=a["id"]) == [a["id"]]
    assert await ids(created_after=(NOW + timedelta(hours=1)).isoformat()) == [b["id"]]
    assert await ids(created_before=(NOW + timedelta(hours=1)).isoformat()) == [a["id"]]
    # urgent first-response target is 30 minutes and nobody replied: breached. low is not yet.
    assert await ids(sla_breached=True) == [a["id"]]
    assert await ids(sla_breached=False) == [b["id"]]
    assert await ids(q="100%") == []  # LIKE wildcards are escaped
    assert (
        await client.get("/v1/tickets", params={"status": "bogus"}, headers=admin)
    ).status_code == 422


async def test_ticket_history_and_csat(client: httpx.AsyncClient, admin: H) -> None:
    t = await create_ticket(client, admin, assignee_id="A-0003")
    tid = t["id"]
    early = await client.post(f"/v1/tickets/{tid}/csat", json={"score": 5}, headers=admin)
    assert early.status_code == 409
    await client.post(f"/v1/tickets/{tid}/close", headers=admin)
    ok = await client.post(
        f"/v1/tickets/{tid}/csat", json={"score": 4, "comment": "fast"}, headers=admin
    )
    assert ok.status_code == 200 and ok.json()["score"] == 4
    assert (
        await client.post(f"/v1/tickets/{tid}/csat", json={"score": 5}, headers=admin)
    ).status_code == 409
    assert (
        await client.post(f"/v1/tickets/{tid}/csat", json={"score": 6}, headers=admin)
    ).status_code == 422
    got = (await client.get(f"/v1/tickets/{tid}/csat", headers=admin)).json()
    assert got["comment"] == "fast" and got["submitted_at"]
    hist = (await client.get(f"/v1/tickets/{tid}/history", headers=admin)).json()["items"]
    assert [h["action"] for h in hist] == ["tickets.submit_csat", "tickets.close", "tickets.create"]
    other = await create_ticket(client, admin)
    unrated = (await client.get(f"/v1/tickets/{other['id']}/csat", headers=admin)).json()
    assert unrated["score"] is None
    assert (await client.get("/v1/tickets/T-9999/history", headers=admin)).status_code == 404


async def test_tags_lifecycle(client: httpx.AsyncClient, admin: H) -> None:
    t = await create_ticket(client, admin)
    tid = t["id"]
    created = await client.post("/v1/tags", json={"name": "Escalated"}, headers=admin)
    assert created.status_code == 201 and created.json()["name"] == "escalated"
    assert (
        await client.post("/v1/tags", json={"name": "escalated"}, headers=admin)
    ).status_code == 409
    added = await client.post(f"/v1/tickets/{tid}/tags", json={"tag": "escalated"}, headers=admin)
    assert added.json()["tags"] == ["escalated"]
    again = await client.post(f"/v1/tickets/{tid}/tags", json={"tag": "escalated"}, headers=admin)
    assert again.json()["tags"] == ["escalated"]
    await client.post(f"/v1/tickets/{tid}/tags", json={"tag": "brand-new"}, headers=admin)
    assert (await client.get(f"/v1/tickets/{tid}/tags", headers=admin)).json() == [
        "brand-new",
        "escalated",
    ]
    listing = (await client.get("/v1/tags", headers=admin)).json()
    assert {x["name"]: x["ticket_count"] for x in listing} == {"brand-new": 1, "escalated": 1}
    removed = await client.delete(f"/v1/tickets/{tid}/tags/escalated", headers=admin)
    assert removed.json()["tags"] == ["brand-new"]
    assert (
        await client.delete(f"/v1/tickets/{tid}/tags/escalated", headers=admin)
    ).status_code == 404
    assert (await client.delete("/v1/tags/brand-new", headers=admin)).status_code == 204
    assert (await client.get(f"/v1/tickets/{tid}", headers=admin)).json()["tags"] == []
    assert (await client.delete("/v1/tags/brand-new", headers=admin)).status_code == 404
    assert (await client.get("/v1/tickets/T-9999/tags", headers=admin)).status_code == 404


async def test_macros_crud_and_apply(client: httpx.AsyncClient, admin: H, agent: H) -> None:
    body = {
        "name": "Thanks",
        "body": "Hi {{customer_name}}, about {{ticket_id}} - {{agent_name}}",
        "actions": {"status": "open", "priority": "high", "add_tags": ["macro"], "public": True},
    }
    created = await client.post("/v1/macros", json=body, headers=admin)
    assert created.status_code == 201
    mid = created.json()["id"]
    assert (await client.post("/v1/macros", json=body, headers=admin)).status_code == 409
    assert (await client.get(f"/v1/macros/{mid}", headers=admin)).json()["name"] == "Thanks"
    assert (await client.get("/v1/macros", params={"q": "thanks"}, headers=admin)).json()["items"][
        0
    ]["id"] == mid
    upd = await client.patch(f"/v1/macros/{mid}", json={"description": "d"}, headers=admin)
    assert upd.json()["description"] == "d"
    other = await client.post("/v1/macros", json={"name": "Other", "body": "b"}, headers=admin)
    clash = await client.patch(
        f"/v1/macros/{other.json()['id']}", json={"name": "Thanks"}, headers=admin
    )
    assert clash.status_code == 409

    t = await create_ticket(client, admin)
    applied = await client.post(
        f"/v1/macros/{mid}/apply", json={"ticket_id": t["id"]}, headers=agent
    )
    assert applied.status_code == 200, applied.text
    out = applied.json()
    assert out["comment"]["body"] == f"Hi Cora Customer, about {t['id']} - Rae Agent"
    assert out["comment"]["is_internal"] is False
    assert out["ticket"]["status"] == "open" and out["ticket"]["priority"] == "high"
    assert out["ticket"]["tags"] == ["macro"] and out["ticket"]["first_response_at"]

    internal = await client.post(
        "/v1/macros",
        json={"name": "Note", "body": "x", "actions": {"public": False}},
        headers=admin,
    )
    res = await client.post(
        f"/v1/macros/{internal.json()['id']}/apply", json={"ticket_id": t["id"]}, headers=admin
    )
    assert res.json()["comment"]["is_internal"] is True
    bad_status = await client.post(
        "/v1/macros",
        json={"name": "Bad", "body": "x", "actions": {"status": "closed"}},
        headers=admin,
    )
    res = await client.post(
        f"/v1/macros/{bad_status.json()['id']}/apply", json={"ticket_id": t["id"]}, headers=admin
    )
    assert res.status_code == 409  # open -> closed is not a legal status step
    after = (await client.get(f"/v1/tickets/{t['id']}/comments", headers=admin)).json()["items"]
    assert len(after) == 2  # the failed macro left no partial comment behind
    assert (
        await client.post(f"/v1/macros/{mid}/apply", json={"ticket_id": "T-9999"}, headers=admin)
    ).status_code == 404
    assert (await client.delete(f"/v1/macros/{mid}", headers=admin)).status_code == 204
    assert (await client.get(f"/v1/macros/{mid}", headers=admin)).status_code == 404
