"""CLI commands and Alembic migrations (sync tests: Alembic runs its own event loop)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect
from typer.testing import CliRunner

from helpdesk.cli import alembic_config, app
from helpdesk.config import Settings, get_settings
from helpdesk.models import Base

runner = CliRunner()


@pytest.fixture
def cli_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    db = tmp_path / "cli.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    get_settings.cache_clear()
    yield db
    get_settings.cache_clear()


def test_migrations_create_exactly_the_model_schema(tmp_path: Path) -> None:
    db = tmp_path / "m.db"
    settings = Settings(database_url=f"sqlite+aiosqlite:///{db}")
    command.upgrade(alembic_config(settings), "head")
    engine = create_engine(f"sqlite:///{db}")
    names = set(inspect(engine).get_table_names())
    assert {
        "tickets",
        "customers",
        "refunds",
        "audit_events",
        "api_keys",
        "alembic_version",
    } <= names
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == [], f"models and migrations have drifted: {diff}"
    command.downgrade(alembic_config(settings), "base")
    assert set(inspect(engine).get_table_names()) == {"alembic_version"}
    engine.dispose()


def test_migrate_seed_and_create_key_flow(cli_db: Path, tmp_path: Path) -> None:
    fixtures = tmp_path / "fx" / "fixtures.json"
    assert runner.invoke(app, ["migrate"]).exit_code == 0
    seeded = runner.invoke(app, ["seed", "--fixtures", str(fixtures)])
    assert seeded.exit_code == 0, seeded.output
    assert "fingerprint" in seeded.output and "HELPDESK_FROZEN_TIME=2026-06-01" in seeded.output
    data = json.loads(fixtures.read_text())
    assert data["counts"]["customers"] == 500 and data["seed"] == 42
    again = runner.invoke(app, ["seed", "--fixtures", str(fixtures)])
    assert again.exit_code != 0 and "--reset" in again.output
    reset = runner.invoke(
        app,
        [
            "seed",
            "--reset",
            "--seed",
            "7",
            "--as-of",
            "2026-01-01T00:00:00",
            "--fixtures",
            str(fixtures),
        ],
    )
    assert reset.exit_code == 0, reset.output
    assert json.loads(fixtures.read_text())["as_of"].startswith("2026-01-01")
    key = runner.invoke(
        app,
        [
            "create-api-key",
            "--name",
            "ci",
            "--scopes",
            "tickets:read,reports:read",
            "--agent",
            "A-0002",
            "--expires-days",
            "5",
        ],
    )
    assert key.exit_code == 0 and key.output.strip().startswith("hd_")
    bad = runner.invoke(app, ["create-api-key", "--name", "x", "--scopes", "nope"])
    assert bad.exit_code != 0
    no_agent = runner.invoke(app, ["create-api-key", "--name", "x", "--agent", "A-9999"])
    assert no_agent.exit_code != 0


def test_seed_without_migration_explains_itself(cli_db: Path, tmp_path: Path) -> None:
    result = runner.invoke(app, ["seed", "--fixtures", str(tmp_path / "f.json")])
    assert result.exit_code != 0 and "helpdesk migrate" in result.output


def test_serve_passes_settings_to_uvicorn(monkeypatch: pytest.MonkeyPatch, cli_db: Path) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr("helpdesk.cli.uvicorn.run", lambda *a, **k: captured.update(args=a, **k))
    assert runner.invoke(app, ["serve", "--port", "9123", "--host", "0.0.0.0"]).exit_code == 0
    assert captured["args"] == ("helpdesk.api.app:create_app",)
    assert (
        captured["factory"] is True and captured["port"] == 9123 and captured["host"] == "0.0.0.0"
    )
    captured.clear()
    runner.invoke(app, ["serve"])
    assert captured["port"] == 8000 and captured["host"] == "127.0.0.1"
