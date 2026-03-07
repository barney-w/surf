"""Tests for ConversationService with mocked Cosmos DB client."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from azure.cosmos.exceptions import CosmosResourceNotFoundError

from src.config.settings import Settings
from src.models.conversation import FeedbackRecord, MessageRecord
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


class TestCreateConversation:
    @pytest.mark.asyncio
    async def test_creates_valid_uuid_and_defaults(self, service, mock_container):
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

        mock_container.create_item.assert_awaited_once()
        body = mock_container.create_item.call_args.kwargs["body"]
        assert body["user_id"] == user_id


class TestAddMessage:
    @pytest.mark.asyncio
    async def test_appends_message_via_patch(self, service, mock_container):
        now = datetime.now(UTC)
        msg = MessageRecord(id="msg-1", role="user", content="Hello", timestamp=now)

        await service.add_message("conv-1", "user-1", msg)

        mock_container.patch_item.assert_awaited_once()
        call_kwargs = mock_container.patch_item.call_args.kwargs
        assert call_kwargs["item"] == "conv-1"
        assert call_kwargs["partition_key"] == "user-1"

        ops = call_kwargs["patch_operations"]
        add_op = next(o for o in ops if o["path"] == "/messages/-")
        assert add_op["op"] == "add"
        assert add_op["value"]["id"] == "msg-1"

        incr_op = next(o for o in ops if o["path"] == "/metadata/message_count")
        assert incr_op["op"] == "incr"
        assert incr_op["value"] == 1


class TestGetConversation:
    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self, service, mock_container):
        mock_container.read_item.side_effect = CosmosResourceNotFoundError()
        valid_id = "00000000-0000-0000-0000-000000000000"

        result = await service.get_conversation(valid_id, "user-1")

        assert result is None
        mock_container.read_item.assert_awaited_once_with(
            item=valid_id, partition_key="user-1"
        )

    @pytest.mark.asyncio
    async def test_returns_document_when_found(self, service, mock_container):
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

    @pytest.mark.asyncio
    async def test_get_conversation_rejects_non_uuid_id(self, service, mock_container):
        result = await service.get_conversation("not-a-uuid", "user-1")

        assert result is None
        mock_container.read_item.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_get_conversation_rejects_user_id_mismatch(self, service, mock_container):
        now = datetime.now(UTC).isoformat()
        valid_id = "12345678-1234-1234-1234-123456789abc"
        mock_container.read_item.return_value = {
            "id": valid_id,
            "user_id": "other-user",
            "created_at": now,
            "updated_at": now,
            "messages": [],
            "metadata": {"message_count": 0, "feedback": []},
        }

        result = await service.get_conversation(valid_id, "user-1")

        assert result is None
        mock_container.read_item.assert_awaited_once()


class TestDeleteConversation:
    @pytest.mark.asyncio
    async def test_returns_false_when_not_found(self, service, mock_container):
        mock_container.delete_item.side_effect = CosmosResourceNotFoundError()

        result = await service.delete_conversation("no-such-id", "user-1")

        assert result is False
        mock_container.delete_item.assert_awaited_once_with(
            item="no-such-id", partition_key="user-1"
        )

    @pytest.mark.asyncio
    async def test_returns_true_on_success(self, service, mock_container):
        result = await service.delete_conversation("conv-1", "user-1")

        assert result is True


class TestAddFeedback:
    @pytest.mark.asyncio
    async def test_appends_feedback_via_patch(self, service, mock_container):
        feedback = FeedbackRecord(
            message_id="msg-1", rating="positive", comment="Great answer"
        )

        await service.add_feedback("conv-1", "user-1", feedback)

        mock_container.patch_item.assert_awaited_once()
        call_kwargs = mock_container.patch_item.call_args.kwargs
        assert call_kwargs["item"] == "conv-1"
        assert call_kwargs["partition_key"] == "user-1"

        ops = call_kwargs["patch_operations"]
        add_op = next(o for o in ops if o["path"] == "/metadata/feedback/-")
        assert add_op["op"] == "add"
        assert add_op["value"]["message_id"] == "msg-1"
        assert add_op["value"]["rating"] == "positive"
