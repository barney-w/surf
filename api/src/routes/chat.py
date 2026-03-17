import asyncio
import json
import logging
import re
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from fastapi import APIRouter, HTTPException, Request

if TYPE_CHECKING:
    from agent_framework import Workflow, WorkflowEvent
import asyncpg
from anthropic import BadRequestError as AnthropicBadRequestError
from fastapi.responses import JSONResponse, StreamingResponse
from openai import RateLimitError as OpenAIRateLimitError

from src.agents._output import (
    deduplicate_sources,
    extract_sources,
    parse_agent_output,
    sanitize_agent_response,
    strip_source_urls,
)
from src.agents._proofread import proofread_message
from src.config.settings import get_settings
from src.middleware.auth import UserContext, get_current_user
from src.middleware.error_handler import LLM_TIMEOUT_SECONDS, LLMTimeoutError
from src.middleware.input_validation import validate_message
from src.middleware.rate_limit import limiter
from src.models.agent import (
    AgentResponseModel,
    RoutingMetadata,
    enrich_agent_response,
)
from src.models.chat import ChatRequest, ChatResponse
from src.models.conversation import AttachmentRecord, FeedbackRecord, MessageRecord
from src.orchestrator.builder import current_attachments
from src.orchestrator.history import current_conversation_id, current_user_id, reset_history_cache
from src.rag.quality_gate import run_quality_gate
from src.rag.tools import rag_results_collector

logger = logging.getLogger(__name__)


def _resolve_workflow_factory(
    body: ChatRequest,
    request: Request,
    user: "UserContext",
) -> object:
    """Return the workflow factory for the request, applying direct agent targeting.

    When ``body.agent`` is set to a domain agent name (not "coordinator"), this
    builds a single-agent workflow that bypasses the coordinator.  Auth-level
    checks ensure the caller has sufficient permissions for the targeted agent.

    Raises HTTPException (404/403/503) on validation failures.
    """
    from src.agents._base import AuthLevel
    from src.agents._registry import AgentRegistry
    from src.routes.agents import _can_access, _resolve_caller_auth_level

    caller_level = _resolve_caller_auth_level(user)

    if body.agent and body.agent != "coordinator":
        agent_graph = request.app.state.agent_graph
        if agent_graph is None:
            raise HTTPException(status_code=503, detail="AI workflow not available.")

        # Check agent exists in the registry
        agent_cls = AgentRegistry.get(body.agent)
        if agent_cls is None:
            raise HTTPException(status_code=404, detail=f"Agent '{body.agent}' not found")

        # Check auth level
        agent_def = agent_cls()
        if not _can_access(agent_def.auth_level, caller_level):
            raise HTTPException(
                status_code=403,
                detail=f"Insufficient permissions to access {agent_def.display_name} agent",
            )

        def _factory() -> "Workflow":
            wf = agent_graph.build_single_agent_workflow(body.agent)
            if wf is None:
                raise HTTPException(
                    status_code=404, detail=f"Agent '{body.agent}' not found in workflow graph"
                )
            return wf

        return _factory

    # Default path: select the auth-filtered graph for the caller's level.
    agent_graphs = getattr(request.app.state, "agent_graphs", None)
    if agent_graphs is not None:
        graph = agent_graphs.get(caller_level, agent_graphs[AuthLevel.PUBLIC])
        return graph.build_workflow

    return request.app.state.workflow


# Number of heartbeat ticks (5s each) before emitting phase(waiting).
_HEARTBEAT_WAIT_TICKS = 2  # 10 seconds

_PROMPT_TOO_LONG_MESSAGE = (
    "The uploaded document is too large to process. "
    "Please try a shorter document or ask about specific pages."
)


def _is_prompt_too_long(exc: Exception) -> bool:
    """Check if an exception is an Anthropic prompt-too-long error."""
    if isinstance(exc, AnthropicBadRequestError):
        msg = str(exc).lower()
        return "prompt is too long" in msg or "too many tokens" in msg
    return False


router = APIRouter(prefix="/api/v1", tags=["chat"])


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
        # Database can raise various exceptions (connection errors, etc.)
        # — never let a metadata update failure break the request.
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
        # Database can raise various exceptions — never let persistence failure break the request.
        logger.error(
            "Database unavailable — could not persist message %s for conversation %s",
            message.id,
            conversation_id,
            exc_info=True,
        )
        return False


