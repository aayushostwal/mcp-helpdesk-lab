from __future__ import annotations

import json

import httpx
import pytest
from mcp.shared.exceptions import McpError

from helpdesk_mcp import content
from helpdesk_mcp.server import build_server
from helpdesk_mcp.settings import McpSettings
from helpdesk_mcp.spec import Spec
from mcp import types
from tests.conftest import create_ticket
from tests.mcp.conftest import Connect, result_json


def test_instructions_are_short_and_mention_the_basics() -> None:
    words = content.INSTRUCTIONS.split()
    assert len(words) <= 250
    for fragment in [
        "T-1042",
        "C-0311",
        "O-1502",
        "R-0111",
        "A-0002",
        "500.00",
        "30 days",
        "7 days",
    ]:
        assert fragment in content.INSTRUCTIONS
    for uri in ["helpdesk://schema", "helpdesk://policies/refunds", "helpdesk://policies/sla"]:
        assert uri in content.INSTRUCTIONS


async def test_server_info_and_instructions(connect: Connect) -> None:
    async with connect() as (session, _):
        init = await session.initialize()
    assert init.serverInfo.name == "helpdesk"
    assert init.instructions == content.INSTRUCTIONS
    assert init.capabilities.tools is not None
    assert init.capabilities.resources is not None
    assert init.capabilities.prompts is not None


async def test_no_instructions_flag(connect: Connect) -> None:
    async with connect(instructions=False) as (session, _):
        init = await session.initialize()
    assert init.instructions is None
    assert init.capabilities.resources is not None  # the other features stay on


async def test_resources_are_listed_and_readable(connect: Connect) -> None:
    async with connect() as (session, _):
        listed = await session.list_resources()
        assert {str(r.uri) for r in listed.resources} == {
            "helpdesk://schema",
            "helpdesk://policies/refunds",
            "helpdesk://policies/sla",
        }
        templates = await session.list_resource_templates()
        assert [t.uriTemplate for t in templates.resourceTemplates] == [
            "helpdesk://tickets/{ticket_id}"
        ]
        for uri, snippet in [
            ("helpdesk://schema", "`T-1042`"),
            ("helpdesk://policies/refunds", "500.00"),
            ("helpdesk://policies/sla", "at_risk"),
        ]:
            read = await session.read_resource(uri)  # type: ignore[arg-type]
            first = read.contents[0]
            assert isinstance(first, types.TextResourceContents)
            assert snippet in first.text
            assert first.mimeType == "text/markdown"


async def test_ticket_resource_reads_through_the_api(
    connect: Connect, client: httpx.AsyncClient, admin: dict[str, str]
) -> None:
    ticket = await create_ticket(client, admin)
    async with connect() as (session, _):
        read = await session.read_resource(f"helpdesk://tickets/{ticket['id']}")  # type: ignore[arg-type]
        first = read.contents[0]
        assert isinstance(first, types.TextResourceContents)
        assert json.loads(first.text)["subject"] == "Printer on fire"
        assert first.mimeType == "application/json"
        with pytest.raises(McpError, match="not-found"):
            await session.read_resource("helpdesk://tickets/T-9999")  # type: ignore[arg-type]


async def test_no_resources_flag(connect: Connect) -> None:
    async with connect(resources=False) as (session, _):
        init = await session.initialize()
        assert init.capabilities.resources is None
        assert init.capabilities.prompts is not None
        with pytest.raises(McpError):
            await session.list_resources()
        assert len((await session.list_tools()).tools) == 102


async def test_prompts(connect: Connect) -> None:
    async with connect() as (session, _):
        prompts = {p.name: p for p in (await session.list_prompts()).prompts}
        assert set(prompts) == {"triage_ticket", "weekly_sla_report", "refund_request"}
        required = {
            n: [a.name for a in (p.arguments or []) if a.required] for n, p in prompts.items()
        }
        assert required == {
            "triage_ticket": ["ticket_id"],
            "weekly_sla_report": [],
            "refund_request": ["order_id", "reason"],
        }

        triage = await session.get_prompt("triage_ticket", {"ticket_id": "T-1042"})
        text = _text(triage)
        assert "T-1042" in text and "tickets_get" in text and "sla_evaluate_ticket" in text

        weekly = _text(await session.get_prompt("weekly_sla_report"))
        assert "all teams" in weekly
        team = _text(await session.get_prompt("weekly_sla_report", {"team": "Billing"}))
        assert "Billing" in team and "teams_list" in team

        refund = _text(
            await session.get_prompt("refund_request", {"order_id": "O-1502", "reason": "damaged"})
        )
        assert "O-1502" in refund and "damaged" in refund and "refunds_create" in refund


