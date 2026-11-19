# mcp-helpdesk-lab: design

Status: approved for build. Owner: Aayush Ostwal.

## 1. Purpose

A public, production-grade reference repo with three jobs:

1. **Helpdesk API**: a realistic support-desk backend (customers, tickets, orders, refunds, SLAs, ...) built the way a real team would ship it.
2. **Helpdesk MCP server**: a thin MCP layer over that API that demonstrates server **instructions**, **resources** and **prompts**, and exposes the same API in three tool designs.
3. **Benchmark**: a reproducible harness that measures what tool design does to tokens, latency, cost and accuracy, using headless Claude Code (`claude -p`) as the agent.

It backs a Substack article ("Part 2: hands-on"). Every number in the article must come from this repo's `bench/results/`.

## 2. Non-negotiables

- Production grade: typed, tested, documented, logged, linted, containerised, CI-checked. Treat it as a service a company runs, not a demo.
- Python 3.12+, managed with `uv`. `src/` layout. One `pyproject.toml` with optional extras.
- `ruff` (lint + format), `mypy --strict` clean, `pytest` with coverage >= 90% on `src/`.
- No secrets in the repo. No personal data. Seed data is synthetic (Faker with a fixed seed).
- Documentation is a deliverable: README plus `docs/`, written for a reader who has never seen MCP.
- MCP server is a **thin wrapper over the HTTP API** (it never touches the DB directly). Business rules live in the API only.

## 3. Repo layout

```
mcp-helpdesk-lab/
  README.md                 # what, why, quickstart, architecture diagram, results summary
  LICENSE                   # MIT
  CONTRIBUTING.md  SECURITY.md  CHANGELOG.md  CODE_OF_CONDUCT.md
  pyproject.toml  uv.lock  Makefile  .pre-commit-config.yaml  .editorconfig
  Dockerfile  docker-compose.yml  .env.example
  .github/workflows/ci.yml  .github/ISSUE_TEMPLATE/  .github/pull_request_template.md
  docs/
    DESIGN.md               # this file
    architecture.md         # components, request flow, diagrams (mermaid)
    api.md                  # resources, auth, errors, pagination, idempotency
    mcp-server.md           # instructions/resources/prompts, the three tool variants, client config
    benchmark.md            # methodology, fairness rules, how to reproduce, threats to validity
    operations.md           # config reference, logging, metrics, health, runbook
    adr/0001-*.md ...       # architecture decision records
  src/
    helpdesk/               # the API service
    helpdesk_mcp/           # the MCP server
  bench/
    tasks/                  # task definitions (YAML) + graders (Python)
    harness/                # runner, stream parser, grader, report/chart builder
    results/                # committed raw JSONL + summary CSV + charts (PNG)
  tests/
    unit/  integration/  mcp/  bench/
  scripts/                  # seed, reset, export-openapi
```

## 4. Helpdesk API (`src/helpdesk`)

**Stack:** FastAPI, Pydantic v2, SQLAlchemy 2.0 (async), Alembic, SQLite by default via `aiosqlite`, PostgreSQL via `asyncpg` when `DATABASE_URL` says so, `structlog`, `prometheus-client`, `pydantic-settings`.

**Layers:** `api/` (routers, request/response schemas) -> `services/` (business rules, transactions) -> `repositories/` (queries) -> `models/` (ORM). Routers never import ORM models directly. Services raise typed domain errors; one exception handler maps them to responses.

**Domains (target ~100 operations total, all real and working):**

| Domain | Examples |
|---|---|
| customers | list/search, get, create, update, merge, list a customer's tickets/orders |
| agents (staff users) & teams | list, get, create, deactivate, team membership, on-call |
| tickets | list/filter (status, priority, assignee, tag, breached SLA, date range), get, create, update, assign, change status, escalate, merge, close, reopen |
| comments | list, add public reply, add internal note |
| tags | list, create, add to/remove from ticket |
| orders | list/search, get, list line items |
| refunds | list, get, create (policy-checked), approve, reject |
| SLA policies | list, get, evaluate ticket SLA status, list breaches |
| macros | list, get, apply to ticket |
| knowledge base | search articles, get, create, update, publish |
| webhooks | list, create, delete, test delivery (no real outbound calls in tests) |
| reports | ticket volume, SLA compliance, agent workload, CSAT summary |
| audit | list audit events (filter by actor, entity, action) |
| admin | settings get/update, API key list/create/revoke |

**Business rules (enforced in services, tested):**
- Refund: order must be `delivered` or `shipped`, within 30 days of delivery, amount <= order total minus prior refunds. Refunds > 500.00 are created `pending_approval`; an agent with role `lead` must approve.
- Ticket status machine: `new -> open -> pending -> solved -> closed`, `reopen` allowed from `solved` only within 7 days. Invalid transitions return 409.
- SLA: per-priority first-response and resolution targets; breach computed from timestamps.
- Merge (tickets/customers): moves comments/tickets, records the source as merged, is idempotent.

