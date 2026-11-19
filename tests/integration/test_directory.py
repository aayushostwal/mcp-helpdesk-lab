"""Agents and teams."""

from __future__ import annotations

import httpx

from tests.conftest import create_ticket

H = dict[str, str]


async def test_agent_crud(client: httpx.AsyncClient, admin: H) -> None:
    created = await client.post(
        "/v1/agents",
        json={"name": "New Agent", "email": "NEW@x.example", "role": "lead", "team_id": "TM-002"},
        headers=admin,
    )
    assert created.status_code == 201
    aid = created.json()["id"]
    assert (
        aid == "A-0005"
        and created.json()["email"] == "new@x.example"
        and created.json()["active"] is True
    )
    assert (
        await client.post(
            "/v1/agents", json={"name": "Dup", "email": "new@x.example"}, headers=admin
        )
    ).status_code == 409
    assert (
        await client.post(
            "/v1/agents",
            json={"name": "Bad", "email": "b@x.example", "team_id": "TM-9"},
            headers=admin,
        )
    ).status_code == 404
    assert (
        await client.post(
            "/v1/agents",
            json={"name": "Bad", "email": "b@x.example", "role": "king"},
            headers=admin,
        )
    ).status_code == 422
    upd = await client.patch(
        f"/v1/agents/{aid}", json={"name": "Renamed", "role": "agent"}, headers=admin
    )
    assert upd.json()["name"] == "Renamed" and upd.json()["role"] == "agent"
    assert (
        await client.patch(f"/v1/agents/{aid}", json={"team_id": "TM-9"}, headers=admin)
    ).status_code == 404
    assert (await client.get(f"/v1/agents/{aid}", headers=admin)).json()["id"] == aid
    assert (await client.get("/v1/agents/A-9999", headers=admin)).status_code == 404


async def test_agent_list_filters(client: httpx.AsyncClient, admin: H) -> None:
    async def ids(**params: object) -> list[str]:
        r = await client.get("/v1/agents", params=params, headers=admin)  # type: ignore[arg-type]
        return [a["id"] for a in r.json()["items"]]

    assert await ids() == ["A-0001", "A-0002", "A-0003", "A-0004"]
    assert await ids(team_id="TM-002") == ["A-0004"]
    assert await ids(role="lead") == ["A-0002"]
    assert await ids(q="rae") == ["A-0003"]
    await client.post("/v1/agents/A-0004/deactivate", headers=admin)
    assert await ids(active=False) == ["A-0004"]
    assert await ids(active=True) == ["A-0001", "A-0002", "A-0003"]
    page = (await client.get("/v1/agents", params={"limit": 2}, headers=admin)).json()
    nxt = (
        await client.get(
            "/v1/agents", params={"limit": 2, "cursor": page["next_cursor"]}, headers=admin
        )
    ).json()
    assert [a["id"] for a in page["items"] + nxt["items"]] == [
        "A-0001",
        "A-0002",
        "A-0003",
        "A-0004",
    ]


async def test_deactivate_unassigns_open_tickets_and_clears_oncall(
    client: httpx.AsyncClient, admin: H
) -> None:
    open_t = await create_ticket(client, admin, assignee_id="A-0003")
    done = await create_ticket(client, admin, assignee_id="A-0003")
    await client.post(f"/v1/tickets/{done['id']}/close", headers=admin)
    await client.put("/v1/teams/TM-001/oncall", json={"agent_id": "A-0003"}, headers=admin)
    r = await client.post("/v1/agents/A-0003/deactivate", headers=admin)
    assert r.json()["active"] is False
    assert (await client.get(f"/v1/tickets/{open_t['id']}", headers=admin)).json()[
        "assignee_id"
    ] is None
    assert (await client.get(f"/v1/tickets/{done['id']}", headers=admin)).json()[
        "assignee_id"
    ] == "A-0003"
    assert (await client.get("/v1/teams/TM-001/oncall", headers=admin)).json() is None
    assert (await client.post("/v1/agents/A-0003/deactivate", headers=admin)).status_code == 200
    assert (await client.get("/v1/agents/A-0003/tickets", headers=admin)).json()["items"][0][
        "id"
    ] == done["id"]
    back = await client.post("/v1/agents/A-0003/reactivate", headers=admin)
    assert back.json()["active"] is True
    assert (await client.post("/v1/agents/A-0003/reactivate", headers=admin)).status_code == 200
    assert (
        await client.get("/v1/agents/A-0003/tickets", params={"status": "closed"}, headers=admin)
    ).status_code == 200
    assert (await client.get("/v1/agents/A-9/tickets", headers=admin)).status_code == 404


