# ADR 0006: Idempotency keys implemented as middleware

Status: accepted

## Context

Clients retry after timeouts. A retried `POST /refunds` must not refund twice. AI agents retry
often and without noticing.

## Decision

- Any `POST` may carry `Idempotency-Key`. A pure-ASGI middleware handles it so the behaviour is
  uniform and routes stay unaware.
- A record is keyed by `(sha256(Authorization header), key)` and stores a hash of
  `method + path + query + body`, plus the status, body and content type of the response.
- Same key and same request: replay the stored response with `Idempotent-Replayed: true`.
  Same key, different request: 422 `idempotency-key-reuse`.
- Only 2xx responses are stored, so a request that failed validation or hit a conflict can be
  corrected and retried with the same key.
- Records expire after 24 hours (configurable). An expired record is deleted when its key is
  seen again.
- The unique constraint on `(scope, key)` resolves races between concurrent first requests: the
  loser's insert fails and is ignored.

## Consequences

- Two truly simultaneous requests with the same new key can both run before either stores a
  record. The window is small; closing it needs a "processing" record and request locking, which
  was judged not worth the complexity here. State-changing rules (refund balance) still hold
  because they are checked inside the transaction.
- Storing the response means the first caller's body is replayed to later callers using the same
  `Authorization` header; scoping by the credential prevents cross-caller leaks.
- The middleware buffers the body and the response of idempotent POSTs in memory. Responses are
  small JSON documents.
