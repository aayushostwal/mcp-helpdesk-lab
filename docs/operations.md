# Operations

How to configure, run, observe and troubleshoot the Helpdesk API.

## Running it

| How | Command |
|---|---|
| Local, SQLite | `make seed && make serve` (or `helpdesk migrate && helpdesk serve`) |
| Docker with PostgreSQL | `docker compose up --build`, then `docker compose run --rm api helpdesk seed` |
| Container image only | `docker build -t helpdesk . && docker run -p 8000:8000 -e HELPDESK_DATABASE_URL=... helpdesk` |

The container runs `helpdesk migrate` and then `helpdesk serve` as an unprivileged user. It has a
`HEALTHCHECK` against `/healthz`. Run one process per container and scale horizontally;
nothing is kept in process memory except metrics counters.

### Bootstrapping access

A fresh database has no API keys. Create one with database access:

```bash
helpdesk create-api-key --name bootstrap --scopes admin
```

The token is printed once. Use it to create narrower keys through
`POST /v1/admin/api-keys`, then revoke the bootstrap key if you do not need it.

### Seed data and the clock

`helpdesk seed` writes about 500 customers, 40 agents in 5 teams, 2,000 tickets with comments,
1,500 orders, refunds, articles, macros and SLA policies. It is deterministic: the same `--seed`
and `--as-of` always give the same rows. All generated dates are relative to `as_of`
(default `2026-06-01T12:00:00+00:00`), not to the wall clock.

Rules such as the 30-day refund window use the service clock. To keep seeded data meaningful,
run the API with `HELPDESK_FROZEN_TIME` set to the same instant (`make serve` does this). In
production leave it unset. `helpdesk seed` also writes `bench/tasks/fixtures.json`: the ids and
facts (for example "an order that needs lead approval") that the benchmark relies on.

Seeded data contains no API keys and no audit events.

## Configuration reference

All settings are environment variables read by `pydantic-settings`. A `.env` file in the working
directory is also read. See `.env.example`.

| Variable | Default | Description |
|---|---|---|
| `HELPDESK_DATABASE_URL` or `DATABASE_URL` | `sqlite+aiosqlite:///./helpdesk.db` | SQLAlchemy URL. `sqlite://`, `postgres://` and `postgresql://` are rewritten to the async drivers. |
| `HELPDESK_ENVIRONMENT` | `dev` | `dev`, `test` or `prod`. Informational. |
| `HELPDESK_LOG_LEVEL` | `INFO` | Python log level name. |
| `HELPDESK_LOG_JSON` | `true` | JSON lines; `false` gives console-friendly output. |
| `HELPDESK_HOST` / `HELPDESK_PORT` | `127.0.0.1` / `8000` | Bind address for `helpdesk serve`. The container sets host `0.0.0.0`. |
| `HELPDESK_FROZEN_TIME` | unset | ISO 8601 instant that pins the service clock. Demos and benchmarks only. |
| `HELPDESK_REFUND_WINDOW_DAYS` | `30` | Default refund window. Overridable at runtime. |
| `HELPDESK_REFUND_APPROVAL_THRESHOLD` | `500.00` | Refunds above this need a lead. Overridable at runtime. |
| `HELPDESK_REOPEN_WINDOW_DAYS` | `7` | How long after solving a ticket can be reopened. Overridable at runtime. |
| `HELPDESK_WEBHOOK_DELIVERY` | `live` | `live` sends HTTP requests; `simulated` always reports success without any network call. |
| `HELPDESK_WEBHOOK_TIMEOUT_SECONDS` | `5` | Per-delivery timeout. |
| `HELPDESK_WEBHOOK_ALLOW_PRIVATE_HOSTS` | `false` | Allow localhost and private addresses as webhook targets (development only). |
| `HELPDESK_IDEMPOTENCY_TTL_HOURS` | `24` | How long stored idempotent responses are kept. |
| `HELPDESK_PROBLEM_BASE_URI` | `https://helpdesk.example/problems/` | Prefix of problem `type` URIs. |

The three business-rule defaults can be changed without a restart through
`PATCH /v1/admin/settings`; the stored value wins over the environment value.

## Health

| Endpoint | Purpose | Healthy |
|---|---|---|
| `GET /healthz` | Liveness: the process is up. No dependencies. | `200 {"status":"ok"}` |
| `GET /readyz` | Readiness: runs `SELECT 1` against the database. | `200 {"status":"ready","database":"ok"}`, otherwise `503` problem |

Use `/healthz` for restart decisions and `/readyz` for load balancer membership.

