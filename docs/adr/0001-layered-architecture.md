# ADR 0001: Layered architecture (routers, services, repositories, models)

Status: accepted

## Context

The API has about 100 operations and real business rules (refund policy, status machine, SLA,
merges). The MCP server and the benchmark will both depend on the HTTP contract, so the
contract and the rules must be easy to find, test and keep stable.

## Decision

Four layers with one-way dependencies:

`api/routers` -> `services` -> `repositories` -> `models`

- **Routers** declare the HTTP contract: paths, `operationId`s, scopes, request and response
  schemas. They contain no rules. They never import ORM models; they convert what a service
  returns with `Schema.model_validate(obj)`.
- **Services** own business rules and transactions. A service method validates, changes state,
  records the audit event and commits once. Services raise typed domain errors
  (`helpdesk.errors`) and know nothing about HTTP.
- **Repositories** own queries and pagination. Nothing else builds SQL.
- **Models** are plain table definitions.

Rules that do not need I/O (status machine, refund policy, SLA maths) are pure functions in
their own modules, called by services.

## Consequences

- Rules are tested without HTTP or a database (`tests/unit`), and end to end through the ASGI app
  (`tests/integration`).
- A new operation touches a predictable set of files. The cost is some boilerplate: a thin
  router function per operation.
- Routers not importing models keeps the public schemas independent of the table layout, so the
  database can change without changing `openapi.json`.
- Composing operations (for example applying a macro) needs services to call each other. This is
  done through explicit non-committing `stage_*` methods so the composite stays one transaction.
