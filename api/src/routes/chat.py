import asyncio
import json
import logging
import re
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from openai import RateLimitError as OpenAIRateLimitError

from src.agents._output import (
    _deduplicate_sources,
    _sanitize_agent_response,
    extract_sources,
    parse_agent_output,
)
from src.middleware.auth import get_current_user
from src.middleware.error_handler import LLM_TIMEOUT_SECONDS, LLMTimeoutError
from src.middleware.input_validation import validate_message
from src.middleware.rate_limit import limiter
from src.models.agent import (
    AgentResponseModel,
    RoutingMetadata,
    Source,
    enrich_agent_response,
)
from src.models.chat import ChatRequest, ChatResponse
from src.models.conversation import FeedbackRecord, MessageRecord
from src.orchestrator.builder import SYNTHESIZE_SUFFIX
from src.orchestrator.history import current_conversation_id, current_user_id
from src.rag.tools import rag_results_collector

logger = logging.getLogger(__name__)

# Number of heartbeat ticks (5s each) before emitting phase(waiting).
_HEARTBEAT_WAIT_TICKS = 2  # 10 seconds

router = APIRouter(prefix="/api/v1", tags=["chat"])


async def _update_last_active_agent(
    conversation_service: object,
    conversation_id: str,
    user_id: str,
    agent_name: str,
) -> None:
    """Update metadata.last_active_agent on the conversation document."""
    try:
        await conversation_service.update_last_active_agent(  # type: ignore[union-attr]
            conversation_id, user_id, agent_name
        )
    except Exception:
        # Cosmos can raise various exceptions (CosmosHttpResponseError, network errors, etc.)
        # — never let a metadata update failure break the request.
        logger.warning(
            "Could not update last_active_agent for conversation %s",
            conversation_id,
            exc_info=True,
        )


async def _persist_message(
    conversation_service: object,
    conversation_id: str,
    user_id: str,
    message: MessageRecord,
) -> bool:
    """Attempt to persist a message, returning False on Cosmos failure."""
    try:
        await conversation_service.add_message(conversation_id, user_id, message)  # type: ignore[union-attr]
        return True
    except Exception:
        # Cosmos can raise various exceptions — never let persistence failure break the request.
        logger.warning(
            "Cosmos DB unavailable — could not persist message %s for conversation %s",
            message.id,
            conversation_id,
            exc_info=True,
        )
        return False


async def _run_workflow(
    workflow_factory: object,
    message: str,
    *,
    conversation_id: str,
    user_id: str,
) -> tuple[str, AgentResponseModel | None, str, list[str]]:
    """Run the AI workflow with a timeout.

    Returns (response_text, structured_result, routed_agent, rag_outputs).
    Raises LLMTimeoutError if the workflow exceeds LLM_TIMEOUT_SECONDS.
    """
    # Set context vars so ConversationHistoryProvider can read them
    # without passing them through workflow options (which leak to the LLM client).
    current_conversation_id.set(conversation_id)
    current_user_id.set(user_id)
    rag_collector: list[str] = []
    rag_results_collector.set(rag_collector)

    # Build a fresh Workflow per request — agent_framework Workflow is stateful
    # and raises RuntimeError if run() is called while another run is in progress.
    workflow = workflow_factory()  # type: ignore[operator]

    async def _execute() -> tuple[str, AgentResponseModel | None, str]:
        response_text = ""
        structured_result: AgentResponseModel | None = None
        routed_agent = "coordinator"
        async for event in workflow.run(
            message,
            stream=True,
        ):  # type: ignore[union-attr]
            if event.type == "handoff_sent":
                routed_agent = event.data.target
            elif event.type == "output":
                if hasattr(event.data, "value") and isinstance(
                    event.data.value, AgentResponseModel
                ):
                    structured_result = _sanitize_agent_response(event.data.value)
                elif hasattr(event.data, "text") and event.data.text:
                    response_text += event.data.text
        return response_text, structured_result, routed_agent

    try:
        result = await asyncio.wait_for(_execute(), timeout=LLM_TIMEOUT_SECONDS)
        return (*result, rag_collector)
    except TimeoutError as err:
        raise LLMTimeoutError(
            f"LLM workflow timed out after {LLM_TIMEOUT_SECONDS}s"
        ) from err


