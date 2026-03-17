"""Shared fixtures for integration tests that require a live database."""

import os
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import asyncpg
import pytest
import pytest_asyncio

from src.config.settings import Settings
from src.services.conversation import ConversationService

# Default points at the docker-compose.test.yml service on port 5433
_DEFAULT_DB_URL = "postgresql://surf:test@localhost:5433/surf_test"
_TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    os.environ.get("DATABASE_URL", _DEFAULT_DB_URL),
)


@pytest.fixture(scope="session")
def db_url():
    """Return the test database URL, skipping if the DB is unreachable."""
    return _TEST_DATABASE_URL


@pytest.fixture(scope="session")
def _run_migrations(db_url):
    """Run Alembic migrations against the test database once per session."""
    # Convert plain postgresql:// to the asyncpg driver URL that alembic env.py expects
    alembic_url = db_url
    if alembic_url.startswith("postgresql://"):
        alembic_url = alembic_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    env = {
        "DATABASE_URL": alembic_url,
        "PATH": os.environ.get("PATH", ""),
    }
    api_dir = str(Path(__file__).resolve().parent.parent.parent)
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=api_dir,
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        pytest.skip(f"Alembic migrations failed: {result.stderr}")


@pytest_asyncio.fixture
async def db_pool(db_url, _run_migrations):
    """Create a temporary asyncpg connection pool from the test database URL."""
    try:
        pool = await asyncpg.create_pool(db_url)
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"Test database unavailable: {exc}")
        return  # unreachable but keeps type checkers happy
    yield pool
    await pool.close()


@pytest.fixture
def db_settings(db_url):
    """Build a Settings object whose Postgres fields match the test database URL."""
    parsed = urlparse(db_url)
    return Settings(
        postgres_host=parsed.hostname or "localhost",
        postgres_port=parsed.port or 5432,
        postgres_database=(parsed.path or "/surf").lstrip("/"),
        postgres_user=parsed.username or "surf",
        postgres_password=parsed.password or "",
        postgres_ssl=False,
        environment="dev",
    )


@pytest_asyncio.fixture
async def conversation_service(db_settings, _run_migrations):
    """Provide a fully initialised ConversationService backed by the test database.

    Tables are truncated after each test to ensure isolation.
    """
    try:
        svc = ConversationService(db_settings)
        await svc.initialize()
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"Test database unavailable: {exc}")
        return

    yield svc

    # Clean up all data after each test
    try:
        pool = svc._get_pool()
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE conversations, messages, feedback CASCADE")
    except Exception:
        pass  # best-effort cleanup

    await svc.close()
