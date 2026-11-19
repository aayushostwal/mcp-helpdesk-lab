# ADR 0003: Scoped, hashed API keys

Status: accepted

## Context

Callers are services and AI agents, not browsers. Credentials must be easy to issue and revoke,
safe to store, and limited to what each caller needs. One rule (refund approval) depends on who
the caller acts as.

## Decision

- Authenticate with `Authorization: Bearer hd_<prefix>_<secret>`.
- Generate the secret randomly (256 bits). Store only the prefix, a random per-key salt and
  `sha256(salt + secret)`. Compare in constant time. Show the token once at creation.
- Look up by the indexed prefix, so a request costs one query and one hash.
- Authorise with **scopes** (`tickets:read`, `refunds:write`, ..., `admin`). Routes declare the
  scopes they need through FastAPI `Security(...)`, which also puts them into `openapi.json`.
  Missing scopes are 403 `insufficient-scope`, listing what was required.
- Optionally link a key to an **agent**. Business rules that depend on a person's role use the
  linked agent's role, not a scope: approving or rejecting a refund requires a key linked to an
  active agent with role `lead`.
- Keys can expire and be revoked. `last_used_at` is refreshed at most every five minutes to
  avoid a write on every read.
- Bootstrap from the CLI (`helpdesk create-api-key`), since an empty database has no key to
  authenticate the first call.

## Consequences

- A fast hash is fine for high-entropy random keys; it would be wrong for passwords, which is why
  this scheme is not used for user logins (there are none).
- Keys are bearer credentials: TLS is required in front of the service.
- No OAuth, no per-user sessions. If end-user identity is needed later, add it in front or
  alongside; the scope model can stay.
- Coarse scopes (read and write per domain) keep the model small; finer control would need
  resource-level rules.