@router.post("/chat")
@limiter.limit("10/minute")
async def chat(body: ChatRequest, request: Request) -> JSONResponse:
    """Send a message and receive an AI response."""
    workflow = request.app.state.workflow
    conversation_service = request.app.state.conversation_service
    user = await get_current_user(request)
    user_id = user.user_id

    if workflow is None:
        raise HTTPException(
            status_code=503,
            detail="AI workflow not available. Azure OpenAI endpoint not configured.",
        )

    # Validate & sanitise input
    sanitised_message = validate_message(body.message)

    # Create or load conversation — handle Cosmos unavailability
    cosmos_available = True
    conversation_id = body.conversation_id or str(uuid.uuid4())

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
        # Cosmos can raise various exceptions — degrade gracefully.
        logger.warning(
            "Cosmos DB unavailable — continuing without persistence",
            exc_info=True,
        )
        cosmos_available = False

    # Run the workflow (with timeout)
    rag_available = True
    try:
        response_text, structured_result, routed_agent, rag_outputs = await _run_workflow(
            workflow,
            sanitised_message,
            conversation_id=conversation_id,
            user_id=user_id,
        )
    except LLMTimeoutError:
        raise
    except Exception:
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

    # Inject sources recovered from the RAG tool output if the agent didn't populate them.
    if not agent_response.sources and rag_outputs:
        rag_text = "\n\n".join(rag_outputs)
        recovered = _deduplicate_sources(extract_sources(rag_text))
        if recovered:
            agent_response = agent_response.model_copy(update={"sources": recovered})

    routing = RoutingMetadata(
        routed_by="coordinator",
        primary_agent=routed_agent,
    )

    # Save user message (after workflow so history provider doesn't see it as prior context)
    user_message = MessageRecord(
        id=str(uuid.uuid4()),
        role="user",
        content=sanitised_message,
        timestamp=datetime.now(UTC),
    )
    if cosmos_available:
        persisted = await _persist_message(
            conversation_service, conversation_id, user_id, user_message
        )
        if not persisted:
            cosmos_available = False

    # Save assistant message
    assistant_message = MessageRecord(
        id=message_id,
        role="assistant",
        content=agent_response.message,
        agent=routed_agent,
        response=agent_response,
        timestamp=datetime.now(UTC),
    )
    if cosmos_available:
        await _persist_message(
            conversation_service, conversation_id, user_id, assistant_message
        )
        # Track the last active agent for topic-switching detection
        await _update_last_active_agent(
            conversation_service, conversation_id, user_id, routed_agent
        )

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
    if not cosmos_available:
        response.headers["X-Surf-Warning"] = "cosmos-unavailable"
    return response