def _build_attachment_records(body: ChatRequest) -> list[AttachmentRecord]:
    """Convert request attachments to persistence-safe records (no base64 data)."""
    import base64

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


async def _proofread_response(agent_response: AgentResponseModel) -> AgentResponseModel:
    """Run the proofread step on the agent response message if enabled."""
    settings = get_settings()
    if not settings.proofread_enabled:
        return agent_response
    corrected = await proofread_message(agent_response.message, settings)
    if corrected != agent_response.message:
        return agent_response.model_copy(update={"message": corrected})
    return agent_response


def _set_attachments_context(body: ChatRequest) -> None:
    """Set the current_attachments context variable from request attachments."""
    if body.attachments:
        current_attachments.set(
            [{"content_type": att.content_type, "data": att.data} for att in body.attachments]
        )
    else:
        current_attachments.set(None)


async def _run_workflow(
    workflow_factory: object,
    message: str,
    *,
    conversation_id: str,
    user_id: str,
    target_agent: str | None = None,
) -> tuple[str, AgentResponseModel | None, str, list[str]]:
    """Run the AI workflow with a timeout.

    Returns (response_text, structured_result, routed_agent, rag_outputs).
    Raises LLMTimeoutError if the workflow exceeds LLM_TIMEOUT_SECONDS.
    """
    # Set context vars so ConversationHistoryProvider can read them
    # without passing them through workflow options (which leak to the LLM client).
    current_conversation_id.set(conversation_id)
    current_user_id.set(user_id)
    reset_history_cache()
    rag_collector: list[str] = []
    rag_results_collector.set(rag_collector)

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


