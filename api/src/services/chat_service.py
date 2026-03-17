"""Shared chat workflow logic for request preparation and persistence.

Extracts common logic used by both ``/chat`` and ``/chat/stream`` endpoints
so that route handlers remain thin wrappers around the AI workflow.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

import asyncpg
from fastapi import HTTPException

from src.agents._output import sanitize_agent_response
from src.middleware.error_handler import LLM_TIMEOUT_SECONDS, LLMTimeoutError
from src.middleware.input_validation import validate_message
from src.models.agent import AgentResponseModel
from src.models.chat import ChatRequest
from src.models.conversation import AttachmentRecord, MessageRecord
from src.orchestrator.builder import current_attachments
from src.orchestrator.history import current_conversation_id, current_user_id, reset_history_cache
from src.rag.tools import rag_results_collector

if TYPE_CHECKING:
    from agent_framework import Workflow, WorkflowEvent

logger = logging.getLogger(__name__)


@dataclass
class ChatContext:
    """Prepared state for a chat request, shared between endpoints."""

    conversation_id: str
    user_id: str
    sanitised_message: str
    db_available: bool
    conversation_service: object | None
    attachment_records: list[AttachmentRecord] = field(default_factory=list)
    target_agent: str | None = None


def build_attachment_records(body: ChatRequest) -> list[AttachmentRecord]:
    """Convert request attachments to persistence-safe records (no base64 data)."""
    records: list[AttachmentRecord] = []
    for att in body.attachments:
        decoded = base64.b64decode(att.data, validate=True)
        records.append(
            AttachmentRecord(
                filename=att.filename,
                content_type=att.content_type,
                size=len(decoded),
            )
        )
    return records


def set_attachments_context(body: ChatRequest) -> None:
    """Set the current_attachments context variable from request attachments."""
    if body.attachments:
        current_attachments.set(
            [{"content_type": att.content_type, "data": att.data} for att in body.attachments]
        )
    else:
        current_attachments.set(None)


async def prepare_chat_request(
    body: ChatRequest,
    user_id: str,
    conversation_service: object | None,
) -> ChatContext:
    """Validate input, create/load conversation, build attachment records.

    Returns a :class:`ChatContext` holding all the prepared state that both
    the ``/chat`` and ``/chat/stream`` endpoints need before running the
    AI workflow.

    Raises :class:`~fastapi.HTTPException` on validation or not-found errors.
    """
    sanitised_message = validate_message(body.message)

    db_available = conversation_service is not None
    conversation_id = body.conversation_id or str(uuid.uuid4())

    if db_available:
        try:
            if body.conversation_id:
                conversation = await conversation_service.get_conversation(  # type: ignore[union-attr]
                    body.conversation_id, user_id
                )
                if conversation is None:
                    raise HTTPException(status_code=404, detail="Conversation not found")
                conversation_id = body.conversation_id
            else:
                conversation = await conversation_service.create_conversation(user_id)  # type: ignore[union-attr]
                conversation_id = conversation.id
        except HTTPException:
            raise
        except Exception:
            logger.warning(
                "Database unavailable — continuing without persistence",
                exc_info=True,
            )
            db_available = False

    set_attachments_context(body)
    attachment_records = build_attachment_records(body)

    target_agent = body.agent if body.agent and body.agent != "coordinator" else None

    return ChatContext(
        conversation_id=conversation_id,
        user_id=user_id,
        sanitised_message=sanitised_message,
        db_available=db_available,
        conversation_service=conversation_service,
        attachment_records=attachment_records,
        target_agent=target_agent,
    )


def setup_context_vars(ctx: ChatContext) -> list[str]:
    """Set context variables for the AI workflow.

    Sets ``current_conversation_id``, ``current_user_id``, resets the history
    cache, and initialises a fresh RAG results collector.

    Returns the RAG collector list so callers can read collected outputs after
    the workflow completes.
    """
    current_conversation_id.set(ctx.conversation_id)
    current_user_id.set(ctx.user_id)
    reset_history_cache()
    rag_collector: list[str] = []
    rag_results_collector.set(rag_collector)
    return rag_collector


async def run_workflow(
    workflow_factory: object,
    message: str,
    *,
    ctx: ChatContext,
) -> tuple[str, AgentResponseModel | None, str, list[str]]:
    """Run the AI workflow with a timeout.

    Returns (response_text, structured_result, routed_agent, rag_outputs).
    Raises :class:`~src.middleware.error_handler.LLMTimeoutError` if the
    workflow exceeds ``LLM_TIMEOUT_SECONDS``.
    """
    rag_collector = setup_context_vars(ctx)
    target_agent = ctx.target_agent

    # Build a fresh Workflow per request — agent_framework Workflow is stateful
    # and raises RuntimeError if run() is called while another run is in progress.
    workflow: Workflow = workflow_factory()  # type: ignore[operator]

    async def _execute() -> tuple[str, AgentResponseModel | None, str]:
        response_text = ""
        structured_result: AgentResponseModel | None = None
        routed_agent = target_agent if target_agent else "coordinator"
        async for _event in workflow.run(  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType]
            message,
            stream=True,
        ):
            event = cast("WorkflowEvent[Any]", _event)
            if event.type == "handoff_sent":
                routed_agent = event.data.target
            elif event.type == "output":
                if hasattr(event.data, "value") and isinstance(
                    event.data.value, AgentResponseModel
                ):
                    structured_result = sanitize_agent_response(event.data.value)
                elif hasattr(event.data, "text") and event.data.text:
                    response_text += event.data.text
        return response_text, structured_result, routed_agent

    try:
        result = await asyncio.wait_for(_execute(), timeout=LLM_TIMEOUT_SECONDS)
        return (*result, rag_collector)
    except TimeoutError as err:
        raise LLMTimeoutError(f"LLM workflow timed out after {LLM_TIMEOUT_SECONDS}s") from err


async def persist_exchange(
    ctx: ChatContext,
    sanitised_message: str,
    agent_response_message: str,
    agent_response: AgentResponseModel,
    routed_agent: str,
    message_id: str,
    *,
    attachment_records: list[AttachmentRecord] | None = None,
) -> bool:
    """Persist user message, assistant message, and update last_active_agent.

    Returns ``True`` if persistence succeeded, ``False`` if the database was
    unavailable (the caller should set ``db_available = False``).
    """
    if not ctx.db_available:
        return False

    records = attachment_records if attachment_records is not None else ctx.attachment_records

    user_message = MessageRecord(
        id=str(uuid.uuid4()),
        role="user",
        content=sanitised_message,
        attachments=records,
        timestamp=datetime.now(UTC),
    )
    assistant_message = MessageRecord(
        id=message_id,
        role="assistant",
        content=agent_response_message,
        agent=routed_agent,
        response=agent_response,
        timestamp=datetime.now(UTC),
    )

    ok = await _persist_message(
        ctx.conversation_service, ctx.conversation_id, ctx.user_id, user_message
    )
    if ok:
        ok = await _persist_message(
            ctx.conversation_service, ctx.conversation_id, ctx.user_id, assistant_message
        )
    if ok:
        await _update_last_active_agent(
            ctx.conversation_service, ctx.conversation_id, ctx.user_id, routed_agent
        )
        return True

    return False


async def _update_last_active_agent(
    conversation_service: object | None,
    conversation_id: str,
    user_id: str,
    agent_name: str,
) -> None:
    """Update metadata.last_active_agent on the conversation document."""
    if conversation_service is None:
        return
    try:
        await conversation_service.update_last_active_agent(  # type: ignore[union-attr]
            conversation_id, user_id, agent_name
        )
    except (asyncpg.PostgresError, ConnectionError, TimeoutError, OSError, ValueError):
        logger.warning(
            "Could not update last_active_agent for conversation %s",
            conversation_id,
            exc_info=True,
        )


async def _persist_message(
    conversation_service: object | None,
    conversation_id: str,
    user_id: str,
    message: MessageRecord,
) -> bool:
    """Attempt to persist a message, returning False on database failure."""
    if conversation_service is None:
        return False
    try:
        await conversation_service.add_message(conversation_id, user_id, message)  # type: ignore[union-attr]
        return True
    except (asyncpg.PostgresError, ConnectionError, TimeoutError, OSError, ValueError):
        logger.error(
            "Database unavailable — could not persist message %s for conversation %s",
            message.id,
            conversation_id,
            exc_info=True,
        )
        return False
