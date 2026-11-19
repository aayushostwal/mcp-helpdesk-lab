# ADR 0005: Cursor (keyset) pagination

Status: accepted

## Context

Tickets, comments and audit events grow continuously and are read newest first while new rows
arrive. Offset pagination skips or repeats rows when data changes between pages and gets slower
with depth. Agents that page through results need predictable, small responses.

## Decision

- List endpoints take `limit` (1 to 100, default 25) and an opaque `cursor`, and return
  `{"items": [...], "next_cursor": "..."|null}`.
- The cursor encodes the last row's sort value and id. The next page selects rows strictly after
  that pair in the same order (`(sort, id) < (value, id)` for descending lists). The id is the
  tie-breaker, so equal timestamps never split or repeat rows.
- The query fetches `limit + 1` rows to know whether another page exists; no `COUNT(*)`.
- Malformed cursors return 400 `invalid-cursor`.
- Cursors are base64url JSON. They are opaque but not signed; tampering can only produce a valid
  position or an error, never access to data the filters would not already return.
- Short reference lists (teams, tags, SLA policies, webhooks, API keys) return plain arrays.

## Consequences

- Stable under concurrent inserts, and constant cost per page with an index on the sort column.
- No total count and no jumping to page N. Clients that need totals use the reports endpoints.
- Changing a filter between pages is the caller's mistake and gives arbitrary results; the
  documentation says to keep filters fixed.
