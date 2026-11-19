# Changelog

All notable changes are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Helpdesk API (phase 1): about 100 operations across customers, agents and teams, tickets,
  comments, tags, orders, refunds, SLA policies, macros, knowledge base, webhooks, reports,
  audit and admin.
- Scoped, hashed API keys; RFC 9457 problem responses; cursor pagination; idempotency keys;
  audit log on every write; structured access logs; Prometheus metrics; health and readiness
  endpoints.
- Alembic migrations, `helpdesk` CLI (`serve`, `migrate`, `seed`, `create-api-key`), and a
  deterministic seed that also writes `bench/tasks/fixtures.json`.
- Unit and integration test suites, CI workflow, Dockerfile and docker-compose.

- MCP server (phase 2), `helpdesk-mcp`: a thin httpx wrapper over the API using the official
  `mcp` SDK, over stdio or streamable HTTP. Server instructions, four resources
  (`helpdesk://schema`, `helpdesk://policies/refunds`, `helpdesk://policies/sla`,
  `helpdesk://tickets/{ticket_id}`) and three prompts, each switchable with `--no-*` flags.
- Three tool designs sharing one executor: `per-endpoint` (generated from `openapi.json`, with
  `--tool-count` subsets), `toolsets` (8 tools with `action` and `params`) and `search-call`
  (in-repo BM25 search, describe, call). Errors are `isError` results with the API's problem
  document; tool annotations come from the HTTP method; POSTs carry an `Idempotency-Key`.
- Structured stderr logging of every tool call, `scripts/mcp_token_report.py` and
  `docs/tool-list-sizes.md`, `make mcp`, `make inspector`, `make tool-sizes`.
- MCP test suite (`tests/mcp`) with a 90% coverage gate on `src/helpdesk_mcp`, and two ADRs.
- `mcp` is now a core dependency (`>=1.30,<2`); the Dockerfile copies `openapi.json` because it
  is bundled into the wheel.

### Planned

- Phase 3: benchmark harness.