@router.post("/chat")
@limiter.limit("10/minute")  # pyright: ignore[reportUnknownMemberType,reportUntypedFunctionDecorator]
async def chat(body: ChatRequest, request: Request) -> JSONResponse:
    """Send a message and receive an AI response."""
    conversation_service = request.app.state.conversation_service
    user = await get_current_user(request)
    user_id = user.user_id

    workflow = _resolve_workflow_factory(body, request, user)
    if workflow is None:
        raise HTTPException(
            status_code=503,
            detail="AI workflow not available. Azure OpenAI endpoint not configured.",
        )

    # Validate & sanitise input
    sanitised_message = validate_message(body.message)

    # Create or load conversation — handle database unavailability
    db_available = conversation_service is not None
    conversation_id = body.conversation_id or str(uuid.uuid4())

    if db_available:
        try:
            if body.conversation_id:
                conversation = await conversation_service.get_conversation(
                    body.conversation_id, user_id
                )
                if conversation is None:
                    raise HTTPException(status_code=404, detail="Conversation not found")
                conversation_id = body.conversation_id
            else:
                conversation = await conversation_service.create_conversation(user_id)
                conversation_id = conversation.id
        except HTTPException:
            raise
        except Exception:
            # Database can raise various exceptions — degrade gracefully.
            logger.warning(
                "Database unavailable — continuing without persistence",
                exc_info=True,
            )
            db_available = False

    # Set attachment context for multimodal LLM calls
    _set_attachments_context(body)

    # Run the workflow (with timeout)
    rag_available = True
    try:
        response_text, structured_result, routed_agent, rag_outputs = await _run_workflow(
            workflow,
            sanitised_message,
            conversation_id=conversation_id,
            user_id=user_id,
            target_agent=body.agent if body.agent and body.agent != "coordinator" else None,
        )
    except LLMTimeoutError:
        raise
    except Exception as exc:
        if _is_prompt_too_long(exc):
            raise HTTPException(
                status_code=413,
                detail=_PROMPT_TOO_LONG_MESSAGE,
            ) from exc
        # RAG or other non-timeout workflow error — degrade gracefully
        logger.warning(
            "Workflow error (possible RAG failure) — returning low-confidence response",
            exc_info=True,
        )
        response_text = ""
        structured_result = None
        routed_agent = "coordinator"
        rag_outputs: list[str] = []
        rag_available = False

    # Build response — use structured output if available, otherwise fallback
    message_id = str(uuid.uuid4())
    if not rag_available:
        agent_response = AgentResponseModel(
            message=(
                "I was unable to fully process your request because some knowledge sources "
                "are temporarily unavailable. Please try again shortly."
            ),
            confidence="low",
            follow_up_suggestions=[
                "Could you rephrase your question?",
                "Try again in a moment.",
            ],
        )
    elif structured_result is not None:
        agent_response = structured_result
    else:
        fallback_text = response_text or "I'm sorry, I couldn't generate a response."
        agent_response = parse_agent_output(fallback_text, routed_agent)

    # Quality gate — catch search-skipped or results-ignored failures
    gate_result = run_quality_gate(agent_response, rag_outputs, routed_agent)
    agent_response = gate_result.remediated
    if gate_result.check != "passed":
        logger.warning(
            "quality_gate_triggered check=%s agent=%s rag_count=%d source_count=%d confidence=%s",
            gate_result.check,
            routed_agent,
            len(rag_outputs),
            len(agent_response.sources),
            agent_response.confidence,
        )

    # Inject sources recovered from the RAG tool output if the agent didn't populate them.
    if not agent_response.sources and rag_outputs:
        rag_text = "\n\n".join(rag_outputs)
        recovered = deduplicate_sources(extract_sources(rag_text))
        if recovered:
            agent_response = agent_response.model_copy(update={"sources": recovered})

    # Proofread — fix generation artefacts (dropped chars, broken markdown)
    agent_response = await _proofread_response(agent_response)

    # Strip source URLs for agents backed by internal document stores
    # (e.g. SharePoint) to avoid exposing infrastructure details.
    if routed_agent == "hr_agent":
        agent_response = strip_source_urls(agent_response)

    routing = RoutingMetadata(
        routed_by="coordinator",
        primary_agent=routed_agent,
    )

    # Persist user message, assistant message, and last-active-agent in parallel.
    user_message = MessageRecord(
        id=str(uuid.uuid4()),
        role="user",
        content=sanitised_message,
        attachments=_build_attachment_records(body),
        timestamp=datetime.now(UTC),
    )
    assistant_message = MessageRecord(
        id=message_id,
        role="assistant",
        content=agent_response.message,
        agent=routed_agent,
        response=agent_response,
        timestamp=datetime.now(UTC),
    )
    if db_available:
        ok = await _persist_message(conversation_service, conversation_id, user_id, user_message)
        if ok:
            ok = await _persist_message(
                conversation_service, conversation_id, user_id, assistant_message
            )
        if ok:
            await _update_last_active_agent(
                conversation_service, conversation_id, user_id, routed_agent
            )
        else:
            db_available = False

    chat_response = ChatResponse(
        conversation_id=conversation_id,
        message_id=message_id,
        agent=routed_agent,
        response=agent_response,
        routing=routing,
        created_at=datetime.now(UTC),
    )

    response = JSONResponse(
        content=chat_response.model_dump(mode="json"),
    )
    if not db_available:
        response.headers["X-Surf-Warning"] = "db-unavailable"
    return response


def _sse(data: dict[str, object]) -> str:
    """Format a dict as a single SSE data event."""
    return f"data: {json.dumps(data)}\n\n"


