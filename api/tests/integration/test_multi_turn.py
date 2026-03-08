"""Integration tests for multi-turn conversation and topic switching."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from agent_framework import BaseContextProvider, Message, SessionContext

from src.models.agent import AgentResponseModel
from src.models.conversation import (
    ConversationDocument,
    ConversationMetadata,
    MessageRecord,
)
from src.orchestrator.history import (
    ConversationHistoryProvider,
    current_conversation_id,
    current_user_id,
)


def _make_conversation(
    messages: list[MessageRecord],
    last_active_agent: str | None = None,
) -> ConversationDocument:
    """Helper to build a ConversationDocument with the given messages."""
    now = datetime.now(UTC)
    return ConversationDocument(
        id=str(uuid.uuid4()),
        user_id="user-1",
        created_at=now,
        updated_at=now,
        messages=messages,
        metadata=ConversationMetadata(
            last_active_agent=last_active_agent,
            message_count=len(messages),
        ),
    )


def _user_msg(content: str) -> MessageRecord:
    return MessageRecord(
        id=str(uuid.uuid4()),
        role="user",
        content=content,
        timestamp=datetime.now(UTC),
    )


def _assistant_msg(content: str, agent: str = "coordinator") -> MessageRecord:
    return MessageRecord(
        id=str(uuid.uuid4()),
        role="assistant",
        content=content,
        agent=agent,
        response=AgentResponseModel(message=content, confidence="medium"),
        timestamp=datetime.now(UTC),
    )


class TestConversationHistoryProvider:
    """Tests for ConversationHistoryProvider."""

    @pytest.mark.asyncio
    async def test_history_injected_via_context_provider(self):
        """Conversation history is correctly injected via context provider."""
        messages = [
            _user_msg("What is the leave policy?"),
            _assistant_msg("You get 20 days annual leave.", agent="hr_agent"),
        ]
        conversation = _make_conversation(messages, last_active_agent="hr_agent")

        mock_service = AsyncMock()
        mock_service.get_conversation.return_value = conversation

        provider = ConversationHistoryProvider(mock_service, max_messages=20)

        context = SessionContext(
            input_messages=[Message("user", ["follow-up question"])],
        )
        session = MagicMock()
        agent = MagicMock()

        token_cid = current_conversation_id.set(conversation.id)
        token_uid = current_user_id.set("user-1")
        try:
            await provider.before_run(agent=agent, session=session, context=context, state={})
        finally:
            current_conversation_id.reset(token_cid)
            current_user_id.reset(token_uid)

        # Verify messages were injected
        injected = context.context_messages.get("conversation_history", [])
        assert len(injected) == 2
        assert injected[0].role == "user"
        assert "leave policy" in injected[0].text
        assert injected[1].role == "assistant"
        assert "20 days" in injected[1].text

    @pytest.mark.asyncio
    async def test_history_truncated_beyond_limit(self):
        """History is truncated to the configured max_messages limit."""
        # Create 30 message pairs (60 messages total)
        messages: list[MessageRecord] = []
        for i in range(30):
            messages.append(_user_msg(f"Question {i}"))
            messages.append(_assistant_msg(f"Answer {i}", agent="hr_agent"))

        conversation = _make_conversation(messages)

        mock_service = AsyncMock()
        mock_service.get_conversation.return_value = conversation

        # Limit to 10 messages
        provider = ConversationHistoryProvider(mock_service, max_messages=10)

        context = SessionContext(
            input_messages=[Message("user", ["next question"])],
        )
        session = MagicMock()
        agent = MagicMock()

        token_cid = current_conversation_id.set(conversation.id)
        token_uid = current_user_id.set("user-1")
        try:
            await provider.before_run(agent=agent, session=session, context=context, state={})
        finally:
            current_conversation_id.reset(token_cid)
            current_user_id.reset(token_uid)

        injected = context.context_messages.get("conversation_history", [])
        # Should only have 10 messages (the last 10 from the conversation)
        assert len(injected) == 10

    @pytest.mark.asyncio
    async def test_follow_up_gets_same_agent_context(self):
        """Follow-up question gets same agent context when topic hasn't changed."""
        messages = [
            _user_msg("How do I reset my password?"),
            _assistant_msg("Go to Settings > Security > Reset Password.", agent="it_agent"),
        ]
        conversation = _make_conversation(messages, last_active_agent="it_agent")

        mock_service = AsyncMock()
        mock_service.get_conversation.return_value = conversation

        provider = ConversationHistoryProvider(mock_service, max_messages=20)

        context = SessionContext(
            input_messages=[Message("user", ["What if that doesn't work?"])],
        )
        session = MagicMock()
        agent = MagicMock()

        token_cid = current_conversation_id.set(conversation.id)
        token_uid = current_user_id.set("user-1")
        try:
            await provider.before_run(agent=agent, session=session, context=context, state={})
        finally:
            current_conversation_id.reset(token_cid)
            current_user_id.reset(token_uid)

        # Verify history includes the IT context so the coordinator can
        # recognize the ongoing IT topic and route accordingly
        injected = context.context_messages.get("conversation_history", [])
        assert len(injected) == 2
        assert "reset" in injected[0].text.lower() or "password" in injected[0].text.lower()
        assert "Settings" in injected[1].text

        # The last_active_agent is tracked in conversation metadata
        assert conversation.metadata.last_active_agent == "it_agent"

    @pytest.mark.asyncio
    async def test_topic_switch_provides_full_history(self):
        """Switching from HR to IT topic still provides full history for routing."""
        messages = [
            _user_msg("What is the leave policy?"),
            _assistant_msg("You get 20 days annual leave.", agent="hr_agent"),
            _user_msg("How many sick days do I get?"),
            _assistant_msg("You get 10 sick days per year.", agent="hr_agent"),
        ]
        conversation = _make_conversation(messages, last_active_agent="hr_agent")

        mock_service = AsyncMock()
        mock_service.get_conversation.return_value = conversation

        provider = ConversationHistoryProvider(mock_service, max_messages=20)

        # User now switches to an IT topic
        context = SessionContext(
            input_messages=[Message("user", ["How do I connect to the VPN?"])],
        )
        session = MagicMock()
        agent = MagicMock()

        token_cid = current_conversation_id.set(conversation.id)
        token_uid = current_user_id.set("user-1")
        try:
            await provider.before_run(agent=agent, session=session, context=context, state={})
        finally:
            current_conversation_id.reset(token_cid)
            current_user_id.reset(token_uid)

        # All 4 prior messages are injected so the coordinator sees the full
        # context and can decide to route to a different agent
        injected = context.context_messages.get("conversation_history", [])
        assert len(injected) == 4

        # The new VPN question is in input_messages, not in injected history
        assert context.input_messages[0].text == "How do I connect to the VPN?"

    @pytest.mark.asyncio
    async def test_no_context_when_ids_missing(self):
        """No history injected when conversation_id or user_id is missing in options."""
        mock_service = AsyncMock()

        provider = ConversationHistoryProvider(mock_service, max_messages=20)

        context = SessionContext(input_messages=[Message("user", ["hello"])])
        session = MagicMock()
        agent = MagicMock()

        await provider.before_run(agent=agent, session=session, context=context, state={})

        # No messages should be injected
        assert context.context_messages.get("conversation_history") is None
        mock_service.get_conversation.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_context_when_conversation_not_found(self):
        """No history injected when conversation doesn't exist in DB."""
        mock_service = AsyncMock()
        mock_service.get_conversation.return_value = None

        provider = ConversationHistoryProvider(mock_service, max_messages=20)

        context = SessionContext(
            input_messages=[Message("user", ["hello"])],
        )
        session = MagicMock()
        agent = MagicMock()

        token_cid = current_conversation_id.set("nonexistent")
        token_uid = current_user_id.set("user-1")
        try:
            await provider.before_run(agent=agent, session=session, context=context, state={})
        finally:
            current_conversation_id.reset(token_cid)
            current_user_id.reset(token_uid)

        assert context.context_messages.get("conversation_history") is None

    @pytest.mark.asyncio
    async def test_context_is_scoped_per_invocation_options(self):
        """Each invocation uses IDs from its own SessionContext options."""
        mock_service = AsyncMock()
        mock_service.get_conversation.return_value = _make_conversation([])

        provider = ConversationHistoryProvider(mock_service, max_messages=20)
        session = MagicMock()
        agent = MagicMock()

        first_context = SessionContext(
            input_messages=[Message("user", ["first"])],
        )
        token_cid = current_conversation_id.set("conv-a")
        token_uid = current_user_id.set("user-a")
        try:
            await provider.before_run(agent=agent, session=session, context=first_context, state={})
        finally:
            current_conversation_id.reset(token_cid)
            current_user_id.reset(token_uid)

        second_context = SessionContext(
            input_messages=[Message("user", ["second"])],
        )
        token_cid = current_conversation_id.set("conv-b")
        token_uid = current_user_id.set("user-b")
        try:
            await provider.before_run(
                agent=agent, session=session, context=second_context, state={}
            )
        finally:
            current_conversation_id.reset(token_cid)
            current_user_id.reset(token_uid)

        assert mock_service.get_conversation.await_args_list[0].args == ("conv-a", "user-a")
        assert mock_service.get_conversation.await_args_list[1].args == ("conv-b", "user-b")

    @pytest.mark.asyncio
    async def test_provider_is_base_context_provider(self):
        """ConversationHistoryProvider extends BaseContextProvider."""
        mock_service = AsyncMock()
        provider = ConversationHistoryProvider(mock_service)

        assert isinstance(provider, BaseContextProvider)
        assert provider.source_id == "conversation_history"
