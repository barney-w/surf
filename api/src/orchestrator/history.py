from contextvars import ContextVar

from agent_framework import BaseContextProvider, Message, SessionContext

from src.services.conversation import ConversationService

# Set these before calling workflow.run() so the provider can read them
# without leaking values through workflow options into the LLM call.
current_conversation_id: ContextVar[str | None] = ContextVar("current_conversation_id", default=None)
current_user_id: ContextVar[str | None] = ContextVar("current_user_id", default=None)


class ConversationHistoryProvider(BaseContextProvider):
    """Loads conversation history from Cosmos DB and injects as prior messages."""

    def __init__(self, conversation_service: ConversationService, max_messages: int = 20):
        super().__init__(source_id="conversation_history")
        self._service = conversation_service
        self._max_messages = max_messages

    async def before_run(self, *, agent, session, context: SessionContext, state: dict, **kw):
        conversation_id = current_conversation_id.get()
        user_id = current_user_id.get()
        if not conversation_id or not user_id:
            return

        conversation = await self._service.get_conversation(conversation_id, user_id)
        if not conversation:
            return

        history_messages = conversation.messages[-self._max_messages :]
        framework_messages: list[Message] = []
        for msg in history_messages:
            if msg.role == "user":
                framework_messages.append(Message("user", [msg.content or ""]))
            elif msg.role == "assistant" and msg.response:
                framework_messages.append(Message("assistant", [msg.response.message]))

        context.extend_messages(self.source_id, framework_messages)
