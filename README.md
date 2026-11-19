# mcp-helpdesk-lab

A reference repository with three parts that build on each other:

1. **Helpdesk API** (done, phase 1): a support-desk backend with about 100 operations across
   customers, tickets, orders, refunds, SLAs, a knowledge base and more. It is built the way a
   team would ship a real service: layered code, strict types, tests, migrations, structured
   logs, metrics, scoped API keys, an audit trail and an OpenAPI contract.
2. **Helpdesk MCP server** (*coming in phase 2*): a thin [MCP](https://modelcontextprotocol.io)
   layer over that API, offered in three tool designs, with server instructions, resources and
   prompts.
3. **Benchmark** (*coming in phase 3*): a reproducible harness that uses headless Claude Code to
   measure what the tool design does to tokens, latency, cost and accuracy.

The API exists first because the other two parts need something realistic to talk to: enough
operations to make tool design matter, real business rules to make accuracy measurable, and a
deterministic dataset so every benchmark run starts from the same state.

If you have never seen MCP: it is a protocol that lets an AI agent discover and call tools
exposed by a server. Each tool has a name, a description and an input schema. Agents read all of
that text before they act, so how an API is turned into tools changes how well the agent does.
This repo is the place to measure that.

## Status

| Phase | Scope | State |
|---|---|---|
| 1 | Helpdesk API, seed, CLI, tests, API docs | **Done** |
| 2 | MCP server and the three tool designs | Coming in phase 2 |
| 3 | Benchmark harness and pilot | Coming in phase 3 |
| 4 | Full benchmark run and results | Not started |

The full plan is in [docs/DESIGN.md](docs/DESIGN.md).

## Quickstart

You need [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 for you). Docker is optional.

```bash
git clone https://github.com/aayushostwal/mcp-helpdesk-lab.git
cd mcp-helpdesk-lab

make install      # create .venv and install everything
make seed         # create a SQLite database and fill it with synthetic data
make key          # print an API key linked to lead agent A-0002 (copy it)
make serve        # API on http://127.0.0.1:8000, docs at /docs
```

In another terminal:

```bash
export KEY=hd_...   # the key printed by `make key`
curl -s localhost:8000/healthz
curl -s "localhost:8000/v1/tickets?status=open&limit=3" -H "Authorization: Bearer $KEY"

# A refund above 500.00 is held for a lead to approve:
curl -s -X POST localhost:8000/v1/refunds \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-1' \
  -d '{"order_id":"O-1502","amount":"680.00","reason":"defective"}'
# -> "status": "pending_approval"
```

`make serve` runs the API with its clock pinned to the seed's reference date so the seeded
orders fall inside the 30-day refund window. See
[docs/operations.md](docs/operations.md#seed-data-and-the-clock).

### With Docker and PostgreSQL

```bash
docker compose up --build
docker compose run --rm api helpdesk seed
docker compose run --rm api helpdesk create-api-key --name local --scopes admin
```

## Architecture

```mermaid
flowchart LR
    subgraph later["Coming in phases 2 and 3"]
        agent["AI agent<br/>(headless Claude Code)"] -->|MCP| mcp["helpdesk_mcp<br/>3 tool designs"]
        bench["bench harness<br/>tasks, graders, charts"] --> agent
    end
    mcp -->|"HTTP + API key"| api
    bench -->|"grades by reading state"| api
    subgraph phase1["Helpdesk API (this release)"]
        api["Middleware<br/>request id, logs, metrics, idempotency"] --> routers["Routers"] --> services["Services<br/>business rules"] --> repos["Repositories"] --> db[("SQLite or<br/>PostgreSQL")]
    end
```

More detail, including the request flow and data model, is in
[docs/architecture.md](docs/architecture.md).

## What the API does

| Area | Highlights |
|---|---|
| Customers, orders | Search, create, merge duplicates; order lifecycle (placed, shipped, delivered, cancelled) |
| Tickets and comments | Filter by status, priority, assignee, tag, SLA breach, dates; status machine; escalate, merge, close, reopen; public replies vs internal notes |
| Refunds | Policy-checked: order status, 30-day window, remaining balance; amounts above 500.00 wait for a lead's approval |
| SLA | Per-priority targets, breach computed from timestamps, evaluation and reports |
| Agents, teams | Roles, membership, on-call, deactivation that releases tickets |
| Macros, knowledge base | Canned replies with actions; searchable articles with draft and publish |
| Webhooks | Subscriptions, signed test deliveries |
| Reports, audit, admin | Volume, SLA compliance, workload, CSAT; every write is audited; runtime settings and API keys |

Cross-cutting behaviour: [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457) problem responses,
cursor pagination, `Idempotency-Key` on POSTs, hashed API keys with scopes, JSON access logs with
request ids, Prometheus metrics at `/metrics`, `/healthz` and `/readyz`. Details in
[docs/api.md](docs/api.md).

## Repository layout

```
src/helpdesk/        the API: api/ (routers, schemas), services/, repositories/, models/,
                     migrations/, seeding.py, cli.py
src/helpdesk_mcp/    MCP server (phase 2)
bench/               benchmark (phase 3); tasks/fixtures.json is generated by the seed
tests/               unit/ and integration/
scripts/             export_openapi.py, seed.py
docs/                architecture, API, operations, decision records
openapi.json         the committed API contract (CI fails if it is stale)
```

## Development

```bash
make check      # ruff, mypy --strict, tests with a 90% coverage gate, OpenAPI drift check
make fmt        # auto-format
make openapi    # regenerate openapi.json after changing routes or schemas
```

Tests run on SQLite by default. To run the integration suite on PostgreSQL:

```bash
TEST_DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/db uv run pytest tests/integration
```

CI (GitHub Actions) runs lint, format check, mypy, the tests with the coverage gate, the OpenAPI
drift check, the integration suite against a PostgreSQL service container, and a Docker build.
See [CONTRIBUTING.md](CONTRIBUTING.md).

## Documentation

- [docs/architecture.md](docs/architecture.md): components, request flow, data model
- [docs/api.md](docs/api.md): auth and scopes, errors, pagination, idempotency, business rules
- [docs/operations.md](docs/operations.md): configuration, logs, metrics, health, runbook
- [docs/adr/](docs/adr/): decision records (layering, databases, API keys, errors, pagination,
  idempotency, clock and seed)
- [docs/DESIGN.md](docs/DESIGN.md): the full design, including the MCP server and benchmark
- [docs/mcp-server.md](docs/mcp-server.md), [docs/benchmark.md](docs/benchmark.md): placeholders
  until phases 2 and 3

## Known limits

- Webhooks can be created and test-delivered, but the service does not yet send events when
  tickets change.
- No rate limiting; put the service behind a gateway. See [SECURITY.md](SECURITY.md).
- The data is synthetic. Names and emails are generated and use reserved example domains.

## License

[MIT](LICENSE). Copyright (c) 2026 Aayush Ostwal.
