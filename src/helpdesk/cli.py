"""Command line interface: ``helpdesk serve | migrate | seed | create-api-key``."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import typer
import uvicorn
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import OperationalError, ProgrammingError

from helpdesk.clock import SystemClock
from helpdesk.config import Settings, get_settings
from helpdesk.db import make_engine, make_session_factory
from helpdesk.seeding import (
    DEFAULT_AS_OF,
    DEFAULT_SEED,
    clear_database,
    fingerprint,
    is_empty,
    seed_database,
)
from helpdesk.services.admin import ApiKeyService
from helpdesk.services.base import Context

app = typer.Typer(help="Helpdesk API service commands.", no_args_is_help=True, add_completion=False)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def alembic_config(settings: Settings) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
    return config


@app.command()
def serve(
    host: Annotated[str | None, typer.Option(help="Bind address (default: HELPDESK_HOST).")] = None,
    port: Annotated[int | None, typer.Option(help="Port (default: HELPDESK_PORT).")] = None,
    reload: Annotated[bool, typer.Option(help="Auto-reload on code changes (dev only).")] = False,
) -> None:
    """Run the API with uvicorn."""
    settings = get_settings()
    uvicorn.run(
        "helpdesk.api.app:create_app",
        factory=True,
        host=host or settings.host,
        port=port or settings.port,
        reload=reload,
        log_config=None,
        access_log=False,
    )


@app.command()
def migrate(
    revision: Annotated[str, typer.Option(help="Target revision.")] = "head",
) -> None:
    """Apply database migrations (Alembic upgrade)."""
    settings = get_settings()
    command.upgrade(alembic_config(settings), revision)
    typer.echo(f"Database is at revision '{revision}'.")


@app.command()
def seed(
    seed_value: Annotated[int, typer.Option("--seed", help="Random seed.")] = DEFAULT_SEED,
    as_of: Annotated[
        str | None, typer.Option(help="Reference 'now' for generated dates (ISO 8601).")
    ] = None,
    reset: Annotated[bool, typer.Option(help="Delete all existing rows first.")] = False,
    fixtures: Annotated[
        Path | None, typer.Option(help="Where to write fixtures JSON for the benchmark.")
    ] = Path("bench/tasks/fixtures.json"),
) -> None:
    """Fill the database with deterministic synthetic data and write fixtures.json."""
    reference = datetime.fromisoformat(as_of) if as_of else DEFAULT_AS_OF
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    data, digest = asyncio.run(_seed(reset, seed_value, reference))
    if fixtures is not None:
        fixtures.parent.mkdir(parents=True, exist_ok=True)
        fixtures.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        typer.echo(f"Wrote {fixtures}")
    typer.echo(f"Seeded {data['counts']['tickets']} tickets. Content fingerprint: {digest}")
    typer.echo(f"Run the API with HELPDESK_FROZEN_TIME={reference.isoformat()} to match the data.")


async def _seed(reset: bool, seed_value: int, as_of: datetime) -> tuple[dict[str, Any], str]:
    engine = make_engine(get_settings())
    factory = make_session_factory(engine)
    try:
        async with factory() as session:
            try:
                if reset:
                    await clear_database(session)
                elif not await is_empty(session):
                    raise typer.BadParameter(
                        "Database already has data. Use --reset to replace it."
                    )
            except (OperationalError, ProgrammingError) as exc:
                raise typer.BadParameter(
                    "Tables are missing. Run `helpdesk migrate` first."
                ) from exc
            fixtures = await seed_database(session, seed_value, as_of)
            return fixtures, await fingerprint(session)
    finally:
        await engine.dispose()


@app.command("create-api-key")
def create_api_key(
    name: Annotated[str, typer.Option(help="Label for the key.")],
    scopes: Annotated[str, typer.Option(help="Comma-separated scopes, e.g. 'admin'.")] = "admin",
    agent: Annotated[
        str | None, typer.Option(help="Link to an agent id (a lead can approve refunds).")
    ] = None,
    expires_days: Annotated[int | None, typer.Option(help="Expire after N days.")] = None,
) -> None:
    """Create an API key and print it once. Bootstraps access to a fresh deployment."""
    token = asyncio.run(
        _create_key(name, [s.strip() for s in scopes.split(",") if s.strip()], agent, expires_days)
    )
    typer.echo(token)


async def _create_key(
    name: str, scopes: list[str], agent: str | None, expires_days: int | None
) -> str:
    settings = get_settings()
    engine = make_engine(settings)
    factory = make_session_factory(engine)
    try:
        async with factory() as session:
            ctx = Context(session=session, clock=SystemClock(), settings=settings)
            _, token = await ApiKeyService(ctx).create(name, scopes, agent, expires_days)
            return token
    except Exception as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        await engine.dispose()


if __name__ == "__main__":
    app()
