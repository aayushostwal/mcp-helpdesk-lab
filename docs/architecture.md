# Architecture

This page describes how the Helpdesk API is put together. The MCP server and the benchmark
(phases 2 and 3) are described at the end, because they sit on top of this service.

## Components

```mermaid
flowchart LR
    client["API client<br/>(curl, app, MCP server)"] -->|HTTP + Bearer key| mw
    subgraph svc["helpdesk process (FastAPI)"]
        mw["Middleware<br/>request id, access log,<br/>metrics, idempotency"] --> routers["Routers<br/>validation, auth scopes,<br/>response schemas"]
        routers --> services["Services<br/>business rules,<br/>transactions, audit"]
        services --> repos["Repositories<br/>queries, pagination"]
        repos --> orm["ORM models<br/>(SQLAlchemy 2.0 async)"]
    end
    orm --> db[("SQLite (default)<br/>or PostgreSQL")]
    mw -. "/metrics" .-> prom["Prometheus"]
    mw -. "JSON lines on stdout" .-> logs["Log pipeline"]
    services -. "test deliveries" .-> hooks["Webhook receivers"]
```

| Layer | Package | Allowed to depend on | Responsibility |
|---|---|---|---|
| Middleware | `helpdesk.api.middleware` | metrics, DB (idempotency only) | Cross-cutting HTTP behaviour |
| Routers | `helpdesk.api.routers` | schemas, services | Parse input, check scopes, shape output |
| Schemas | `helpdesk.api.schemas` | pydantic | The public contract (request and response types) |
| Services | `helpdesk.services` | repositories, models | Business rules, one transaction per operation, audit |
| Repositories | `helpdesk.repositories` | models | The only place that builds queries |
| Models | `helpdesk.models` | SQLAlchemy | Table definitions |

Routers never import ORM models: they receive objects from services and turn them into response
schemas with `Schema.model_validate(obj)`. Services raise typed errors from `helpdesk.errors`;
a single handler turns them into `application/problem+json` responses. The reasoning is in
[ADR 0001](adr/0001-layered-architecture.md).

## Request flow

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant M as Middleware
    participant R as Router + auth dependency
    participant S as Service
    participant D as Repository / DB
    C->>M: POST /v1/refunds (Idempotency-Key, Bearer hd_...)
    M->>M: assign X-Request-ID, start timer
    M->>D: look up (hashed Authorization, key)
    alt key seen before with same body
        M-->>C: replay stored response (Idempotent-Replayed: true)
    else new key
        M->>R: forward request
        R->>D: find key by prefix, verify sha256(salt+secret)
        R->>R: check scopes (403 insufficient_scope)
        R->>S: RefundService.create(...)
        S->>D: load order, committed refunds, settings
        S->>S: check_refund_policy (window, status, balance)
        S->>D: insert refund + audit event, COMMIT
        S-->>R: Refund
        R-->>M: 201 JSON
        M->>D: store response if 2xx
        M-->>C: 201 + X-Request-ID
    end
    M->>M: one access log line, update metrics
```

Failures at any step raise a domain error; the exception handler converts it to a problem
document and the middleware still logs and counts the request.

## Data model

```mermaid
erDiagram
    CUSTOMER ||--o{ TICKET : opens
    CUSTOMER ||--o{ ORDER : places
    CUSTOMER ||--o{ REFUND : "is owed"
    ORDER ||--|{ ORDER_ITEM : contains
    ORDER ||--o{ REFUND : "refunded by"
    ORDER |o--o{ TICKET : "mentioned in"
    TICKET ||--o{ COMMENT : has
    TICKET }o--o{ TAG : "labelled with"
    TICKET }o--o| AGENT : "assigned to"
    TICKET }o--o| TEAM : "routed to"
    TEAM ||--o{ AGENT : "has members"
    SLA_POLICY ||--o{ TICKET : "targets by priority"
    AGENT |o--o{ API_KEY : "acts as"
    WEBHOOK ||--o{ WEBHOOK_DELIVERY : records
    KB_ARTICLE }o--o| AGENT : "written by"
```

Plus three platform tables: `audit_events`, `idempotency_records`, and `sequences` (id counters
that give the `T-1042` style ids). `settings` holds runtime overrides of the business rules.

Notes:

- Ids are readable strings: `C-0311` customer, `T-1042` ticket, `O-5520` order, `R-0042` refund,
  `A-0007` agent, `TM-002` team, `CM-00123` comment, `KB-0012` article, `K-003` API key.
- Money is stored as integer cents and exposed as two-decimal strings (`"129.90"`).
- Timestamps are timezone-aware UTC everywhere. SQLite drops the time zone, so a column type
  restores it on read.
- A ticket stores its SLA due times (`first_response_due_at`, `resolution_due_at`) when it is
  created or its priority changes. Breach status is then computed from timestamps and can be
  filtered in SQL.

## Where the rules live

| Rule | Code |
|---|---|
| Ticket status machine, reopen window | `services/workflow.py` (pure functions) |
| Refund policy | `services/refund_policy.py` (pure function) + `services/refunds.py` |
| SLA evaluation | `services/sla.py` |
| Merge (tickets, customers) | `services/tickets.py`, `services/customers.py` |
| Audit recording | `services/base.py` (`BaseService.audit`) |
| Auth and scopes | `api/deps.py`, `security.py` |
| Idempotency | `api/middleware.py` |

## Transactions

Each service operation validates, mutates, writes its audit event and commits once. If anything
raises before the commit, the request's session is discarded and nothing is persisted (for
example a macro whose last action is an illegal status change leaves no partial comment behind).
Reads never commit.

## What comes next

```mermaid
flowchart LR
    agent["AI agent<br/>(Claude Code)"] -->|MCP| mcp["helpdesk_mcp<br/>phase 2"]
    mcp -->|HTTP, API key| api["Helpdesk API<br/>(this repo, phase 1)"]
    bench["bench harness<br/>phase 3"] -->|runs| agent
    bench -->|grades via| api
```

The MCP server is a thin wrapper: it never touches the database and holds no business rules.
Tools are generated from `openapi.json`, which is why every operation carries a stable
`operationId`, a summary and a description written for an AI reader.
