# ADR 0004: RFC 9457 problem+json for every error

Status: accepted

## Context

Clients, and especially AI agents, decide what to do next from the error. They need a
machine-readable category and a human-readable explanation, in one consistent shape,
including for framework-generated errors (404 on unknown routes, validation failures).

## Decision

- Every error response is `application/problem+json` with `type`, `title`, `status`, `detail`,
  `instance`, and a `request_id` that matches the `X-Request-ID` header.
- `type` is `<base URI><slug>` where the slug is stable (`refund-exceeds-balance`). Clients
  match on `type`. The base URI is configurable and is an identifier, not a promise of a
  resolvable page.
- Services raise subclasses of `DomainError` that carry status, slug, title and extra members
  (`refundable`, `allowed`, `required_scopes`). One handler renders them. Validation errors,
  `HTTPException` and unmatched routes go through the same renderer.
- Unexpected exceptions are caught in the outermost middleware, logged with a stack trace, and
  answered with a generic 500 problem that exposes no internals.
- The OpenAPI document declares `ProblemDetail` for 4xx responses with the
  `application/problem+json` media type, and the FastAPI default `HTTPValidationError` is gone.
- Use 422 for "understood but not allowed" (business rules), 409 for "conflicts with current
  state" (duplicate, wrong status), 400 only for malformed transport-level input.

## Consequences

- One error shape to document and test.
- `detail` text is written for people (and models) and may change; `type` and the named extra
  members are the contract.
- Extra members make errors self-explanatory (an invalid transition lists the allowed
  statuses) at the cost of a looser schema (`ProblemDetail` allows extra properties).