async def test_team_crud_and_membership(client: httpx.AsyncClient, admin: H) -> None:
    teams = (await client.get("/v1/teams", headers=admin)).json()
    assert [t["name"] for t in teams] == ["Billing", "Support"]
    created = await client.post(
        "/v1/teams", json={"name": "Escalations", "description": "seniors"}, headers=admin
    )
    assert created.status_code == 201 and created.json()["id"] == "TM-003"
    assert (
        await client.post("/v1/teams", json={"name": "Escalations"}, headers=admin)
    ).status_code == 409
    assert (await client.get("/v1/teams/TM-003", headers=admin)).json()["description"] == "seniors"
    assert (await client.get("/v1/teams/TM-9", headers=admin)).status_code == 404
    members = (await client.get("/v1/teams/TM-001/members", headers=admin)).json()
    assert [m["id"] for m in members] == ["A-0001", "A-0002", "A-0003"]
    moved = await client.post(
        "/v1/teams/TM-003/members", json={"agent_id": "A-0003"}, headers=admin
    )
    assert moved.json()["team_id"] == "TM-003"
    assert (
        await client.post("/v1/teams/TM-003/members", json={"agent_id": "A-0003"}, headers=admin)
    ).status_code == 200
    assert (
        await client.post("/v1/teams/TM-003/members", json={"agent_id": "A-9"}, headers=admin)
    ).status_code == 404
    removed = await client.delete("/v1/teams/TM-003/members/A-0003", headers=admin)
    assert removed.json()["team_id"] is None
    assert (
        await client.delete("/v1/teams/TM-003/members/A-0003", headers=admin)
    ).status_code == 409
    assert (await client.delete("/v1/teams/TM-003/members/A-9", headers=admin)).status_code == 404
    assert (await client.get("/v1/teams/TM-9/members", headers=admin)).status_code == 404


async def test_oncall_rules(client: httpx.AsyncClient, admin: H) -> None:
    assert (await client.get("/v1/teams/TM-001/oncall", headers=admin)).json() is None
    ok = await client.put("/v1/teams/TM-001/oncall", json={"agent_id": "A-0002"}, headers=admin)
    assert ok.json()["oncall_agent_id"] == "A-0002"
    assert (await client.get("/v1/teams/TM-001/oncall", headers=admin)).json()["id"] == "A-0002"
    not_member = await client.put(
        "/v1/teams/TM-001/oncall", json={"agent_id": "A-0004"}, headers=admin
    )
    assert not_member.status_code == 422
    assert (
        await client.put("/v1/teams/TM-001/oncall", json={"agent_id": "A-9"}, headers=admin)
    ).status_code == 404
    # moving the on-call agent to another team ends their duty
    await client.post("/v1/teams/TM-002/members", json={"agent_id": "A-0002"}, headers=admin)
    assert (await client.get("/v1/teams/TM-001", headers=admin)).json()["oncall_agent_id"] is None
    await client.put("/v1/teams/TM-002/oncall", json={"agent_id": "A-0002"}, headers=admin)
    await client.patch("/v1/agents/A-0002", json={"team_id": "TM-001"}, headers=admin)
    assert (await client.get("/v1/teams/TM-002", headers=admin)).json()["oncall_agent_id"] is None
    await client.put("/v1/teams/TM-001/oncall", json={"agent_id": "A-0002"}, headers=admin)
    await client.delete("/v1/teams/TM-001/members/A-0002", headers=admin)
    assert (await client.get("/v1/teams/TM-001", headers=admin)).json()["oncall_agent_id"] is None
    await client.post("/v1/teams/TM-001/members", json={"agent_id": "A-0003"}, headers=admin)
    await client.post("/v1/agents/A-0003/deactivate", headers=admin)
    inactive = await client.put(
        "/v1/teams/TM-001/oncall", json={"agent_id": "A-0003"}, headers=admin
    )
    assert inactive.status_code == 422
    cleared = await client.put("/v1/teams/TM-001/oncall", json={"agent_id": None}, headers=admin)
    assert cleared.json()["oncall_agent_id"] is None
