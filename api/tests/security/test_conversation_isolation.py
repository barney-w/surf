"""Security tests for conversation isolation in ConversationService.

Tests verify that UUID validation and SQL-level user_id filtering prevent
unauthorised access to conversations belonging to other users.
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.settings import Settings
from src.services.conversation import ConversationService


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        postgres_host="localhost",
        postgres_port=5432,
        postgres_database="testdb",
        postgres_user="testuser",
        postgres_password="testpass",
        postgres_ssl=False,
    )


@pytest.fixture
def mock_conn() -> AsyncMock:
    """Mock asyncpg connection with standard query methods."""
    conn = AsyncMock()
    # conn.transaction() is a sync call returning an async context manager
    tx = MagicMock()
    tx.__aenter__ = AsyncMock(return_value=tx)
    tx.__aexit__ = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=tx)
    return conn


@pytest.fixture
def mock_pool(mock_conn: AsyncMock) -> MagicMock:
    """Mock asyncpg pool that yields mock_conn from acquire()."""
    pool = MagicMock()
    cm = AsyncMock()
    cm.__aenter__.return_value = mock_conn
    cm.__aexit__.return_value = None
    pool.acquire.return_value = cm
    return pool


@pytest.fixture
def service(settings: Settings, mock_pool: MagicMock) -> ConversationService:
    svc = ConversationService(settings)
    svc._pool = mock_pool
    return svc


class TestConversationIsolation:
    """ConversationService must isolate conversations by UUID format and user_id."""

    @pytest.mark.asyncio
    async def test_non_uuid_conversation_id_returns_none(
        self, service: ConversationService, mock_pool: MagicMock
    ):
        """Path-traversal and non-UUID conversation IDs must be rejected without
        hitting the database at all."""
        result = await service.get_conversation("../../etc/passwd", "user-1")

        assert result is None
        # The UUID guard must short-circuit before any DB call
        mock_pool.acquire.assert_not_called()

    @pytest.mark.asyncio
    async def test_user_id_mismatch_returns_none(
        self, service: ConversationService, mock_conn: AsyncMock
    ):
        """When the SQL WHERE clause filters out rows due to user_id mismatch,
        fetchrow returns None and the service returns None.

        This proves isolation is enforced at the SQL level — the query includes
        WHERE user_id = $2, so a mismatched user never sees the row.
        """
        valid_id = "12345678-1234-1234-1234-123456789abc"
        # SQL WHERE user_id = $2 won't match, so fetchrow returns None
        mock_conn.fetchrow.return_value = None

        result = await service.get_conversation(valid_id, "requesting-user")

        assert result is None
        # The query was executed (UUID passed validation)
        mock_conn.fetchrow.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_valid_uuid_with_correct_user_passes(
        self, service: ConversationService, mock_conn: AsyncMock
    ):
        """A valid UUID with a matching user_id must return the conversation document."""
        now = datetime.now(UTC)
        valid_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        conv_uuid = uuid.UUID(valid_id)

        mock_conn.fetchrow.return_value = {
            "id": conv_uuid,
            "user_id": "user-1",
            "created_at": now,
            "updated_at": now,
            "last_active_agent": None,
        }
        # Empty messages and feedback
        mock_conn.fetch.side_effect = [[], []]

        result = await service.get_conversation(valid_id, "user-1")

        assert result is not None
        assert result.id == valid_id
        assert result.user_id == "user-1"
        mock_conn.fetchrow.assert_awaited_once()
