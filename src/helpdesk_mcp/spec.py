"""Read ``openapi.json`` and turn each operation into a plain description of one tool.

Nothing here knows about MCP or HTTP clients. An :class:`Operation` carries what every variant
needs: the id, the HTTP request shape, the text for the model, and one flat JSON schema that
merges path parameters, query parameters and the request body.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from helpdesk_mcp.settings import McpSettings

HTTP_METHODS = ("get", "post", "put", "patch", "delete")
COMPONENT_PREFIX = "#/components/schemas/"
DEFS_PREFIX = "#/$defs/"


class SpecError(RuntimeError):
    """The OpenAPI document cannot be turned into tools."""


@dataclass(frozen=True)
class Parameter:
    name: str
    location: str  # "path" or "query"
    required: bool


@dataclass(frozen=True)
class Operation:
    operation_id: str
    method: str  # upper case
    path: str
    tag: str
    summary: str
    description: str
    parameters: tuple[Parameter, ...]
    body_fields: tuple[str, ...]
    body_required: bool
    scopes: tuple[str, ...]
    input_schema: dict[str, Any] = field(compare=False, repr=False)

    @property
    def tool_description(self) -> str:
        """Summary, a blank line, then the long description (what a per-endpoint tool shows)."""
        if self.description and self.description != self.summary:
            return f"{self.summary}\n\n{self.description}"
        return self.summary

    @property
    def argument_names(self) -> tuple[str, ...]:
        return (*(p.name for p in self.parameters), *self.body_fields)

    @property
    def required_arguments(self) -> tuple[str, ...]:
        return tuple(self.input_schema.get("required", ()))

    @property
    def has_body(self) -> bool:
        return bool(self.body_fields) or self.body_required


@dataclass(frozen=True)
class Spec:
    title: str
    version: str
    operations: dict[str, Operation]

    def get(self, operation_id: str) -> Operation | None:
        return self.operations.get(operation_id)


def _rewrite_refs(node: Any, used: set[str]) -> Any:
    """Copy a schema, pointing ``#/components/schemas/X`` at ``#/$defs/X`` and noting each X."""
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str) and value.startswith(COMPONENT_PREFIX):
                name = value[len(COMPONENT_PREFIX) :]
                used.add(name)
                out[key] = DEFS_PREFIX + name
            else:
                out[key] = _rewrite_refs(value, used)
        return out
    if isinstance(node, list):
        return [_rewrite_refs(item, used) for item in node]
    return node


def _ref_name(schema: dict[str, Any]) -> str | None:
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith(COMPONENT_PREFIX):
        return ref[len(COMPONENT_PREFIX) :]
    return None


def _body_object(
    body_schema: dict[str, Any], components: dict[str, Any], operation_id: str
) -> dict[str, Any]:
    """Resolve the request body to its object schema (an optional body is ``X | null``)."""
    candidates = [
        option for option in body_schema.get("anyOf", [body_schema]) if option.get("type") != "null"
    ]
    if len(candidates) != 1:
        raise SpecError(f"{operation_id}: unsupported request body schema")
    schema = candidates[0]
    name = _ref_name(schema)
    if name is not None:
        schema = components[name]
    if schema.get("type") != "object":
        raise SpecError(f"{operation_id}: request body is not an object")
    return schema  # type: ignore[no-any-return]


