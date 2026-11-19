from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import jsonschema
import pytest

from helpdesk_mcp.settings import McpSettings
from helpdesk_mcp.spec import Spec, SpecError, load_spec, parse_spec


def test_all_operations_are_loaded(spec: Spec) -> None:
    assert len(spec.operations) == 102
    assert spec.get("tickets_close") is not None
    assert spec.get("nope") is None


def test_every_input_schema_is_valid_json_schema(spec: Spec) -> None:
    for op in spec.operations.values():
        jsonschema.Draft202012Validator.check_schema(op.input_schema)
        assert op.input_schema["type"] == "object"


def test_schemas_are_self_contained(spec: Spec) -> None:
    """No pointer into ``#/components``; every ``$defs`` reference resolves."""
    for op in spec.operations.values():
        text = json.dumps(op.input_schema)
        assert "#/components" not in text, op.operation_id
        defs = op.input_schema.get("$defs", {})
        for ref in _refs(op.input_schema):
            assert ref.removeprefix("#/$defs/") in defs, (op.operation_id, ref)


def _refs(node: Any) -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref":
                found.append(value)
            else:
                found.extend(_refs(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_refs(item))
    return found


def test_path_query_and_body_are_merged_flat(spec: Spec) -> None:
    op = spec.operations["refunds_create"]
    assert set(op.input_schema["properties"]) == {"order_id", "amount", "reason", "ticket_id"}
    assert op.input_schema["required"] == ["order_id", "amount", "reason"]
    assert op.method == "POST" and op.path == "/v1/refunds" and op.tag == "refunds"
    assert op.scopes == ("refunds:write",)

    op = spec.operations["tickets_escalate"]
    assert op.input_schema["required"][0] == "ticket_id"  # path parameter first, then body
    assert "reason" in op.input_schema["required"]

    op = spec.operations["tickets_list"]
    assert "ticket_id" not in op.input_schema["properties"]
    assert "sla_breached" in op.input_schema["properties"]
    assert "required" not in op.input_schema  # nothing required for a list


def test_optional_body_adds_no_required_fields(spec: Spec) -> None:
    op = spec.operations["tickets_close"]
    assert op.input_schema["required"] == ["ticket_id"]
    assert "note" in op.input_schema["properties"]
    assert not op.body_required


def test_tool_description_is_summary_then_description(spec: Spec) -> None:
    op = spec.operations["refunds_create"]
    assert op.tool_description == f"{op.summary}\n\n{op.description}"
    assert op.summary and op.description


def test_descriptions_are_never_trimmed(
    spec: Spec,
) -> None:
    raw = json.loads(Path("openapi.json").read_text())
    for path, item in raw["paths"].items():
        for method, body in item.items():
            op = spec.operations[body["operationId"]]
            assert op.method == method.upper() and op.path == path
            assert op.description == body["description"].strip()
            assert op.summary == body["summary"].strip()


def _doc(**operation: Any) -> dict[str, Any]:
    return {"paths": {"/x": {"post": {"operationId": "x_do", **operation}}}}


def test_spec_errors() -> None:
    with pytest.raises(SpecError, match="no operationId"):
        parse_spec({"paths": {"/x": {"get": {}}}})
    with pytest.raises(SpecError, match="duplicate"):
        parse_spec(
            {"paths": {"/x": {"get": {"operationId": "a"}}, "/y": {"get": {"operationId": "a"}}}}
        )
    with pytest.raises(SpecError, match="parameter location"):
        parse_spec(_doc(parameters=[{"name": "h", "in": "header"}]))
    with pytest.raises(SpecError, match="only JSON"):
        parse_spec(_doc(requestBody={"content": {"text/plain": {}}}))
    with pytest.raises(SpecError, match="not an object"):
        parse_spec(
            _doc(requestBody={"content": {"application/json": {"schema": {"type": "array"}}}})
        )
    with pytest.raises(SpecError, match="unsupported request body"):
        parse_spec(
            _doc(
                requestBody={
                    "content": {
                        "application/json": {
                            "schema": {"anyOf": [{"type": "object"}, {"type": "array"}]}
                        }
                    }
                }
            )
        )
    clash = _doc(
        parameters=[{"name": "a", "in": "query", "schema": {"type": "string"}}],
        requestBody={
            "content": {
                "application/json": {
                    "schema": {"type": "object", "properties": {"a": {"type": "string"}}}
                }
            }
        },
    )
    with pytest.raises(SpecError, match="clash"):
        parse_spec(clash)


def test_path_level_parameters_and_param_description() -> None:
    doc = {
        "info": {"title": "T", "version": "9"},
        "paths": {
            "/x/{id}": {
                "parameters": [{"name": "id", "in": "path", "schema": {"type": "string"}}],
                "get": {
                    "operationId": "x_get",
                    "parameters": [
                        {"name": "q", "in": "query", "description": "Search", "schema": {}}
                    ],
                },
            }
        },
    }
    spec = parse_spec(doc)
    op = spec.operations["x_get"]
    assert spec.title == "T" and spec.version == "9"
    assert op.input_schema["required"] == ["id"]
    assert op.input_schema["properties"]["q"]["description"] == "Search"
    assert op.tag == "untagged"
    assert op.tool_description == ""


def test_load_spec_prefers_explicit_path(tmp_path: Path) -> None:
    path = tmp_path / "o.json"
    path.write_text(json.dumps({"paths": {"/a": {"get": {"operationId": "a_get"}}}}))
    spec = load_spec(McpSettings(openapi_path=path))
    assert list(spec.operations) == ["a_get"]


def test_load_spec_falls_back_to_the_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("helpdesk_mcp.spec.repo_openapi_path", lambda: None)
    doc = {"paths": {"/a": {"get": {"operationId": "from_api"}}}}

    def fake_get(url: str, timeout: float) -> httpx.Response:
        assert url == "http://api.test/openapi.json"
        return httpx.Response(200, json=doc, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)
    assert "from_api" in load_spec(McpSettings(api_url="http://api.test/")).operations


def test_load_spec_reports_a_missing_document(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("helpdesk_mcp.spec.repo_openapi_path", lambda: None)

    def boom(url: str, timeout: float) -> httpx.Response:
        raise httpx.ConnectError("no")

    monkeypatch.setattr(httpx, "get", boom)
    with pytest.raises(SpecError, match="HELPDESK_MCP_OPENAPI_PATH"):
        load_spec(McpSettings(api_url="http://nowhere.invalid"))


def test_shared_nested_definitions_are_included_once() -> None:
    ref = "#/components/schemas/"
    schemas = {
        "A": {"type": "object", "properties": {"b": {"$ref": ref + "B"}}},
        "B": {"type": "object", "properties": {"c": {"$ref": ref + "C"}, "d": {"$ref": ref + "D"}}},
        "C": {"type": "string"},
        "D": {"type": "object", "properties": {"c": {"$ref": ref + "C"}}},
    }
    doc = {
        "components": {"schemas": schemas},
        "paths": {
            "/x": {
                "post": {
                    "operationId": "x_do",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/A"}}
                        },
                    },
                }
            }
        },
    }
    op = parse_spec(doc).operations["x_do"]
    assert set(op.input_schema["$defs"]) == {"B", "C", "D"}
    assert op.input_schema["properties"]["b"] == {"$ref": "#/$defs/B"}


def test_repo_openapi_path_can_be_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    from helpdesk_mcp import spec as spec_module

    assert spec_module.repo_openapi_path() is not None
    monkeypatch.setattr(Path, "is_file", lambda self: False)
    assert spec_module.repo_openapi_path() is None
