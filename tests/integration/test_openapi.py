"""The OpenAPI document is the contract the MCP layer is generated from."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
METHODS = {"get", "post", "put", "patch", "delete"}


async def _spec(client: httpx.AsyncClient) -> dict[str, object]:
    spec: dict[str, object] = (await client.get("/openapi.json")).json()
    return spec


async def test_every_operation_is_documented_for_tool_generation(client: httpx.AsyncClient) -> None:
    spec = await _spec(client)
    seen: set[str] = set()
    count = 0
    for path, item in spec["paths"].items():  # type: ignore[attr-defined]
        for method, op in item.items():
            assert method in METHODS
            count += 1
            op_id = op["operationId"]
            assert op_id not in seen, f"duplicate operationId {op_id}"
            seen.add(op_id)
            assert "_" in op_id and op_id == op_id.lower(), op_id
            assert 10 <= len(op["summary"]) <= 80, (op_id, op["summary"])
            assert len(op["description"]) >= 60, (op_id, "description too short for an AI reader")
            assert op["tags"], op_id
            assert "security" in op, f"{method.upper()} {path} is not protected"
            assert {"401", "403", "422"} <= set(op["responses"]), op_id
    assert count >= 95


async def test_error_responses_are_problem_json(client: httpx.AsyncClient) -> None:
    spec = await _spec(client)
    op = spec["paths"]["/v1/tickets/{ticket_id}"]["get"]  # type: ignore[index]
    assert "application/problem+json" in op["responses"]["404"]["content"]
    assert "application/json" not in op["responses"]["404"]["content"]
    assert "ProblemDetail" in spec["components"]["schemas"]  # type: ignore[index]
    assert "HTTPValidationError" not in json.dumps(spec)


async def test_committed_openapi_json_is_current() -> None:
    import importlib.util

    script = ROOT / "scripts" / "export_openapi.py"
    module_spec = importlib.util.spec_from_file_location("export_openapi", script)
    assert module_spec and module_spec.loader
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    committed = (ROOT / "openapi.json").read_text()
    assert committed == module.render(), "openapi.json is stale: run `make openapi`"