def _build_operation(
    path: str,
    method: str,
    raw: dict[str, Any],
    path_level_params: list[dict[str, Any]],
    components: dict[str, Any],
) -> Operation:
    operation_id = raw["operationId"]
    used: set[str] = set()
    properties: dict[str, Any] = {}
    required: list[str] = []
    parameters: list[Parameter] = []

    for param in [*path_level_params, *raw.get("parameters", [])]:
        location = param["in"]
        if location not in ("path", "query"):
            raise SpecError(f"{operation_id}: unsupported parameter location '{location}'")
        name = param["name"]
        schema = _rewrite_refs(param.get("schema", {}), used)
        if "description" in param and "description" not in schema:
            schema["description"] = param["description"]
        properties[name] = schema
        is_required = bool(param.get("required")) or location == "path"
        parameters.append(Parameter(name, location, is_required))
        if is_required:
            required.append(name)

    body_fields: tuple[str, ...] = ()
    body_required = False
    request_body = raw.get("requestBody")
    if request_body:
        content = request_body.get("content", {})
        if "application/json" not in content:
            raise SpecError(f"{operation_id}: only JSON request bodies are supported")
        body = _body_object(content["application/json"].get("schema", {}), components, operation_id)
        body_required = bool(request_body.get("required"))
        names = list(body.get("properties", {}))
        clash = set(names) & set(properties)
        if clash:
            raise SpecError(f"{operation_id}: body fields clash with parameters: {sorted(clash)}")
        for name, schema in body.get("properties", {}).items():
            properties[name] = _rewrite_refs(schema, used)
        body_fields = tuple(names)
        if body_required:
            required.extend(body.get("required", []))

    defs: dict[str, Any] = {}
    queue = sorted(used)
    while queue:
        name = queue.pop()
        if name in defs:
            continue
        nested: set[str] = set()
        defs[name] = _rewrite_refs(components[name], nested)
        queue.extend(sorted(nested - defs.keys()))

    input_schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        input_schema["required"] = required
    input_schema["additionalProperties"] = False
    if defs:
        input_schema["$defs"] = dict(sorted(defs.items()))

    scopes = tuple(
        scope
        for requirement in raw.get("security", [])
        for scope in requirement.get("HTTPBearer", [])
    )
    return Operation(
        operation_id=operation_id,
        method=method.upper(),
        path=path,
        tag=(raw.get("tags") or ["untagged"])[0],
        summary=raw.get("summary", "").strip(),
        description=raw.get("description", "").strip(),
        parameters=tuple(parameters),
        body_fields=body_fields,
        body_required=body_required,
        scopes=scopes,
        input_schema=copy.deepcopy(input_schema),
    )


def parse_spec(document: dict[str, Any]) -> Spec:
    """Build a :class:`Spec` from a parsed OpenAPI document."""
    components = document.get("components", {}).get("schemas", {})
    operations: dict[str, Operation] = {}
    for path, item in document.get("paths", {}).items():
        path_level = item.get("parameters", [])
        for method in HTTP_METHODS:
            raw = item.get(method)
            if raw is None:
                continue
            if "operationId" not in raw:
                raise SpecError(f"{method.upper()} {path} has no operationId")
            op = _build_operation(path, method, raw, path_level, components)
            if op.operation_id in operations:
                raise SpecError(f"duplicate operationId {op.operation_id}")
            operations[op.operation_id] = op
    info = document.get("info", {})
    return Spec(info.get("title", ""), info.get("version", ""), operations)


def repo_openapi_path() -> Path | None:
    """The bundled copy (installed wheel) or the committed one next to ``src/`` (checkout)."""
    here = Path(__file__).resolve()
    for candidate in (here.parent / "openapi.json", here.parents[2] / "openapi.json"):
        if candidate.is_file():
            return candidate
    return None


def load_spec(settings: McpSettings) -> Spec:
    """Load the OpenAPI document: explicit path, then the repository copy, then the live API."""
    path = settings.openapi_path or repo_openapi_path()
    if path is not None:
        return parse_spec(json.loads(Path(path).read_text(encoding="utf-8")))
    try:
        response = httpx.get(f"{settings.base_url}/openapi.json", timeout=settings.timeout_seconds)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise SpecError(
            "No openapi.json found and the API did not serve one at "
            f"{settings.base_url}/openapi.json ({exc.__class__.__name__}). "
            "Set HELPDESK_MCP_OPENAPI_PATH."
        ) from exc
    return parse_spec(response.json())