**Cross-cutting:**
- Auth: API keys (`Authorization: Bearer hd_...`), stored hashed (SHA-256 + per-key salt), with scopes (`tickets:read`, `tickets:write`, `refunds:write`, `admin`, ...). 401 vs 403 with `insufficient_scope`.
- Errors: RFC 9457 `application/problem+json` with stable `type` URIs and a `request_id`.
- Pagination: cursor-based (`limit`, `cursor`, `next_cursor`), max limit 100.
- Idempotency: `Idempotency-Key` header on all POSTs that create or move money/state; stored responses replayed for 24h.
- Audit: every write records actor, action, entity, before/after diff, request_id.
- Logging: structlog JSON to stdout, one access log line per request (method, route template, status, duration_ms, request_id, api_key_id), no secrets or PII bodies.
- Observability: `/healthz` (liveness), `/readyz` (DB check), `/metrics` (Prometheus: request count/latency by route + status).
- Config: `pydantic-settings`, all via env vars, documented in `docs/operations.md` and `.env.example`.
- OpenAPI: every operation has a stable `operationId` (e.g. `tickets_escalate`), a one-line summary and a description. `scripts/export_openapi.py` writes `openapi.json` (committed); CI fails if it drifts.

**Seed (`scripts/seed.py`, `helpdesk seed`):** deterministic, fixed seed. ~500 customers, ~40 agents in 5 teams, ~2,000 tickets with comments, ~1,500 orders, refunds, KB articles, macros, SLA policies. Seeding the same seed twice yields byte-identical DB content. The benchmark relies on known facts in the seed (see §6), so the seed must also emit `bench/tasks/fixtures.json` with the IDs/values graders need.

**CLI:** `helpdesk serve | migrate | seed | create-api-key` (Typer).

## 5. MCP server (`src/helpdesk_mcp`)

**Stack:** official `mcp` Python SDK (FastMCP; check the installed version's API by reading its source, don't guess), `httpx` async client to the Helpdesk API. Transports: stdio (default) and streamable HTTP. API base URL and key from env.

**Context features (same in every variant):**
- **Server instructions:** a short, plain description of how the app fits together: tickets belong to customers, orders belong to customers, refunds need an order ID and follow the refund policy, search before you write, IDs look like `T-1042`, `C-311`, `O-5520`. Kept under ~250 words. A flag `--no-instructions` turns it off (used in the before/after demo).
- **Resources:** `helpdesk://schema` (entity model + relationships, markdown), `helpdesk://policies/refunds`, `helpdesk://policies/sla`, `helpdesk://tickets/{ticket_id}` (resource template). Flag `--no-resources`.
- **Prompts:** `triage_ticket(ticket_id)`, `weekly_sla_report(team?)`, `refund_request(order_id, reason)`. Flag `--no-prompts`.

**Three tool designs, chosen with `--variant`:**

| Variant | Tools | How built |
|---|---|---|
| `per-endpoint` | one tool per API operation | generated from `openapi.json` (name = operationId, description = summary + description, input schema = params + body). `--tool-count N` exposes a deterministic subset: the operations any benchmark task needs are always included, the rest are filled in a fixed order until N. Counts used: 10, 25, 50, 100 (100 = all). |
| `toolsets` | ~8 domain tools (`tickets`, `customers`, `orders_refunds`, `knowledge`, `team`, `sla_reports`, `admin`, `webhooks`) each with an `action` enum and a params object | hand-written mapping onto the same operations |
| `search-call` | `search_api(query, limit)` -> ranked operations with short descriptions; `describe_operation(operation_id)` -> full input schema; `call_api(operation_id, params)` -> executes | BM25 (implemented in-repo, no heavy deps) over the OpenAPI index |

All variants must reach exactly the same operations and return the same JSON for the same call (shared executor). Errors are returned as `isError: true` tool results with the API's problem detail, never as exceptions that kill the session. Tool annotations (`readOnlyHint`, `destructiveHint`, `idempotentHint`) set from the HTTP method.

**Logging:** structlog to **stderr** (stdout is the stdio transport), one line per tool call: tool, operation_id, duration_ms, status, error type. No API key in logs.

## 6. Benchmark (`bench/`)

**Agent:** headless Claude Code, one fresh process per run:

```
claude -p "<task prompt>" --model <model> --output-format stream-json --verbose \
  --strict-mcp-config --mcp-config <generated config for this variant> \
  --allowedTools "mcp__helpdesk__*" --tools "" (or the CLI's equivalent to disable built-in tools) \
  --permission-mode bypassPermissions --max-turns 30
```