def _sse(data: dict) -> str:
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
        self._buf = ""        # pre-marker accumulation
        self._in_value = False
        self._escape = False
        self._done = False
        self._guard_buf = ""  # buffer first N chars for pollution check
        self._suppressed = False  # True once pollution is detected

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
            remainder = self._buf[m.end():]
            self._buf = ""
            return self._guarded_read(remainder)

        return self._guarded_read(chunk)

    def _guarded_read(self, s: str) -> str:
        """Read string chars, applying the pollution guard then normal extraction."""
        if self._suppressed:
            return ""

        # Still buffering the guard window.
        if len(self._guard_buf) < self._GUARD_LEN:
            needed = self._GUARD_LEN - len(self._guard_buf)
            chars, remaining = s[:needed], s[needed:]

            out_inner, done = self._read_string(chars)
            self._guard_buf += out_inner
            if done:
                self._done = True
                # Short message that ended before guard window — not pollution.
                return self._guard_buf

            if len(self._guard_buf) >= self._GUARD_LEN:
                if self._guard_buf.startswith(self._SOURCE_POLLUTION_PREFIX):
                    self._suppressed = True
                    logger.warning(
                        "_MessageFieldExtractor: source pollution detected — suppressing stream"
                    )
                    return ""
                # Guard passed — emit buffered chars then continue with remainder.
                flushed = self._guard_buf
                self._guard_buf = ""
                out_rest, done = self._read_string(remaining)
                if done:
                    self._done = True
                return flushed + out_rest

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
            if self._escape:
                out.append(
                    {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\",
                     "/": "/", "b": "\b", "f": "\f", "u": ""}.get(ch, ch)
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
@limiter.limit("10/minute")
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
    workflow = request.app.state.workflow
    conversation_service = request.app.state.conversation_service
    user = await get_current_user(request)
    user_id = user.user_id

    if workflow is None:
        async def _no_workflow() -> AsyncGenerator[str, None]:
            yield _sse({"type": "error", "error": {
                "code": "API_ERROR",
                "message": "AI workflow not available. Azure OpenAI endpoint not configured.",
                "retryable": False,
            }})

        return StreamingResponse(_no_workflow(), media_type="text/event-stream")

    sanitised_message = validate_message(body.message)
    cosmos_available = True
    conversation_id = body.conversation_id or str(uuid.uuid4())

    try:
        if body.conversation_id:
            conversation = await conversation_service.get_conversation(
                body.conversation_id, user_id
            )
            if conversation is None:
                async def _not_found() -> AsyncGenerator[str, None]:
                    yield _sse({"type": "error", "error": {
                        "code": "API_ERROR",
                        "message": "Conversation not found",
                        "retryable": False,
                    }})

                return StreamingResponse(_not_found(), media_type="text/event-stream")
            conversation_id = body.conversation_id
        else:
            conversation = await conversation_service.create_conversation(user_id)
            conversation_id = conversation.id
    except (ConnectionError, TimeoutError, OSError):
        logger.warning("Cosmos DB unavailable — continuing without persistence", exc_info=True)
        cosmos_available = False

    async def generate() -> AsyncGenerator[str, None]:
        nonlocal cosmos_available

        current_conversation_id.set(conversation_id)
        current_user_id.set(user_id)
        rag_collector: list[str] = []
        rag_results_collector.set(rag_collector)

        yield _sse({"type": "phase", "phase": "thinking"})

        wf = workflow()  # type: ignore[operator]
        routed_agent = "coordinator"
        structured_result: AgentResponseModel | None = None
        response_text = ""
        domain_agent_json_buf = ""  # accumulates raw JSON for domain agents
        recovered_sources: list[Source] = []
        extractor = _MessageFieldExtractor()
        generating_announced = False
        message_id = str(uuid.uuid4())

        # Queue used to multiplex workflow events and heartbeat ticks.
        # Items: ('event', event_obj) | ('heartbeat',) | ('done',) | ('error', exc)
        queue: asyncio.Queue[tuple] = asyncio.Queue()

        async def _run_workflow_into_queue() -> None:
            try:
                async with asyncio.timeout(LLM_TIMEOUT_SECONDS):
                    async for event in wf.run(sanitised_message, stream=True):  # type: ignore[union-attr]
                        await queue.put(("event", event))
                await queue.put(("done",))
            except Exception as exc:
                await queue.put(("error", exc))

        async def _heartbeat() -> None:
            """Send a tick every 5 seconds so the main loop can emit keepalives."""
            while True:
                await asyncio.sleep(5)
                await queue.put(("heartbeat",))

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
                    exc = item[1]
                    if isinstance(exc, TimeoutError):
                        yield _sse({"type": "error", "error": {
                            "code": "TIMEOUT",
                            "message": "Request timed out. Please try again.",
                            "retryable": True,
                        }})
                    elif isinstance(exc, OpenAIRateLimitError):
                        logger.warning(
                            "LLM rate limited in SSE stream — quota exhausted after retries"
                        )
                        yield _sse({"type": "error", "error": {
                            "code": "RATE_LIMIT",
                            "message": "The AI service is temporarily busy."
                            " Please wait a moment and try again.",
                            "retryable": True,
                        }})
                    else:
                        logger.warning("Workflow error in SSE stream", exc_info=exc)
                        yield _sse({"type": "error", "error": {
                            "code": "API_ERROR",
                            "message": "The agent encountered an error. Please try again.",
                            "retryable": True,
                        }})
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
                event = item[1]

                logger.debug("event: type=%s data_type=%s", event.type, type(event.data).__name__)
                if event.type == "handoff_sent":
                    routed_agent = event.data.target
                    logger.debug("handoff_sent: target=%s", routed_agent)
                    # Only announce the first handoff (coordinator → domain search agent).
                    # The internal handoff (search → synthesize) is an implementation detail;
                    # suppressing it keeps the SSE stream clean for clients.
                    if not routed_agent.endswith(SYNTHESIZE_SUFFIX):
                        yield _sse({"type": "agent", "agent": routed_agent})
                        yield _sse({"type": "phase", "phase": "generating"})
                        generating_announced = True
                        heartbeat_count = 0  # reset so we don't re-emit "waiting" mid-generation
                    else:
                        # Search → synthesize handoff: extract sources from the search-agent
                        # echo text before discarding it, reset for the synthesise agent's JSON.
                        logger.debug(
                            "internal handoff → %s: resetting buf (was %d chars)",
                            routed_agent,
                            len(domain_agent_json_buf),
                        )
                        recovered_sources = extract_sources(domain_agent_json_buf)
                        domain_agent_json_buf = ""
                        extractor = _MessageFieldExtractor()  # fresh extractor for JSON stream

                elif event.type == "output":
                    data = event.data

                    # Final AgentResponse — carries the fully parsed structured object.
                    if hasattr(data, "value") and isinstance(data.value, AgentResponseModel):
                        structured_result = _sanitize_agent_response(data.value)
                        continue

                    # Streaming token chunk (AgentResponseUpdate).
                    chunk = data.text if hasattr(data, "text") else None
                    if not chunk:
                        continue

                    if routed_agent == "coordinator":
                        # Plain-text coordinator response — stream tokens directly.
                        if not generating_announced:
                            yield _sse({"type": "agent", "agent": "coordinator"})
                            yield _sse({"type": "phase", "phase": "generating"})
                            generating_announced = True
                        response_text += chunk
                        yield _sse({"type": "delta", "content": chunk})

                    else:
                        # Domain agent — JSON output, extract the 'message' field value.
                        domain_agent_json_buf += chunk
                        extracted = extractor.feed(chunk)
                        if extracted:
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
                        yield _sse({"type": "error", "error": {
                            "code": "RATE_LIMIT",
                            "message": "The AI service is temporarily busy."
                            " Please wait a moment and try again.",
                            "retryable": True,
                        }})
                    else:
                        yield _sse({"type": "error", "error": {
                            "code": "API_ERROR",
                            "message": "The agent encountered an error. Please try again.",
                            "retryable": True,
                        }})
                    return

        finally:
            heartbeat_task.cancel()
            workflow_task.cancel()

        # Announce agent/generating phase for any path that didn't handoff.
        if not generating_announced:
            yield _sse({"type": "agent", "agent": routed_agent})
            yield _sse({"type": "phase", "phase": "generating"})

        # Debug: log what we got from the workflow
        logger.info("workflow output: structured_result=%s response_text=%r buf_start=%r",
                    structured_result is not None, response_text[:100] if response_text else None,
                    domain_agent_json_buf[:300] if domain_agent_json_buf else None)

        # Build the AgentResponseModel from whatever the workflow produced.
        if structured_result is not None:
            agent_response = structured_result
        elif response_text:
            agent_response = parse_agent_output(response_text, routed_agent)
        elif domain_agent_json_buf:
            # Streaming delivered JSON chunks but no final output event — parse the buffer.
            agent_response = parse_agent_output(domain_agent_json_buf, routed_agent)
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

        # Inject recovered sources if the agent didn't populate them.
        # Try handoff-captured sources first, then fall back to RAG tool output.
        if not agent_response.sources:
            if not recovered_sources and rag_collector:
                rag_text = "\n\n".join(rag_collector)
                recovered_sources = extract_sources(rag_text)
            recovered_sources = _deduplicate_sources(recovered_sources)
            if recovered_sources:
                agent_response = agent_response.model_copy(update={"sources": recovered_sources})
                logger.info("injected %d recovered sources into response", len(recovered_sources))

        enriched = enrich_agent_response(agent_response)

        yield _sse({"type": "phase", "phase": "verifying"})
        yield _sse({"type": "confidence", "breakdown": enriched.confidence.model_dump()})
        yield _sse({"type": "verification", "result": enriched.verification.model_dump()})
        yield _sse({
            "type": "done",
            "response": enriched.model_dump(mode="json"),
            "conversation_id": conversation_id,
        })
        yield "data: [DONE]\n\n"

        # Persist after streaming completes — Cosmos errors here are non-fatal.
        # User message saved after the workflow run so the history provider
        # doesn't inject it as prior context during the run.
        user_message = MessageRecord(
            id=str(uuid.uuid4()),
            role="user",
            content=sanitised_message,
            timestamp=datetime.now(UTC),
        )
        if cosmos_available:
            persisted = await _persist_message(
                conversation_service, conversation_id, user_id, user_message
            )
            if not persisted:
                cosmos_available = False

        assistant_message = MessageRecord(
            id=message_id,
            role="assistant",
            content=enriched.message,
            agent=routed_agent,
            response=agent_response,
            timestamp=datetime.now(UTC),
        )
        if cosmos_available:
            await _persist_message(
                conversation_service, conversation_id, user_id, assistant_message
            )
            await _update_last_active_agent(
                conversation_service, conversation_id, user_id, routed_agent
            )

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/chat/{conversation_id}")
@limiter.limit("60/minute")
async def get_conversation(conversation_id: str, request: Request) -> dict:
    """Load a conversation by ID."""
    conversation_service = request.app.state.conversation_service
    user = await get_current_user(request)
    user_id = user.user_id

    conversation = await conversation_service.get_conversation(conversation_id, user_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return conversation.model_dump(mode="json")


@router.delete("/chat/{conversation_id}")
@limiter.limit("20/minute")
async def delete_conversation(conversation_id: str, request: Request) -> dict:
    """Delete a conversation."""
    conversation_service = request.app.state.conversation_service
    user = await get_current_user(request)
    user_id = user.user_id

    deleted = await conversation_service.delete_conversation(conversation_id, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return {"status": "deleted", "conversation_id": conversation_id}


@router.post("/chat/{conversation_id}/feedback")
@limiter.limit("30/minute")
async def submit_feedback(
    conversation_id: str, feedback: FeedbackRecord, request: Request
) -> dict:
    """Submit feedback for a message in a conversation."""
    conversation_service = request.app.state.conversation_service
    user = await get_current_user(request)
    user_id = user.user_id

    conversation = await conversation_service.get_conversation(conversation_id, user_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    await conversation_service.add_feedback(conversation_id, user_id, feedback)

    return {"status": "received", "conversation_id": conversation_id}
