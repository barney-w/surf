"""Integration test: full conversation lifecycle against a real PostgreSQL database."""

import uuid
from datetime import UTC, datetime

import pytest

from src.models.conversation import FeedbackRecord, MessageRecord


@pytest.mark.asyncio
async def test_full_conversation_lifecycle(conversation_service):
    """Create, populate, retrieve, leave feedback, delete — then verify not-found."""
    svc = conversation_service
    user_id = f"integration-test-{uuid.uuid4()}"

    # ── 1. Create conversation ──────────────────────────────────────────
    conv = await svc.create_conversation(user_id)
    conv_id = conv.id

    assert conv.user_id == user_id
    assert conv.messages == []
    assert conv.metadata.message_count == 0

    # ── 2. Add a user message ───────────────────────────────────────────
    user_msg = MessageRecord(
        id=str(uuid.uuid4()),
        role="user",
        content="What is the leave policy?",
        timestamp=datetime.now(UTC),
    )
    await svc.add_message(conv_id, user_id, user_msg)

    # ── 3. Add an assistant message ─────────────────────────────────────
    assistant_msg = MessageRecord(
        id=str(uuid.uuid4()),
        role="assistant",
        content="You are entitled to 20 days of annual leave per year.",
        agent="hr_agent",
        timestamp=datetime.now(UTC),
    )
    await svc.add_message(conv_id, user_id, assistant_msg)

    # ── 4. Retrieve and verify message order and content ────────────────
    loaded = await svc.get_conversation(conv_id, user_id)
    assert loaded is not None
    assert len(loaded.messages) == 2
    assert loaded.messages[0].role == "user"
    assert loaded.messages[0].content == "What is the leave policy?"
    assert loaded.messages[1].role == "assistant"
    assert loaded.messages[1].content == "You are entitled to 20 days of annual leave per year."
    assert loaded.messages[1].agent == "hr_agent"

    # ── 5. Submit feedback on the assistant message ─────────────────────
    feedback = FeedbackRecord(
        message_id=assistant_msg.id,
        rating="positive",
        comment="Very helpful answer.",
    )
    await svc.add_feedback(conv_id, user_id, feedback)

    # Re-fetch and verify feedback is attached
    loaded_with_fb = await svc.get_conversation(conv_id, user_id)
    assert loaded_with_fb is not None
    assert len(loaded_with_fb.metadata.feedback) == 1
    assert loaded_with_fb.metadata.feedback[0].rating == "positive"
    assert loaded_with_fb.metadata.feedback[0].message_id == assistant_msg.id

    # ── 6. Delete conversation — verify cascade ─────────────────────────
    deleted = await svc.delete_conversation(conv_id, user_id)
    assert deleted is True

    # ── 7. Verify deleted conversation returns None (not-found) ─────────
    gone = await svc.get_conversation(conv_id, user_id)
    assert gone is None


@pytest.mark.asyncio
async def test_user_isolation(conversation_service):
    """A conversation created by one user must not be visible to another."""
    svc = conversation_service
    user_a = f"user-a-{uuid.uuid4()}"
    user_b = f"user-b-{uuid.uuid4()}"

    conv = await svc.create_conversation(user_a)

    # User B cannot retrieve User A's conversation
    result = await svc.get_conversation(conv.id, user_b)
    assert result is None

    # User B cannot delete User A's conversation
    deleted = await svc.delete_conversation(conv.id, user_b)
    assert deleted is False

    # Clean up
    await svc.delete_conversation(conv.id, user_a)


@pytest.mark.asyncio
async def test_last_active_agent_tracking(conversation_service):
    """Updating last_active_agent is persisted and retrievable."""
    svc = conversation_service
    user_id = f"integration-test-{uuid.uuid4()}"

    conv = await svc.create_conversation(user_id)
    assert conv.metadata.last_active_agent is None

    await svc.update_last_active_agent(conv.id, user_id, "it_agent")

    loaded = await svc.get_conversation(conv.id, user_id)
    assert loaded is not None
    assert loaded.metadata.last_active_agent == "it_agent"

    # Clean up
    await svc.delete_conversation(conv.id, user_id)