class _MessageFieldExtractor:
    """Extract the 'message' string value from a streaming JSON document.

    Domain agents emit AgentResponseModel JSON via response_format. Streaming
    delivers it as raw token chunks (e.g. '{"message": "Ann', 'ual leave...').
    This class scans for the "message": " marker and forwards the string value
    characters as they arrive, so the client can render them in real time.

    Since 'message' is the first field in AgentResponseModel, readable text
    starts flowing almost immediately after the LLM begins generating.

    Source-pollution guard: if the LLM puts raw === SOURCE === blocks inside the
    message field (prompt non-compliance), the extractor detects the prefix and
    suppresses all streaming output. The sanitized final response is delivered
    via the 'done' event instead, so the user still gets a clean answer.
    """

    _NEEDLE = re.compile(r'"message"\s*:\s*"')
    # Prefix that indicates the LLM leaked RAG source markers into the message.
    _SOURCE_POLLUTION_PREFIX = "=== SOURCE"
    _GUARD_LEN = len(_SOURCE_POLLUTION_PREFIX)

    def __init__(self) -> None:
        self._buf = ""  # pre-marker accumulation
        self._in_value = False
        self._escape = False
        self._done = False
        self._guard_buf = ""  # buffer first N chars for pollution check
        self._suppressed = False  # True once pollution is detected
        self._unicode_remaining = 0  # hex digits still expected for \uXXXX
        self._unicode_hex = ""  # accumulated hex digits

    def feed(self, chunk: str) -> str:
        """Feed a token chunk. Returns any message content ready to stream."""
        if self._done or self._suppressed:
            return ""

        if not self._in_value:
            self._buf += chunk
            m = self._NEEDLE.search(self._buf)
            if not m:
                return ""
            self._in_value = True
            remainder = self._buf[m.end() :]
            self._buf = ""
            return self._guarded_read(remainder)

        return self._guarded_read(chunk)

    def _guarded_read(self, s: str) -> str:
        """Read string chars, applying the pollution guard then normal extraction."""
        if self._suppressed:
            return ""

        # Still buffering the guard window.  Process the full input through
        # _read_string (escape sequences like \uXXXX consume multiple input
        # chars per output char, so we cannot split by input length).
        if len(self._guard_buf) < self._GUARD_LEN:
            out_inner, done = self._read_string(s)
            self._guard_buf += out_inner
            if done:
                self._done = True
                if self._guard_buf.startswith(self._SOURCE_POLLUTION_PREFIX):
                    self._suppressed = True
                    logger.warning(
                        "_MessageFieldExtractor: source pollution detected — suppressing stream"
                    )
                    return ""
                return self._guard_buf

            if len(self._guard_buf) >= self._GUARD_LEN:
                if self._guard_buf.startswith(self._SOURCE_POLLUTION_PREFIX):
                    self._suppressed = True
                    logger.warning(
                        "_MessageFieldExtractor: source pollution detected — suppressing stream"
                    )
                    return ""
                # Guard passed — emit all buffered chars.
                flushed = self._guard_buf
                self._guard_buf = ""
                return flushed

            return ""  # guard window not yet full

        # Guard already passed — normal extraction.
        out, done = self._read_string(s)
        if done:
            self._done = True
        return out

    def _read_string(self, s: str) -> tuple[str, bool]:
        """Read characters from inside a JSON string value until the closing quote."""
        out: list[str] = []
        for ch in s:
            # Accumulating hex digits for a \uXXXX escape
            if self._unicode_remaining > 0:
                self._unicode_hex += ch
                self._unicode_remaining -= 1
                if self._unicode_remaining == 0:
                    try:
                        out.append(chr(int(self._unicode_hex, 16)))
                    except ValueError:
                        out.append(self._unicode_hex)
                    self._unicode_hex = ""
                continue

            if self._escape:
                if ch == "u":
                    # Start \uXXXX — need 4 hex digits (may span chunks)
                    self._unicode_remaining = 4
                    self._unicode_hex = ""
                else:
                    out.append(
                        {
                            "n": "\n",
                            "t": "\t",
                            "r": "\r",
                            '"': '"',
                            "\\": "\\",
                            "/": "/",
                            "b": "\b",
                            "f": "\f",
                        }.get(ch, ch)
                    )
                self._escape = False
            elif ch == "\\":
                self._escape = True
            elif ch == '"':
                return "".join(out), True
            else:
                out.append(ch)
        return "".join(out), False