## Logging

One JSON line per request to **stdout**, plus application events. Example access line:

```json
{"method": "POST", "route": "/v1/refunds", "status": 201, "duration_ms": 14.2,
 "api_key_id": "K-003", "request_id": "9f0c2a7e5b6d4c3a8e1f0a2b3c4d5e6f",
 "event": "request", "level": "info", "timestamp": "2026-06-01T12:00:00.123456Z"}
```

- `route` is the template (`/v1/tickets/{ticket_id}`), never the raw path, so logs and metrics
  have bounded cardinality and ids do not leak into aggregates.
- Request and response bodies, query strings and the `Authorization` header are never logged.
- The request id comes from the caller's `X-Request-ID` if it matches `[A-Za-z0-9._-]{8,64}`,
  otherwise it is generated. It is returned as `X-Request-ID`, put in problem documents, stored
  on audit events and bound to every log line in the request.
- Unhandled exceptions are logged once with a stack trace (`unhandled_exception`) and answered
  with a generic 500 problem.

Find everything about one request: search logs for its `request_id`, then
`GET /v1/audit/events?request_id=...` for the writes it made.

## Metrics

`GET /metrics` serves Prometheus text format. It is unauthenticated: restrict it at the network
level.

| Metric | Type | Labels |
|---|---|---|
| `helpdesk_http_requests_total` | counter | `method`, `route`, `status` |
| `helpdesk_http_request_duration_seconds` | histogram | `method`, `route` |

Unmatched paths share the route label `unmatched`. Replayed idempotent responses are answered
before routing and are counted under the label `idempotent_replay`.

Useful queries:

```promql
sum by (route) (rate(helpdesk_http_requests_total{status=~"5.."}[5m]))
histogram_quantile(0.95, sum by (le, route) (rate(helpdesk_http_request_duration_seconds_bucket[5m])))
```

## Database and migrations

- Schema changes ship as Alembic migrations in `src/helpdesk/migrations/versions`.
  `helpdesk migrate` applies them; the container does this on start.
- Create a migration: edit the models, run
  `uv run alembic revision --autogenerate -m "describe change"`, review the file.
  A test fails if models and migrations drift.
- SQLite is the default and suits development and the benchmark. Use PostgreSQL for anything
  shared: see [ADR 0002](adr/0002-sqlite-and-postgresql.md).
- Backups are the database's job (`pg_dump`, volume snapshots). For SQLite, copy the file while
  the service is stopped or use `sqlite3 helpdesk.db ".backup copy.db"`.
- Maintenance: idempotency records older than the TTL are ignored and replaced when the same key
  is used again; there is no background job, so the table grows by one row per idempotent POST.
  Delete rows older than a week from `idempotency_records` periodically if volume is high.

## Runbook

| Symptom | Likely cause | What to do |
|---|---|---|
| Every call returns 401 | Missing or revoked key | `GET /v1/admin/whoami` with the key; create a new one with `helpdesk create-api-key`. |
| 403 `insufficient-scope` | Key lacks a scope | The problem body lists `missing_scopes`. Create a key with them. |
| 403 `agent-role-required` on refund approval | Key not linked to a lead | Create a key with `--agent <lead id>`; check `admin_whoami` shows `agent_role: lead`. |
| Refunds on seeded data fail with `refund-not-eligible` | Service clock does not match the seed | Set `HELPDESK_FROZEN_TIME` to the seed's `as_of`. |
| `/readyz` returns 503 | Database unreachable | Check `HELPDESK_DATABASE_URL`, network, credentials. Liveness stays green on purpose. |
| `helpdesk seed` says tables are missing | Migrations not applied | Run `helpdesk migrate`. |
| `helpdesk seed` says the database has data | Seed refuses to merge into existing data | Use `--reset` (deletes all rows) or a new database. |
| A POST returned an old response | Idempotent replay | Check for `Idempotent-Replayed: true`; use a new `Idempotency-Key` for a new operation. |
| 500 with a `request_id` | Bug or infrastructure failure | Search the logs for that `request_id`, look for `unhandled_exception`. |
| Slow list endpoints | Missing filters on a large table | Use filters and `limit`; check the route's p95 in `/metrics`. |

## Security notes

- Keep `/metrics` and `/docs` behind your network controls.
- Terminate TLS in front of the service; API keys are bearer credentials.
- Run with a database user that cannot alter schemas, and migrate with a separate one.
- See `SECURITY.md` for known limits (no rate limiting, SSRF guard is name-based).