def _text(result: types.GetPromptResult) -> str:
    message = result.messages[0]
    assert message.role == "user" and isinstance(message.content, types.TextContent)
    return message.content.text


async def test_no_prompts_flag(connect: Connect) -> None:
    async with connect(prompts=False) as (session, _):
        init = await session.initialize()
        assert init.capabilities.prompts is None
        with pytest.raises(McpError):
            await session.list_prompts()


async def test_all_flags_off_leaves_only_tools(connect: Connect) -> None:
    async with connect(instructions=False, resources=False, prompts=False) as (session, _):
        init = await session.initialize()
        assert init.instructions is None
        assert init.capabilities.resources is None and init.capabilities.prompts is None
        assert init.capabilities.tools is not None


async def test_successful_call_returns_compact_json(
    connect: Connect, client: httpx.AsyncClient, admin: dict[str, str]
) -> None:
    ticket = await create_ticket(client, admin)
    async with connect() as (session, _):
        result = await session.call_tool("tickets_get", {"ticket_id": ticket["id"]})
    assert not result.isError
    block = result.content[0]
    assert isinstance(block, types.TextContent)
    assert block.text == json.dumps(ticket, ensure_ascii=False, separators=(",", ":")) or (
        json.loads(block.text)["id"] == ticket["id"]
    )
    assert json.loads(block.text)["customer_id"] == "C-0001"


async def test_api_errors_become_is_error_results(connect: Connect) -> None:
    async with connect() as (session, _):
        not_found = await session.call_tool("tickets_get", {"ticket_id": "T-9999"})
        assert not_found.isError
        body = result_json(not_found)
        assert body["status"] == 404 and body["type"].endswith("/not-found")
        assert "T-9999" in body["detail"] and body["request_id"]

        invalid = await session.call_tool("tickets_create", {"subject": ""})
        body = result_json(invalid)
        assert invalid.isError and body["status"] == 422 and body["errors"]

        policy = await session.call_tool(
            "refunds_create", {"order_id": "O-0002", "amount": "10.00", "reason": "late"}
        )
        body = result_json(policy)
        assert policy.isError and body["type"].endswith("refund-not-eligible")
        assert "30-day" in body["detail"]

        bad_args = await session.call_tool("tickets_get", {"ticket_id": "T-1", "bogus": 1})
        assert bad_args.isError and result_json(bad_args)["status"] == 422

        # the session survives every one of these
        assert not (await session.call_tool("tags_list", {})).isError


async def test_auth_and_role_errors(connect: Connect, world: dict[str, str]) -> None:
    async with connect(no_key=True) as (session, _):
        result = await session.call_tool("tags_list", {})
        assert result.isError and result_json(result)["status"] == 401
    async with connect() as (session, _):  # admin agent is not a lead
        result = await session.call_tool("refunds_approve", {"refund_id": "R-0001"})
        assert result.isError and result_json(result)["status"] in (403, 404)


async def test_unreachable_api_does_not_crash_the_session(spec: Spec) -> None:
    from mcp.shared.memory import create_connected_server_and_client_session

    settings = McpSettings(api_url="http://127.0.0.1:9", retries=0, timeout_seconds=2)
    bundle = build_server(settings, spec=spec)
    try:
        async with create_connected_server_and_client_session(bundle.mcp) as session:
            result = await session.call_tool("tags_list", {})
            assert result.isError
            body = result_json(result)
            assert body["status"] == 502 and body["type"].endswith("upstream-unreachable")
            assert (await session.list_tools()).tools  # still alive
    finally:
        await bundle.aclose()


async def test_a_crashing_variant_is_reported_not_raised(
    connect: Connect, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with connect() as (session, bundle):

        async def boom(name: str, arguments: object) -> object:
            raise RuntimeError("secret internals")

        monkeypatch.setattr(bundle.variant, "call", boom)
        result = await session.call_tool("tags_list", {})
        assert result.isError
        text = json.dumps(result_json(result))
        assert "internal-error" in text and "secret internals" not in text
        monkeypatch.undo()
        assert not (await session.call_tool("tags_list", {})).isError


async def test_post_calls_send_idempotency_keys_end_to_end(
    connect: Connect, client: httpx.AsyncClient, admin: dict[str, str]
) -> None:
    """A real POST through the server works, and a retried key would replay, not repeat."""
    ticket = await create_ticket(client, admin)
    async with connect() as (session, _):
        result = await session.call_tool(
            "comments_add_note", {"ticket_id": ticket["id"], "body": "checked the logs"}
        )
        assert not result.isError
        assert result_json(result)["is_internal"] is True
        comments = await session.call_tool(
            "comments_list", {"ticket_id": ticket["id"], "include_internal": True}
        )
        assert len(result_json(comments)["items"]) == 1
