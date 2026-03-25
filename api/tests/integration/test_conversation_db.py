"""Integration tests: conversation persistence against a real PostgreSQL database.

These tests require a running Postgres instance (see docker-compose.test.yml).
They are skipped automatically when the database is unavailable.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

import pytest

from src.models.conversation import FeedbackRecord, MessageRecord

pytestmark = pytest.mark.integration


def _user_id() -> str:
    return f"test-user-{uuid.uuid4()}"


def _message(
    role: Literal["user", "assistant"] = "user",
    content: str = "hello",
    agent: str | None = None,
) -> MessageRecord:
    return MessageRecord(
        id=str(uuid.uuid4()),
        role=role,
        content=content,
        agent=agent,
        timestamp=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# 1. Create and retrieve a conversation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_and_get_conversation(conversation_service):
    """A newly created conversation is retrievable and has correct fields."""
    svc = conversation_service
    user_id = _user_id()

    conv = await svc.create_conversation(user_id)

    assert conv.id is not None
    assert conv.user_id == user_id
    assert conv.messages == []
    assert conv.metadata.message_count == 0
    assert conv.created_at is not None
    assert conv.updated_at is not None

    # Retrieve and verify round-trip
    loaded = await svc.get_conversation(conv.id, user_id)
    assert loaded is not None
    assert loaded.id == conv.id
    assert loaded.user_id == user_id
    assert loaded.metadata.message_count == 0


# ---------------------------------------------------------------------------
# 2. Add messages and verify ordering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_message_and_retrieve(conversation_service):
    """User and assistant messages are persisted in order."""
    svc = conversation_service
    user_id = _user_id()
    conv = await svc.create_conversation(user_id)

    user_msg = _message("user", "What is the refund policy?")
    assistant_msg = _message(
        "assistant", "Our refund policy allows returns within 30 days.", agent="support_agent"
    )

    await svc.add_message(conv.id, user_id, user_msg)
    await svc.add_message(conv.id, user_id, assistant_msg)

    loaded = await svc.get_conversation(conv.id, user_id)
    assert loaded is not None
    assert len(loaded.messages) == 2
    assert loaded.metadata.message_count == 2

    # Verify ordering
    assert loaded.messages[0].role == "user"
    assert loaded.messages[0].content == "What is the refund policy?"
    assert loaded.messages[1].role == "assistant"
    assert loaded.messages[1].content == "Our refund policy allows returns within 30 days."
    assert loaded.messages[1].agent == "support_agent"


# ---------------------------------------------------------------------------
# 3. Conversation isolation between users
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conversation_isolation(conversation_service):
    """User A's conversations are invisible to User B."""
    svc = conversation_service
    user_a = _user_id()
    user_b = _user_id()

    conv = await svc.create_conversation(user_a)
    await svc.add_message(conv.id, user_a, _message("user", "secret message"))

    # User B cannot see User A's conversation
    result = await svc.get_conversation(conv.id, user_b)
    assert result is None

    # User B cannot delete User A's conversation
    deleted = await svc.delete_conversation(conv.id, user_b)
    assert deleted is False

    # User A can still see their own conversation
    own = await svc.get_conversation(conv.id, user_a)
    assert own is not None
    assert len(own.messages) == 1


# ---------------------------------------------------------------------------
# 4. Delete conversation cascades to messages and feedback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_conversation_cascades(conversation_service):
    """Deleting a conversation removes its messages and feedback."""
    svc = conversation_service
    user_id = _user_id()

    conv = await svc.create_conversation(user_id)
    msg = _message("assistant", "Here is your answer.", agent="qa_agent")
    await svc.add_message(conv.id, user_id, msg)

    feedback = FeedbackRecord(message_id=msg.id, rating="positive", comment="Great answer")
    await svc.add_feedback(conv.id, user_id, feedback)

    # Verify data exists
    loaded = await svc.get_conversation(conv.id, user_id)
    assert loaded is not None
    assert len(loaded.messages) == 1
    assert len(loaded.metadata.feedback) == 1

    # Delete
    deleted = await svc.delete_conversation(conv.id, user_id)
    assert deleted is True

    # Verify everything is gone
    gone = await svc.get_conversation(conv.id, user_id)
    assert gone is None

    # Also verify via the pool that no orphan rows remain
    pool = svc._get_pool()
    async with pool.acquire() as conn:
        msg_count = await conn.fetchval(
            "SELECT count(*) FROM messages WHERE conversation_id = $1",
            uuid.UUID(conv.id),
        )
        fb_count = await conn.fetchval(
            "SELECT count(*) FROM feedback WHERE conversation_id = $1",
            uuid.UUID(conv.id),
        )
    assert msg_count == 0
    assert fb_count == 0


# ---------------------------------------------------------------------------
# 5. Cleanup expired conversations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_expired(conversation_service):
    """Conversations older than the TTL are removed by cleanup."""
    svc = conversation_service
    user_id = _user_id()

    # Create a conversation and manually backdate it
    conv = await svc.create_conversation(user_id)
    await svc.add_message(conv.id, user_id, _message("user", "old message"))

    pool = svc._get_pool()
    old_date = datetime.now(UTC) - timedelta(days=100)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE conversations SET updated_at = $1 WHERE id = $2",
            old_date,
            uuid.UUID(conv.id),
        )

    # Create a fresh conversation that should survive cleanup
    fresh_conv = await svc.create_conversation(user_id)

    # Run cleanup with 90-day TTL
    deleted_count = await svc.cleanup_expired_conversations(90)
    assert deleted_count >= 1

    # Old conversation should be gone
    assert await svc.get_conversation(conv.id, user_id) is None

    # Fresh conversation should still exist
    assert await svc.get_conversation(fresh_conv.id, user_id) is not None


# ---------------------------------------------------------------------------
# 6. Feedback persists and is retrievable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feedback_persistence(conversation_service):
    """Feedback is correctly stored and returned with the conversation."""
    svc = conversation_service
    user_id = _user_id()

    conv = await svc.create_conversation(user_id)
    msg = _message("assistant", "Here is some information.", agent="info_agent")
    await svc.add_message(conv.id, user_id, msg)

    feedback = FeedbackRecord(message_id=msg.id, rating="negative", comment="Not helpful")
    await svc.add_feedback(conv.id, user_id, feedback)

    loaded = await svc.get_conversation(conv.id, user_id)
    assert loaded is not None
    assert len(loaded.metadata.feedback) == 1
    assert loaded.metadata.feedback[0].rating == "negative"
    assert loaded.metadata.feedback[0].comment == "Not helpful"
    assert loaded.metadata.feedback[0].message_id == msg.id