Verify every flag against `claude --help` on the installed version (2.1.x) before relying on it. Built-in tools (Bash, Read, Web...) must be disabled so the agent can only use the MCP server.

**Critical fairness control:** Claude Code can defer MCP tool definitions and load them on demand (tool search). That would hide exactly the effect we measure. Find the setting that controls it (e.g. an `ENABLE_TOOL_SEARCH`-style env var or setting; confirm in docs/`--help`/source) and **turn it off** for the main matrix. Run it **on** as a separate, clearly-labelled extra variant `per-endpoint-100+client-tool-search`.

**Matrix:**
- Variants: per-endpoint@10, @25, @50, @100, toolsets, search-call, plus the client-tool-search extra.
- Models: `sonnet` and `haiku` (aliases resolved by the CLI; record the resolved model ID from the stream).
- Tasks: 20 (see below). Repeats: 3. Total about 7 x 2 x 20 x 3 = 840 runs. Make the matrix configurable; ship a `--pilot` preset (1 model, 5 tasks, 1 repeat, 3 variants).

**Tasks (`bench/tasks/*.yaml`)**, each with `id`, `band`, `prompt`, `needs_operations`, and a grader:
- **easy** (6): single lookup or single write, e.g. "Close ticket T-1042 with the note 'resolved by phone'."
- **multi-step** (8): chained, e.g. "Refund the most recent order of the customer who opened T-0217, full amount, reason 'damaged'."
- **ambiguous** (4): tools with similar names compete, e.g. internal note vs public reply, escalate vs reassign.
- **policy** (2): the correct outcome is to refuse or route for approval, e.g. a refund outside 30 days, a refund > 500 that must be pending.

**Grading** is deterministic: after the run, inspect DB state via the API (and/or the agent's final answer for read tasks) and return pass/fail plus a reason. No LLM-as-judge.

**Isolation:** each run gets a fresh copy of the seeded SQLite file and its own API process on a free port. Runs execute with a configurable worker pool (default 4). A crashed or timed-out run is recorded as such, not dropped.

**Recorded per run (JSONL, one line per run):** run_id, timestamp, variant, tool_count, model (requested + resolved), task_id, band, repeat, pass, grade_reason, wall_ms, api_ms, ttft_ms, num_turns, tool_calls (ordered list of {tool, operation_id, ok, ms}), wrong_tool_calls (calls to operations not in `needs_operations`), input/output/cache-read/cache-creation tokens, total_cost_usd as reported by the CLI, exit status, error.

**Baselines:**
- `context-cost`: for every variant, one run with the prompt "Reply ok" to measure what loading the server adds to the first request (tokens with server minus tokens with no server). This gives the clean "tool definitions cost X tokens" chart.

**Report (`bench/harness/report.py`):** reads JSONL, writes `bench/results/summary.csv` and PNG charts at 2x scale:
1. context tokens added by the server vs tool count (search-call and toolsets as reference lines)
2. input tokens per task by variant
3. pass rate by variant x band (with 95% Wilson intervals)
4. wall time distribution by variant (box plot)
5. wrong-tool calls on ambiguous tasks
6. CLI-reported cost per 1,000 tasks by variant x model

Charts: clean, labelled, no chartjunk, colour-blind-safe palette, readable at phone width. Every chart states n.

**Threats to validity** (must be written up in `docs/benchmark.md`): one app, one client, synthetic data, prompt caching effects, CLI overhead included in tokens, small n per cell.

## 7. Testing

- Unit: services and business rules (refund policy, status machine, SLA math, merge), BM25 ranking, OpenAPI-to-tool generation, stream-json parser, graders.
- Integration: API routers against a temp SQLite DB through `httpx.AsyncClient` (ASGI transport); auth/scopes; errors; pagination; idempotency replay; audit records.
- MCP: start the MCP server in-process with the SDK's client, list tools per variant and count, call tools end-to-end against a live test API, check instructions/resources/prompts are served and the `--no-*` flags work, check all three variants return identical results for the same operation.
- Bench: harness dry-run with a fake `claude` binary that replays a recorded stream, so CI never calls a model.
- CI (GitHub Actions): ruff, ruff format --check, mypy, pytest + coverage gate, openapi drift check, Docker build. Postgres job via service container for the integration suite.

## 8. Build phases

1. Helpdesk API + seed + tests + API docs (this design §4, §7).
2. MCP server + variants + tests + MCP docs (§5).
3. Benchmark harness + pilot + docs (§6).
4. Full benchmark run, charts, README results section.
5. Fresh-agent demo (screenshots, timeline, accuracy), used by the article.