@router.post("/chat/stream")
@limiter.limit("10/minute")  # pyright: ignore[reportUnknownMemberType,reportUntypedFunctionDecorator]
async def chat_stream(body: ChatRequest, request: Request) -> StreamingResponse:
    """SSE streaming endpoint — real token streaming from the LLM.

    Events emitted in order:
      phase(thinking) → agent(name) → phase(generating) → delta* (real tokens)
      → phase(verifying) → confidence → verification → done(response) → [DONE]

    Delta events carry real LLM output tokens forwarded as they arrive.
    For domain agents (structured JSON output) the 'message' field value is
    extracted and streamed; for coordinator plain-text responses tokens are
    forwarded directly.  The final 'done' event carries the full enriched
    response (confidence breakdown, verification, sources, follow-ups).
    """
    conversation_service = request.app.state.conversation_service
    user = await get_current_user(request)
    user_id = user.user_id

    workflow = _resolve_workflow_factory(body, request, user)
    if workflow is None:

        async def _no_workflow() -> AsyncGenerator[str, None]:
            yield _sse(
                {
                    "type": "error",
                    "error": {
                        "code": "API_ERROR",
                        "message": "AI workflow not available. Endpoint not configured.",
                        "retryable": False,
                    },
                }
            )

        return StreamingResponse(_no_workflow(), media_type="text/event-stream")

    sanitised_message = validate_message(body.message)
    db_available = conversation_service is not None
    conversation_id = body.conversation_id or str(uuid.uuid4())

    if db_available:
        try:
            if body.conversation_id:
                conversation = await conversation_service.get_conversation(
                    body.conversation_id, user_id
                )
                if conversation is None:

                    async def _not_found() -> AsyncGenerator[str, None]:
                        yield _sse(
                            {
                                "type": "error",
                                "error": {
                                    "code": "API_ERROR",
                                    "message": "Conversation not found",
                                    "retryable": False,
                                },
                            }
                        )

                    return StreamingResponse(_not_found(), media_type="text/event-stream")
                conversation_id = body.conversation_id
            else:
                conversation = await conversation_service.create_conversation(user_id)
                conversation_id = conversation.id
        except (ConnectionError, TimeoutError, OSError):
            logger.warning("Database unavailable — continuing without persistence", exc_info=True)
            db_available = False

    # Set attachment context for multimodal LLM calls (must be set before generate())
    _set_attachments_context(body)
    attachment_records = _build_attachment_records(body)

    async def generate() -> AsyncGenerator[str, None]:
        nonlocal db_available

        current_conversation_id.set(conversation_id)
        current_user_id.set(user_id)
        reset_history_cache()
        rag_collector: list[str] = []
        rag_results_collector.set(rag_collector)
        # Re-set attachments in the generator's context (async generators run in
        # their own context copy).
        _set_attachments_context(body)

        yield _sse({"type": "phase", "phase": "thinking"})

        wf: Workflow = workflow()  # type: ignore[operator]
        # When directly targeting an agent (no coordinator), initialise
        # routed_agent to the target so output is processed as domain-agent
        # JSON rather than buffered as coordinator plain text.
        is_direct = bool(body.agent and body.agent != "coordinator")
        routed_agent = body.agent if is_direct else "coordinator"
        structured_result: AgentResponseModel | None = None
        response_text = ""
        coordinator_buf = ""  # buffer coordinator tokens; discard on handoff
        domain_agent_json_buf = ""  # accumulates raw JSON for domain agents
        extractor = _MessageFieldExtractor()
        generating_announced = False
        message_id = str(uuid.uuid4())

        # Queue used to multiplex workflow events and heartbeat ticks.
        # Items: ('event', event_obj) | ('heartbeat',) | ('done',) | ('error', exc)
        queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()

        async def _run_workflow_into_queue() -> None:
            try:
                async with asyncio.timeout(LLM_TIMEOUT_SECONDS):
                    async for event in wf.run(sanitised_message, stream=True):  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType]
                        await queue.put(("event", event))
                await queue.put(("done", None))
            except Exception as exc:
                await queue.put(("error", exc))

        async def _heartbeat() -> None:
            """Send a tick every 5 seconds so the main loop can emit keepalives."""
            while True:
                await asyncio.sleep(5)
                await queue.put(("heartbeat", None))

        workflow_task = asyncio.create_task(_run_workflow_into_queue())
        heartbeat_task = asyncio.create_task(_heartbeat())
        heartbeat_count = 0

        try:
            while True:
                item = await queue.get()
                kind = item[0]

                if kind == "done":
                    break

                if kind == "error":
                    exc = cast("Exception", item[1])
                    if _is_prompt_too_long(exc):
                        yield _sse(
                            {
                                "type": "error",
                                "error": {
                                    "code": "PROMPT_TOO_LONG",
                                    "message": _PROMPT_TOO_LONG_MESSAGE,
                                    "retryable": False,
                                },
                            }
                        )
                    elif isinstance(exc, TimeoutError):
                        yield _sse(
                            {
                                "type": "error",
                                "error": {
                                    "code": "TIMEOUT",
                                    "message": "Request timed out. Please try again.",
                                    "retryable": True,
                                },
                            }
                        )
                    elif isinstance(exc, OpenAIRateLimitError):
                        logger.warning(
                            "LLM rate limited in SSE stream — quota exhausted after retries"
                        )
                        yield _sse(
                            {
                                "type": "error",
                                "error": {
                                    "code": "RATE_LIMIT",
                                    "message": "The AI service is temporarily busy."
                                    " Please wait a moment and try again.",
                                    "retryable": True,
                                },
                            }
                        )
                    else:
                        logger.warning("Workflow error in SSE stream", exc_info=exc)
                        yield _sse(
                            {
                                "type": "error",
                                "error": {
                                    "code": "API_ERROR",
                                    "message": "The agent encountered an error. Please try again.",
                                    "retryable": True,
                                },
                            }
                        )
                    return

                if kind == "heartbeat":
                    heartbeat_count += 1
                    if heartbeat_count == _HEARTBEAT_WAIT_TICKS and not generating_announced:
                        # 10 seconds elapsed with no output — tell the client to show
                        # a "still working" message (e.g. during a 429 retry window).
                        yield _sse({"type": "phase", "phase": "waiting"})
                    else:
                        # SSE comment — keeps the TCP connection alive, ignored by clients.
                        yield ":keepalive\n\n"
                    continue

                # kind == "event"
                event = cast("WorkflowEvent[Any]", item[1])

                logger.debug("event: type=%s data_type=%s", event.type, type(event.data).__name__)
                if event.type == "handoff_sent":
                    routed_agent = event.data.target
                    logger.debug("handoff_sent: target=%s", routed_agent)
                    # Discard any coordinator text that preceded the handoff.
                    # The coordinator sometimes generates text before calling
                    # the handoff tool; streaming it would duplicate the domain
                    # agent's answer.
                    if coordinator_buf:
                        logger.debug(
                            "Discarding %d chars of coordinator text before handoff",
                            len(coordinator_buf),
                        )
                        coordinator_buf = ""
                    generating_announced = False
                    yield _sse({"type": "agent", "agent": routed_agent})
                    yield _sse({"type": "phase", "phase": "generating"})
                    generating_announced = True
                    heartbeat_count = 0  # reset so we don't re-emit "waiting" mid-generation

                elif event.type == "output":
                    data = event.data

                    # Final AgentResponse — carries the fully parsed structured object.
                    if hasattr(data, "value") and isinstance(data.value, AgentResponseModel):
                        structured_result = sanitize_agent_response(data.value)
                        continue

                    # Detect tool-use output (function_call content blocks).
                    # When a domain agent calls a tool (e.g. search_knowledge_base),
                    # the first LLM turn may have emitted preliminary text that
                    # the extractor already processed.  Reset the extractor and
                    # tell the client to discard streamed deltas so the real
                    # answer (from the post-tool-use turn) streams cleanly.
                    if (
                        routed_agent != "coordinator"
                        and hasattr(data, "contents")
                        and any(getattr(c, "type", None) == "function_call" for c in data.contents)
                    ):
                        if extractor._in_value or extractor._done:
                            logger.debug(
                                "Tool use detected after partial stream — "
                                "resetting extractor and emitting delta_reset"
                            )
                            extractor = _MessageFieldExtractor()
                            domain_agent_json_buf = ""
                            yield _sse({"type": "delta_reset"})
                        yield _sse({"type": "phase", "phase": "retrieving"})

                    # Streaming token chunk (AgentResponseUpdate).
                    chunk = data.text if hasattr(data, "text") else None
                    if not chunk:
                        continue

                    if routed_agent == "coordinator":
                        # Buffer coordinator tokens — don't stream yet.
                        # If a handoff follows, the buffer is discarded so the
                        # domain agent's answer is the only content streamed.
                        # If the coordinator answers directly (no handoff), the
                        # buffer is flushed after the workflow completes.
                        coordinator_buf += chunk

                    else:
                        # Domain agent — JSON output, extract the 'message' field value.
                        domain_agent_json_buf += chunk
                        extracted = extractor.feed(chunk)
                        if extracted:
                            if not generating_announced:
                                yield _sse({"type": "phase", "phase": "generating"})
                                generating_announced = True
                            yield _sse({"type": "delta", "content": extracted})

                elif event.type == "failed":
                    details = event.details
                    error_type = getattr(details, "error_type", "") or ""
                    error_msg = getattr(details, "message", "") or ""
                    logger.warning("Workflow failed: %s — %s", error_type, error_msg)
                    is_rate_limit = (
                        "429" in error_msg
                        or "rate" in error_msg.lower()
                        or "RateLimit" in error_type
                        or isinstance(
                            getattr(details, "original_error", None), OpenAIRateLimitError
                        )
                    )
                    if is_rate_limit:
                        yield _sse(
                            {
                                "type": "error",
                                "error": {
                                    "code": "RATE_LIMIT",
                                    "message": "The AI service is temporarily busy."
                                    " Please wait a moment and try again.",
                                    "retryable": True,
                                },
                            }
                        )
                    else:
                        yield _sse(
                            {
                                "type": "error",
                                "error": {
                                    "code": "API_ERROR",
                                    "message": "The agent encountered an error. Please try again.",
                                    "retryable": True,
                                },
                            }
                        )
                    return

        finally:
            heartbeat_task.cancel()
            workflow_task.cancel()

        # Flush buffered coordinator text if the coordinator answered directly
        # (no handoff occurred). Drip-feed the buffer as small delta events so
        # the client's streaming UI (character drain + cursor) works correctly.
        # Emitting everything in one delta causes React to batch the content
        # and done events, skipping the streaming animation entirely.
        if coordinator_buf:
            if not generating_announced:
                yield _sse({"type": "agent", "agent": "coordinator"})
                yield _sse({"type": "phase", "phase": "generating"})
                generating_announced = True
            response_text = coordinator_buf
            chunk_size = 40  # characters per drip
            for i in range(0, len(coordinator_buf), chunk_size):
                yield _sse({"type": "delta", "content": coordinator_buf[i : i + chunk_size]})
                await asyncio.sleep(0)  # yield control so each chunk is a separate HTTP frame
        elif not generating_announced:
            yield _sse({"type": "agent", "agent": routed_agent})
            yield _sse({"type": "phase", "phase": "generating"})

        # Debug: log what we got from the workflow
        logger.info(
            "workflow output: structured_result=%s response_text=%r buf_start=%r",
            structured_result is not None,
            response_text[:100] if response_text else None,
            domain_agent_json_buf[:300] if domain_agent_json_buf else None,
        )

        # Build the AgentResponseModel from whatever the workflow produced.
        # Domain agent JSON buffer takes priority over coordinator plain text —
        # when a handoff occurs, response_text holds the coordinator's routing
        # message while domain_agent_json_buf holds the actual answer.
        if structured_result is not None:
            agent_response = structured_result
        elif domain_agent_json_buf:
            agent_response = parse_agent_output(domain_agent_json_buf, routed_agent)
        elif response_text:
            agent_response = parse_agent_output(response_text, routed_agent)
        else:
            # Workflow completed but produced no output — shouldn't normally happen.
            agent_response = AgentResponseModel(
                message="I'm sorry, I couldn't generate a response. Please try again.",
                confidence="low",
                follow_up_suggestions=[
                    "Could you rephrase your question?",
                    "Try again in a moment.",
                ],
            )

        # Quality gate — catch search-skipped or results-ignored failures
        gate_result = run_quality_gate(agent_response, rag_collector, routed_agent)
        agent_response = gate_result.remediated
        if gate_result.check != "passed":
            logger.warning(
                "quality_gate_triggered check=%s agent=%s "
                "rag_count=%d source_count=%d confidence=%s",
                gate_result.check,
                routed_agent,
                len(rag_collector),
                len(agent_response.sources),
                agent_response.confidence,
            )

        # Inject recovered sources if the agent didn't populate them.
        if not agent_response.sources and rag_collector:
            rag_text = "\n\n".join(rag_collector)
            recovered_sources = deduplicate_sources(extract_sources(rag_text))
            if recovered_sources:
                agent_response = agent_response.model_copy(update={"sources": recovered_sources})
                logger.info("injected %d recovered sources into response", len(recovered_sources))

        # Proofread — fix generation artefacts (dropped chars, broken markdown)
        agent_response = await _proofread_response(agent_response)

        # Strip source URLs for agents backed by internal document stores
        # (e.g. SharePoint) to avoid exposing infrastructure details.
        if routed_agent == "hr_agent":
            agent_response = strip_source_urls(agent_response)

        enriched = enrich_agent_response(agent_response)

        # Persist BEFORE final SSE events so messages are saved even if the
        # client disconnects after receiving [DONE] (which would stop the
        # generator and skip any code after the last yield).
        # Messages are saved sequentially (user then assistant) to guarantee
        # correct ordering — parallel inserts could
        # interleave and produce assistant-before-user order.
        user_message = MessageRecord(
            id=str(uuid.uuid4()),
            role="user",
            content=sanitised_message,
            attachments=attachment_records,
            timestamp=datetime.now(UTC),
        )
        assistant_message = MessageRecord(
            id=message_id,
            role="assistant",
            content=enriched.message,
            agent=routed_agent,
            response=agent_response,
            timestamp=datetime.now(UTC),
        )
        if db_available:
            ok = await _persist_message(
                conversation_service, conversation_id, user_id, user_message
            )
            if ok:
                ok = await _persist_message(
                    conversation_service, conversation_id, user_id, assistant_message
                )
            if ok:
                await _update_last_active_agent(
                    conversation_service, conversation_id, user_id, routed_agent
                )
            else:
                db_available = False

        yield _sse({"type": "phase", "phase": "verifying"})
        yield _sse({"type": "confidence", "breakdown": enriched.confidence.model_dump()})
        yield _sse({"type": "verification", "result": enriched.verification.model_dump()})

        if not db_available:
            yield _sse(
                {
                    "type": "warning",
                    "code": "db-unavailable",
                    "message": (
                        "Your message may not have been saved."
                        " Conversation history could be incomplete."
                    ),
                }
            )

        yield _sse(
            {
                "type": "done",
                "response": enriched.model_dump(mode="json"),
                "conversation_id": conversation_id,
            }
        )
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/chat/{conversation_id}")
@limiter.limit("60/minute")  # pyright: ignore[reportUnknownMemberType,reportUntypedFunctionDecorator]
async def get_conversation(conversation_id: str, request: Request) -> dict[str, object]:
    """Load a conversation by ID."""
    conversation_service = request.app.state.conversation_service
    if conversation_service is None:
        raise HTTPException(status_code=503, detail="Conversation history not available")
    user = await get_current_user(request)
    user_id = user.user_id

    conversation = await conversation_service.get_conversation(conversation_id, user_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return conversation.model_dump(mode="json")


@router.delete("/chat/{conversation_id}")
@limiter.limit("20/minute")  # pyright: ignore[reportUnknownMemberType,reportUntypedFunctionDecorator]
async def delete_conversation(conversation_id: str, request: Request) -> dict[str, object]:
    """Delete a conversation."""
    conversation_service = request.app.state.conversation_service
    if conversation_service is None:
        raise HTTPException(status_code=503, detail="Conversation history not available")
    user = await get_current_user(request)
    user_id = user.user_id

    deleted = await conversation_service.delete_conversation(conversation_id, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return {"status": "deleted", "conversation_id": conversation_id}


@router.post("/chat/{conversation_id}/feedback")
@limiter.limit("30/minute")  # pyright: ignore[reportUnknownMemberType,reportUntypedFunctionDecorator]
async def submit_feedback(
    conversation_id: str,
    feedback: FeedbackRecord,
    request: Request,
) -> dict[str, object]:
    """Submit feedback for a message in a conversation."""
    conversation_service = request.app.state.conversation_service
    if conversation_service is None:
        raise HTTPException(status_code=503, detail="Conversation history not available")
    user = await get_current_user(request)
    user_id = user.user_id

    conversation = await conversation_service.get_conversation(conversation_id, user_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    await conversation_service.add_feedback(conversation_id, user_id, feedback)

    return {"status": "received", "conversation_id": conversation_id}
