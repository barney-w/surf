from contextvars import ContextVar
from typing import Any

from agent_framework import (
    AgentSession,
    BaseContextProvider,
    Message,
    SessionContext,
    SupportsAgentRun,
)

from src.services.conversation import ConversationService

# Set these before calling workflow.run() so the provider can read them
# without leaking values through workflow options into the LLM call.
current_conversation_id: ContextVar[str | None] = ContextVar(
    "current_conversation_id", default=None
)
current_user_id: ContextVar[str | None] = ContextVar("current_user_id", default=None)

# Per-request cache for conversation history messages.  Set to a fresh dict
# before each workflow run so that the second before_run call (domain agent)
# reuses the Cosmos result from the first call (coordinator).
_history_cache: ContextVar[dict[str, list[Message]]] = ContextVar(
    "_history_cache", default={}
)


def reset_history_cache() -> None:
    """Reset the per-request history cache.  Call before each workflow run."""
    _history_cache.set({})


class ConversationHistoryProvider(BaseContextProvider):
    """Loads conversation history from Cosmos DB and injects as prior messages."""

    def __init__(self, conversation_service: ConversationService, max_messages: int = 20):
        super().__init__(source_id="conversation_history")
        self._service = conversation_service
        self._max_messages = max_messages

    async def before_run(
        self,
        *,
        agent: SupportsAgentRun,
        session: AgentSession,
        context: SessionContext,
        state: dict[str, Any],
    ) -> None:
        conversation_id = current_conversation_id.get()
        user_id = current_user_id.get()
        if not conversation_id or not user_id:
            return

        cache = _history_cache.get()
        cache_key = f"{conversation_id}:{user_id}"

        if cache_key in cache:
            context.extend_messages(self.source_id, cache[cache_key])
            return

        conversation = await self._service.get_conversation(conversation_id, user_id)
        if not conversation:
            return

        history_messages = conversation.messages[-self._max_messages :]
        framework_messages: list[Message] = []
        for msg in history_messages:
            if msg.role == "user":
                framework_messages.append(Message("user", [msg.content or ""]))
            elif msg.role == "assistant":
                # Prefer the structured response message, fall back to raw content
                # so assistant turns are never silently dropped from context.
                text = (msg.response.message if msg.response else None) or msg.content or ""
                if text:
                    framework_messages.append(Message("assistant", [text]))

        cache[cache_key] = framework_messages
        context.extend_messages(self.source_id, framework_messages)
