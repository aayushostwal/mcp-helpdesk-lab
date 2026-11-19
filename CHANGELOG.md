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

### Planned

- Phase 2: MCP server (`helpdesk_mcp`) with three tool designs.
- Phase 3: benchmark harness.
