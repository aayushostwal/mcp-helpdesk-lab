# Contributing

Thanks for taking a look. This is a reference project, so changes should keep the code boring,
typed and tested.

## Setup

```bash
make install        # uv sync --all-extras (Python 3.12+)
make seed           # optional: local SQLite database with synthetic data
make check          # lint, mypy --strict, tests with coverage gate, OpenAPI drift check
```

You need [uv](https://docs.astral.sh/uv/). Docker is only needed to build the image.

## Ground rules

- **Layering.** Routers (`api/routers`) call services, services call repositories, repositories
  own the SQL. Routers never import ORM models; services never build queries. See
  `docs/adr/0001-layered-architecture.md`.
- **Business rules live in services** and are covered by tests. Pure rules (status machine,
  refund policy, SLA maths) are plain functions so they can be unit tested without a database.
- **Every write is audited.** A new write operation must call `self.audit(...)` before it commits.
- **Every operation needs** a stable `operation_id` (`<domain>_<verb>`), a one-line `summary` and
  a `description` written for an AI reader: what it does, when to use it, key parameters.
  `tests/integration/test_openapi.py` enforces this.
- **Run `make openapi`** after changing routes or schemas and commit `openapi.json`. CI fails if
  it drifts.
- **Migrations.** Change models, then
  `uv run alembic revision --autogenerate -m "message"`, review the file, and make sure
  `tests/integration/test_cli_and_migrations.py` still passes.
- **Style.** `ruff` (lint and format) and `mypy --strict` must be clean. No `type: ignore`
  without a reason.
- **Tests.** Coverage must stay at or above 90%. Integration tests run on SQLite by default;
  set `TEST_DATABASE_URL=postgresql+asyncpg://...` to run them on PostgreSQL.

## Commits and pull requests

- Conventional commit messages: `feat(api): ...`, `fix(seed): ...`, `docs: ...`, `test: ...`.
- Keep pull requests focused. Describe what changed and why, and note any behaviour change.
- Update `CHANGELOG.md` for user-visible changes and the relevant page under `docs/`.
