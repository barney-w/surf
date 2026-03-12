"""Shared fixtures for integration tests that require a live database."""

import os
from urllib.parse import urlparse

import asyncpg
import pytest
import pytest_asyncio

from src.config.settings import Settings
from src.services.conversation import ConversationService


@pytest.fixture(scope="session")
def db_url():
    """Require DATABASE_URL for integration tests."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skipping DB integration tests")
    return url


@pytest_asyncio.fixture
async def db_pool(db_url):
    """Create a temporary asyncpg connection pool from DATABASE_URL."""
    pool = await asyncpg.create_pool(db_url)
    yield pool
    await pool.close()


@pytest.fixture
def db_settings(db_url):
    """Build a Settings object whose Postgres fields match DATABASE_URL."""
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
async def conversation_service(db_settings):
    """Provide a fully initialised ConversationService backed by the test database."""
    svc = ConversationService(db_settings)
    await svc.initialize()
    yield svc
    await svc.close()
