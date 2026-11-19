# ADR 0002: SQLite by default, PostgreSQL in production

Status: accepted

## Context

The service should start with zero setup for development, tests and the benchmark (which runs
hundreds of short-lived API processes, each with a fresh copy of a seeded database). It should
also behave correctly on a real server database.

## Decision

- Use SQLAlchemy 2.0 async. Default driver: `aiosqlite` with a file database. PostgreSQL via
  `asyncpg` when `HELPDESK_DATABASE_URL` says so (`pip install .[postgres]`).
- Write portable code only: standard SQL types, JSON stored with SQLAlchemy's generic `JSON`,
  no database-specific functions, `ILIKE` through SQLAlchemy, integer cents for money.
- Timestamps are always timezone-aware UTC. A `TypeDecorator` re-attaches UTC on SQLite, which
  stores naive values.
- Ids are generated from a `sequences` table with `UPDATE ... RETURNING`. On PostgreSQL the
  row lock serialises concurrent allocations. On SQLite the single writer lock does.
- Run the integration suite on both: SQLite locally and in CI, PostgreSQL 16 in CI through a
  service container (`TEST_DATABASE_URL`).
- Foreign keys are enforced on SQLite (`PRAGMA foreign_keys=ON`) so tests catch the same
  mistakes PostgreSQL would.

## Consequences

- No `LISTEN/NOTIFY`, array columns, partial indexes or `JSONB` operators. Acceptable here.
- SQLite allows one writer at a time. That is fine for a single process; use PostgreSQL when
  several processes write.
- Free-text search is a `LIKE` scan with in-process ranking, not full-text search. It is
  adequate for hundreds of articles.
- Identical logical content is produced on both databases. The seed fixtures are byte-identical;
  the content fingerprint differs between dialects because they format values differently.
