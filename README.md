# mcp-helpdesk-lab

A reference repository with three parts that build on each other:

1. **Helpdesk API** (done, phase 1): a support-desk backend with about 100 operations across
   customers, tickets, orders, refunds, SLAs, a knowledge base and more. It is built the way a
   team would ship a real service: layered code, strict types, tests, migrations, structured
   logs, metrics, scoped API keys, an audit trail and an OpenAPI contract.
2. **Helpdesk MCP server** (done, phase 2): a thin [MCP](https://modelcontextprotocol.io)
   layer over that API, offered in three tool designs (one tool per endpoint, grouped toolsets,
   search-then-call), with server instructions, resources and prompts.
3. **Benchmark** (harness and pilot done, phase 3): a reproducible harness that uses headless Claude Code to
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
| 2 | MCP server and the three tool designs | **Done** |
| 3 | Benchmark harness and pilot | **Done** (see [Benchmark](#benchmark)) |
| 4 | Full benchmark run and results | **Done** (see [Benchmark results](#benchmark-results)) |

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

### The MCP server

With the API running and a key in `HELPDESK_MCP_API_KEY` (see above), start the server over
stdio. A client such as Claude Code starts it for you:

```bash
export HELPDESK_MCP_API_KEY=hd_...       # the key printed by `make key`
make mcp VARIANT=search-call             # or per-endpoint, toolsets; MCP_ARGS="--tool-count 25"
make inspector                           # browse it in the MCP Inspector (needs Node.js)

claude mcp add helpdesk -e HELPDESK_MCP_API_KEY=$HELPDESK_MCP_API_KEY \
  -- uv run --directory "$PWD" helpdesk-mcp --variant search-call
```

Variants, flags, client configuration for Claude Code, Claude Desktop and the Inspector, and the
size of each tool list are in [docs/mcp-server.md](docs/mcp-server.md).

### With Docker and PostgreSQL

```bash
docker compose up --build
docker compose run --rm api helpdesk seed
docker compose run --rm api helpdesk create-api-key --name local --scopes admin
```

## Architecture

```mermaid
flowchart LR
    subgraph phase3["Benchmark (phase 3)"]
        agent["AI agent<br/>(headless Claude Code)"]
        bench["bench harness<br/>tasks, graders, charts"] --> agent
    end
    agent -->|MCP| mcp["helpdesk_mcp (phase 2)<br/>3 tool designs"]
    mcp -->|"HTTP + API key"| api
    bench -->|"grades by reading state"| api
    subgraph phase1["Helpdesk API (phase 1)"]
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
src/helpdesk_mcp/    MCP server: variants, executor, BM25 search, resources, prompts, CLI
bench/               benchmark: tasks/ (20 YAML tasks, graders, oracles, fixtures.json from the
                     seed), harness/ (runner, stream parser, report), results/
tests/               unit/, integration/, mcp/ and bench/
scripts/             export_openapi.py, seed.py, mcp_token_report.py
docs/                architecture, API, operations, decision records
openapi.json         the committed API contract (CI fails if it is stale)
```

## Development

```bash
make check      # ruff, mypy --strict, tests with 90% coverage gates, OpenAPI drift check
make fmt        # auto-format
make openapi    # regenerate openapi.json after changing routes or schemas
make tool-sizes # regenerate docs/tool-list-sizes.md after changing tools
```

Tests run on SQLite by default. To run the integration suite on PostgreSQL:

```bash
TEST_DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/db uv run pytest tests/integration
```

CI (GitHub Actions) runs lint, format check, mypy, the tests with the coverage gate, the OpenAPI
drift check, the integration suite against a PostgreSQL service container, and a Docker build.
See [CONTRIBUTING.md](CONTRIBUTING.md).

## Benchmark

`python -m bench` measures what the tool design does, with headless Claude Code (`claude -p`) as
the agent: 20 tasks in four bands (easy, multi-step, ambiguous, policy), graded by reading the
database through the API. Methodology and the fairness rules are in
[docs/benchmark.md](docs/benchmark.md).

```bash
make bench-check    # prove every task is solvable and fails when nothing is done (no model)
make bench-dry      # whole pipeline with a fake claude (no model, no cost)
make bench-pilot    # about 27 real runs on your Claude login (haiku), then the report
make bench-report   # rebuild bench/results/summary.csv, summary.md and the charts
```

## Benchmark results

The full matrix has been run: 780 graded task runs (7 variants, 2 models, 20 tasks, 3 repeats;
`per-endpoint-10` runs only the 10 tasks it can solve), plus context-baseline runs. The headline
numbers are in [bench/results/KEY_NUMBERS.md](bench/results/KEY_NUMBERS.md) (plain tables, all
generated from the result files), with the full tables in
[summary.md](bench/results/summary.md), [summary.csv](bench/results/summary.csv) and
[summary_by_task.csv](bench/results/summary_by_task.csv). Charts:

1. [Context tokens added vs tool count](bench/results/charts/1-context-tokens-vs-tool-count.png)
2. [Input tokens per task](bench/results/charts/2-input-tokens-per-task.png)
3. [Pass rate by band](bench/results/charts/3-pass-rate-by-band.png)
4. [Wall time](bench/results/charts/4-wall-time.png)
5. [Wrong-tool calls on ambiguous tasks](bench/results/charts/5-wrong-calls-ambiguous.png)
6. [Cost per task](bench/results/charts/6-cost-per-task.png)
7. [Pass rate per variant and model](bench/results/charts/7-pass-rate-by-variant.png)

What the data show, and what they do not:

- The size of the tool list is measured exactly. The 100-tool per-endpoint server adds about
  27,300 input tokens to the first request, the toolsets server about 6,100, search-call about
  1,400 and the 100-tool server with client tool search about 3,300. Median input tokens per
  task follow the same order.
- Accuracy differences between variants are **within the confidence intervals**. Over all
  tasks the pass rate per variant and model runs from 91.7% (per-endpoint-50, haiku, 55/60,
  interval 81.9% to 96.4%) to 100% (search-call, sonnet, 60/60, interval 94.0% to 100.0%);
  every one of those intervals overlaps with the others. The data do not show that one design is
  more accurate than another, and they do not show a cause for any difference.
- Two graded tasks need care. `multi-04-merge-duplicate-customers` has a seed flaw (haiku did not
  merge in any variant) and the `multi-05` grader was corrected after the run; both are described
  in [docs/benchmark.md](docs/benchmark.md#post-hoc-changes-and-known-task-flaws). Pass rates are
  given with and without multi-04. `per-endpoint-10` covers only ten tasks, so its pass rate is
  not comparable with the others.

Limits: one app (a synthetic helpdesk with 102 operations), one client (Claude Code 2.1.326),
synthetic data, 20 tasks written by the people who built the API, n=60 runs per variant and
model cell (30 for per-endpoint-10), and the cost figures are the CLI's list-price equivalent,
not an invoice. Models are resolved by alias at run time. Treat the numbers as a measurement of
this setup, not as a ranking of tool designs in general.

Rebuild the report with `make bench-report`.

## Documentation

- [docs/architecture.md](docs/architecture.md): components, request flow, data model
- [docs/api.md](docs/api.md): auth and scopes, errors, pagination, idempotency, business rules
- [docs/operations.md](docs/operations.md): configuration, logs, metrics, health, runbook
- [docs/mcp-server.md](docs/mcp-server.md): the MCP server, its three tool designs, client setup
- [docs/tool-list-sizes.md](docs/tool-list-sizes.md): tool count and approximate size per variant
- [docs/adr/](docs/adr/): decision records (layering, databases, API keys, errors, pagination,
  idempotency, clock and seed, thin MCP wrapper, shared executor)
- [docs/DESIGN.md](docs/DESIGN.md): the full design, including the MCP server and benchmark
- [docs/benchmark.md](docs/benchmark.md): benchmark methodology, fairness rules, how to reproduce,
  threats to validity

## Known limits

- The MCP server uses one API key for all calls, and its HTTP transport has no authentication of
  its own. See [docs/mcp-server.md](docs/mcp-server.md#limits).
- Webhooks can be created and test-delivered, but the service does not yet send events when
  tickets change.
- No rate limiting; put the service behind a gateway. See [SECURITY.md](SECURITY.md).
- The data is synthetic. Names and emails are generated and use reserved example domains.

## License

[MIT](LICENSE). Copyright (c) 2026 Aayush Ostwal.
