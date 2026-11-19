"""Shared fixtures for the benchmark tests. None of them calls a model."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from bench.harness.isolation import RunEnv, ensure_seed_db, run_env

DATA = Path(__file__).parent / "data"


@pytest.fixture(scope="session")
def seed_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A pristine seeded database, built once for the whole test session."""
    return ensure_seed_db(tmp_path_factory.mktemp("bench-cache"))


@pytest.fixture
def env(seed_db: Path) -> Iterator[RunEnv]:
    """A live API on a fresh database copy (no MCP server)."""
    with run_env(None, seed_db=seed_db) as run:
        yield run


def stream_lines(name: str) -> list[str]:
    return (DATA / name).read_text().splitlines()
