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

- Benchmark harness (phase 3), `python -m bench`: runs headless Claude Code (`claude -p`, on the
  user's login, one fresh process per run) against a per-run copy of the seeded database and its
  own API process; 20 tasks in four bands with deterministic graders and scripted oracles;
  resumable matrix runner with a worker pool, bounded retries for infrastructure errors only,
  hard process-group kill on timeout; context-cost baselines; stream-json parser; report with
  `summary.csv`, `summary.md` and six PNG charts; `--dry-run` with a fake `claude` for CI;
  `probe-tool-search` to measure the `ENABLE_TOOL_SEARCH` behaviour.
- `docs/benchmark.md`, `make bench-check`, `make bench-pilot`, `make bench-dry`,
  `make bench-report`, a bench coverage gate (90%) and a dry run in CI.
- Pilot results in `bench/results/` (haiku, 15 task runs, plus context baselines).

### Changed

- `ALWAYS_INCLUDED` in `helpdesk_mcp.subset` now starts with exactly the operations the benchmark
  tasks need, so every task is solvable at 25, 50 and 100 tools (ten of twenty are not at 10).
  The tool lists for `--tool-count` 10, 25 and 50 change; `docs/tool-list-sizes.md` is regenerated.
