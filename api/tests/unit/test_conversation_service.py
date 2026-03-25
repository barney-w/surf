"""Tests for ConversationService with mocked PostgreSQL pool."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.settings import Settings
from src.models.conversation import ConversationSummary, FeedbackRecord, MessageRecord
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
    # Make pool.acquire() work as an async context manager
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


class TestCreateConversation:
    @pytest.mark.asyncio
    async def test_creates_valid_uuid_and_defaults(
        self,
        service: ConversationService,
        mock_conn: AsyncMock,
    ):
        user_id = "user-1"
        doc = await service.create_conversation(user_id)

        # ID is a valid UUID
        uuid.UUID(doc.id)

        assert doc.user_id == user_id
        assert doc.messages == []
        assert doc.metadata.message_count == 0
        assert doc.metadata.feedback == []
        assert doc.created_at.tzinfo is not None
        assert doc.updated_at.tzinfo is not None

        # Verify INSERT was called
        mock_conn.execute.assert_awaited_once()
        call_args = mock_conn.execute.call_args
        assert "INSERT INTO conversations" in call_args[0][0]
        assert call_args[0][2] == user_id  # $2 = user_id


class TestAddMessage:
    @pytest.mark.asyncio
    async def test_inserts_message_with_correct_ordinal(
        self,
        service: ConversationService,
        mock_conn: AsyncMock,
    ):
        now = datetime.now(UTC)
        msg = MessageRecord(id="msg-1", role="user", content="Hello", timestamp=now)
        conv_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

        # First fetchval: ownership check returns matching user_id
        # Second fetchval: max ordinal returns 0
        mock_conn.fetchval.side_effect = ["user-1", 0]

        await service.add_message(conv_id, "user-1", msg)

        # Verify fetchval was called twice (ownership + ordinal)
        assert mock_conn.fetchval.await_count == 2

        # Verify execute was called twice (INSERT message + UPDATE updated_at)
        assert mock_conn.execute.await_count == 2

        # Check the INSERT call
        insert_call = mock_conn.execute.call_args_list[0]
        assert "INSERT INTO messages" in insert_call[0][0]
        assert insert_call[0][9] == 1  # ordinal = max(0) + 1

        # Check the UPDATE call
        update_call = mock_conn.execute.call_args_list[1]
        assert "UPDATE conversations SET updated_at" in update_call[0][0]


class TestGetConversation:
    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(
        self,
        service: ConversationService,
        mock_conn: AsyncMock,
    ):
        valid_id = "00000000-0000-0000-0000-000000000000"
        mock_conn.fetchrow.return_value = None

        result = await service.get_conversation(valid_id, "user-1")

        assert result is None
        mock_conn.fetchrow.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_document_when_found(
        self,
        service: ConversationService,
        mock_conn: AsyncMock,
    ):
        now = datetime.now(UTC)
        valid_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        conv_uuid = uuid.UUID(valid_id)

        # Mock fetchrow for conversation
        mock_conn.fetchrow.return_value = {
            "id": conv_uuid,
            "user_id": "user-1",
            "created_at": now,
            "updated_at": now,
            "last_active_agent": None,
        }

        # Mock fetch: first call = messages, second call = feedback
        mock_conn.fetch.side_effect = [
            [
                {
                    "id": "msg-1",
                    "role": "user",
                    "content": "Hello",
                    "agent": None,
                    "response": None,
                    "attachments": "[]",
                    "timestamp": now,
                    "ordinal": 1,
                },
            ],
            [
                {
                    "message_id": "msg-1",
                    "rating": "positive",
                    "comment": "Great answer",
                },
            ],
        ]

        result = await service.get_conversation(valid_id, "user-1")

        assert result is not None
        assert result.id == valid_id
        assert result.user_id == "user-1"
        assert len(result.messages) == 1
        assert result.messages[0].id == "msg-1"
        assert result.messages[0].content == "Hello"
        assert len(result.metadata.feedback) == 1
        assert result.metadata.feedback[0].rating == "positive"
        assert result.metadata.message_count == 1

    @pytest.mark.asyncio
    async def test_rejects_non_uuid_id(
        self,
        service: ConversationService,
        mock_pool: MagicMock,
    ):
        result = await service.get_conversation("not-a-uuid", "user-1")

        assert result is None
        # UUID guard must short-circuit before any DB call
        mock_pool.acquire.assert_not_called()


class TestDeleteConversation:
    @pytest.mark.asyncio
    async def test_returns_false_when_not_found(
        self,
        service: ConversationService,
        mock_conn: AsyncMock,
    ):
        valid_id = "00000000-0000-0000-0000-000000000000"
        mock_conn.execute.return_value = "DELETE 0"

        result = await service.delete_conversation(valid_id, "user-1")

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_on_success(
        self,
        service: ConversationService,
        mock_conn: AsyncMock,
    ):
        valid_id = "00000000-0000-0000-0000-000000000000"
        mock_conn.execute.return_value = "DELETE 1"

        result = await service.delete_conversation(valid_id, "user-1")

        assert result is True

    @pytest.mark.asyncio
    async def test_rejects_non_uuid_id(
        self,
        service: ConversationService,
        mock_pool: MagicMock,
    ):
        result = await service.delete_conversation("not-a-uuid", "user-1")

        assert result is False
        mock_pool.acquire.assert_not_called()


class TestAddFeedback:
    @pytest.mark.asyncio
    async def test_inserts_feedback_after_ownership_check(
        self,
        service: ConversationService,
        mock_conn: AsyncMock,
    ):
        conv_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        feedback = FeedbackRecord(message_id="msg-1", rating="positive", comment="Great answer")

        # fetchval returns matching user_id for ownership check
        mock_conn.fetchval.return_value = "user-1"

        await service.add_feedback(conv_id, "user-1", feedback)

        # Verify ownership check
        mock_conn.fetchval.assert_awaited_once()
        assert "SELECT user_id FROM conversations" in mock_conn.fetchval.call_args[0][0]

        # Verify INSERT into feedback
        mock_conn.execute.assert_awaited_once()
        insert_call = mock_conn.execute.call_args
        assert "INSERT INTO feedback" in insert_call[0][0]
        assert insert_call[0][2] == "msg-1"  # message_id
        assert insert_call[0][3] == "positive"  # rating


class TestUpdateLastActiveAgent:
    @pytest.mark.asyncio
    async def test_updates_agent_and_timestamp(
        self,
        service: ConversationService,
        mock_conn: AsyncMock,
    ):
        conv_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        mock_conn.execute.return_value = "UPDATE 1"

        await service.update_last_active_agent(conv_id, "user-1", "weather-agent")

        mock_conn.execute.assert_awaited_once()
        call_args = mock_conn.execute.call_args
        assert "UPDATE conversations SET last_active_agent" in call_args[0][0]
        assert call_args[0][1] == "weather-agent"  # $1 = agent_name
        assert call_args[0][4] == "user-1"  # $4 = user_id

    @pytest.mark.asyncio
    async def test_raises_on_not_found(
        self,
        service: ConversationService,
        mock_conn: AsyncMock,
    ):
        conv_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        mock_conn.execute.return_value = "UPDATE 0"

        with pytest.raises(ValueError, match="not found or access denied"):
            await service.update_last_active_agent(conv_id, "user-1", "weather-agent")


class TestListConversations:
    @pytest.mark.asyncio
    async def test_returns_summaries_ordered_by_updated_at(
        self,
        service: ConversationService,
        mock_conn: AsyncMock,
    ):
        now = datetime.now(UTC)
        conv_id = uuid.uuid4()

        mock_conn.fetch.return_value = [
            {
                "id": conv_id,
                "updated_at": now,
                "last_active_agent": "weather-agent",
                "first_user_message": "What is the weather today?",
                "last_message": "It is sunny and warm.",
                "message_count": 4,
            },
        ]

        result = await service.list_conversations("user-1", limit=20, offset=0)

        assert len(result) == 1
        assert isinstance(result[0], ConversationSummary)
        assert result[0].id == str(conv_id)
        assert result[0].title == "What is the weather today?"
        assert result[0].last_message_preview == "It is sunny and warm."
        assert result[0].last_active_agent == "weather-agent"
        assert result[0].message_count == 4
        assert result[0].updated_at == now

        # Verify query includes ORDER BY updated_at DESC
        call_args = mock_conn.fetch.call_args
        assert "ORDER BY c.updated_at DESC" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_conversations(
        self,
        service: ConversationService,
        mock_conn: AsyncMock,
    ):
        mock_conn.fetch.return_value = []

        result = await service.list_conversations("user-no-convos")

        assert result == []

    @pytest.mark.asyncio
    async def test_pagination_params_passed_to_query(
        self,
        service: ConversationService,
        mock_conn: AsyncMock,
    ):
        mock_conn.fetch.return_value = []

        await service.list_conversations("user-1", limit=10, offset=5)

        call_args = mock_conn.fetch.call_args
        assert call_args[0][1] == "user-1"  # $1 = user_id
        assert call_args[0][2] == 10  # $2 = limit
        assert call_args[0][3] == 5  # $3 = offset

    @pytest.mark.asyncio
    async def test_title_defaults_to_new_conversation_when_no_user_message(
        self,
        service: ConversationService,
        mock_conn: AsyncMock,
    ):
        now = datetime.now(UTC)
        conv_id = uuid.uuid4()

        mock_conn.fetch.return_value = [
            {
                "id": conv_id,
                "updated_at": now,
                "last_active_agent": None,
                "first_user_message": None,
                "last_message": None,
                "message_count": 0,
            },
        ]

        result = await service.list_conversations("user-1")

        assert result[0].title == "New conversation"
        assert result[0].last_message_preview is None

    @pytest.mark.asyncio
    async def test_title_truncated_to_80_chars(
        self,
        service: ConversationService,
        mock_conn: AsyncMock,
    ):
        now = datetime.now(UTC)
        conv_id = uuid.uuid4()
        long_message = "A" * 120

        mock_conn.fetch.return_value = [
            {
                "id": conv_id,
                "updated_at": now,
                "last_active_agent": None,
                "first_user_message": long_message,
                "last_message": "short",
                "message_count": 1,
            },
        ]

        result = await service.list_conversations("user-1")

        assert len(result[0].title) == 80
        assert result[0].title == "A" * 80

    @pytest.mark.asyncio
    async def test_last_message_preview_truncated_to_120_chars(
        self,
        service: ConversationService,
        mock_conn: AsyncMock,
    ):
        now = datetime.now(UTC)
        conv_id = uuid.uuid4()
        long_preview = "B" * 200

        mock_conn.fetch.return_value = [
            {
                "id": conv_id,
                "updated_at": now,
                "last_active_agent": None,
                "first_user_message": "Hello",
                "last_message": long_preview,
                "message_count": 2,
            },
        ]

        result = await service.list_conversations("user-1")

        assert result[0].last_message_preview is not None
        assert len(result[0].last_message_preview) == 120
        assert result[0].last_message_preview == "B" * 120

    @pytest.mark.asyncio
    async def test_user_isolation_query_filters_by_user_id(
        self,
        service: ConversationService,
        mock_conn: AsyncMock,
    ):
        """User A's query only passes user A's ID — the WHERE clause ensures isolation."""
        mock_conn.fetch.return_value = []

        await service.list_conversations("user-a")

        call_args = mock_conn.fetch.call_args
        query = call_args[0][0]
        # Query must filter by user_id
        assert "WHERE c.user_id = $1" in query
        # The bound parameter must be user-a
        assert call_args[0][1] == "user-a"


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_returns_true_when_healthy(
        self,
        service: ConversationService,
        mock_conn: AsyncMock,
    ):
        mock_conn.fetchval.return_value = 1

        result = await service.health_check()

        assert result is True
        mock_conn.fetchval.assert_awaited_once_with("SELECT 1")

    @pytest.mark.asyncio
    async def test_returns_false_when_unhealthy(
        self,
        service: ConversationService,
        mock_conn: AsyncMock,
    ):
        mock_conn.fetchval.side_effect = Exception("Connection refused")

        result = await service.health_check()

        assert result is False
