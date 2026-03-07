"""Security tests for conversation isolation in ConversationService.

Tests verify that UUID validation and user_id matching prevent unauthorized
access to conversations belonging to other users.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from src.config.settings import Settings
from src.services.conversation import ConversationService


@pytest.fixture
def settings():
    return Settings(
        cosmos_endpoint="https://fake-account.documents.azure.com:443/",
        cosmos_database_name="testdb",
        cosmos_container_name="testcontainer",
    )


@pytest.fixture
def mock_container():
    return AsyncMock()


@pytest.fixture
def service(settings, mock_container):
    svc = ConversationService(settings)
    svc._container = mock_container
    svc._client = AsyncMock()
    return svc


class TestConversationIsolation:
    """ConversationService must isolate conversations by UUID format and user_id."""

    @pytest.mark.asyncio
    async def test_non_uuid_conversation_id_returns_none(self, service, mock_container):
        """Path-traversal and non-UUID conversation IDs must be rejected without
        hitting Cosmos DB at all."""
        result = await service.get_conversation("../../etc/passwd", "user-1")

        assert result is None
        # The UUID guard must short-circuit before any DB call
        mock_container.read_item.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_user_id_mismatch_returns_none(self, service, mock_container):
        """A conversation that exists but belongs to a different user must return None.

        This is the defense-in-depth check that verifies user_id even after
        the partition key lookup succeeds.
        """
        now = datetime.now(UTC).isoformat()
        valid_id = "12345678-1234-1234-1234-123456789abc"
        # Cosmos returns a document owned by "other-user"
        mock_container.read_item.return_value = {
            "id": valid_id,
            "user_id": "other-user",
            "created_at": now,
            "updated_at": now,
            "messages": [],
            "metadata": {"message_count": 0, "feedback": []},
        }

        result = await service.get_conversation(valid_id, "requesting-user")

        assert result is None
        # Cosmos was queried (UUID passed), but user_id mismatch returns None
        mock_container.read_item.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_valid_uuid_with_correct_user_passes(self, service, mock_container):
        """A valid UUID with a matching user_id must return the conversation document."""
        now = datetime.now(UTC).isoformat()
        valid_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        mock_container.read_item.return_value = {
            "id": valid_id,
            "user_id": "user-1",
            "created_at": now,
            "updated_at": now,
            "messages": [],
            "metadata": {"message_count": 0, "feedback": []},
        }

        result = await service.get_conversation(valid_id, "user-1")

        assert result is not None
        assert result.id == valid_id
        assert result.user_id == "user-1"
        mock_container.read_item.assert_awaited_once_with(
            item=valid_id, partition_key="user-1"
        )
