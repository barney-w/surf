import asyncio
import logging
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from fastapi import APIRouter, HTTPException, Request

if TYPE_CHECKING:
    from agent_framework import Workflow, WorkflowEvent
from fastapi.responses import JSONResponse, StreamingResponse
from openai import RateLimitError as OpenAIRateLimitError

from src.agents._output import (
    parse_agent_output,
    sanitize_agent_response,
)
from src.middleware.auth import UserContext, get_current_user
from src.middleware.error_handler import LLM_TIMEOUT_SECONDS, LLMTimeoutError
from src.middleware.rate_limit import limiter
from src.models.agent import (
    AgentResponseModel,
    RoutingMetadata,
    enrich_agent_response,
)
from src.models.chat import ChatRequest, ChatResponse
from src.models.conversation import FeedbackRecord
from src.rag.tools import _search_overrides, parse_debug_overrides, search_debug_info
from src.middleware.telemetry import record_token_usage
from src.orchestrator.builder import token_usage_collector
from src.services.chat_service import (
    persist_exchange,
    prepare_chat_request,
    run_workflow,
    set_attachments_context,
    setup_context_vars,
)
from src.services.response_pipeline import process_agent_response
from src.services.streaming import (
    HEARTBEAT_WAIT_TICKS,
    PROMPT_TOO_LONG_MESSAGE,
    MessageFieldExtractor,
    is_prompt_too_long,
    sse,
)

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


router = APIRouter(prefix="/api/v1", tags=["chat"])


@router.get("/conversations")
@limiter.limit("30/minute")  # pyright: ignore[reportUnknownMemberType,reportUntypedFunctionDecorator]
async def list_conversations(
    request: Request,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, object]]:
    """List conversations for the current user."""
    conversation_service = request.app.state.conversation_service
    if conversation_service is None:
        raise HTTPException(status_code=503, detail="Conversation history not available")
    user = await get_current_user(request)
    summaries = await conversation_service.list_conversations(
        user.user_id, limit=min(limit, 50), offset=max(offset, 0)
    )
    return [s.model_dump(mode="json") for s in summaries]


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

    ctx = await prepare_chat_request(body, user_id, conversation_service)

    # Apply debug overrides from X-Surf-Debug-* request headers.
    overrides = parse_debug_overrides(dict(request.headers))
    if overrides:
        _search_overrides.set(overrides)

    # Run the workflow (with timeout)
    rag_available = True
    try:
        response_text, structured_result, routed_agent, rag_outputs = await run_workflow(
            workflow,
            ctx.sanitised_message,
            ctx=ctx,
        )
    except LLMTimeoutError:
        raise
    except Exception as exc:
        if is_prompt_too_long(exc):
            raise HTTPException(
                status_code=413,
                detail=PROMPT_TOO_LONG_MESSAGE,
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

    # Post-processing pipeline (quality gate, source recovery, proofread, URL stripping)
    agent_response, _ = await process_agent_response(agent_response, rag_outputs, routed_agent)

    routing = RoutingMetadata(
        routed_by="coordinator",
        primary_agent=routed_agent,
    )

    # Persist user message, assistant message, and update last_active_agent.
    if ctx.db_available:
        ok = await persist_exchange(
            ctx,
            ctx.sanitised_message,
            agent_response.message,
            agent_response,
            routed_agent,
            message_id,
        )
        if not ok:
            ctx.db_available = False

    chat_response = ChatResponse(
        conversation_id=ctx.conversation_id,
        message_id=message_id,
        agent=routed_agent,
        response=agent_response,
        routing=routing,
        created_at=datetime.now(UTC),
    )

    response = JSONResponse(
        content=chat_response.model_dump(mode="json"),
    )
    if not ctx.db_available:
        response.headers["X-Surf-Warning"] = "db-unavailable"
    return response


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

    def _sse_error(code: str, msg: str, *, retryable: bool = True) -> str:
        return sse(
            {"type": "error", "error": {"code": code, "message": msg, "retryable": retryable}}
        )

    async def _stream_error(
        code: str, msg: str, *, retryable: bool = False
    ) -> AsyncGenerator[str, None]:
        yield _sse_error(code, msg, retryable=retryable)

    workflow = _resolve_workflow_factory(body, request, user)
    if workflow is None:
        return StreamingResponse(
            _stream_error("API_ERROR", "AI workflow not available. Endpoint not configured."),
            media_type="text/event-stream",
        )

    try:
        ctx = await prepare_chat_request(body, user_id, conversation_service)
    except HTTPException as exc:
        return StreamingResponse(
            _stream_error("API_ERROR", str(exc.detail)),
            media_type="text/event-stream",
        )

    # Parse debug overrides once so the generator can re-apply them.
    stream_overrides = parse_debug_overrides(dict(request.headers))
    # Gate the debug SSE event behind the X-Surf-Debug request header.
    emit_debug = "x-surf-debug" in {k.lower() for k in request.headers.keys()}

    async def generate() -> AsyncGenerator[str, None]:
        rag_collector = setup_context_vars(ctx)
        # Re-set attachments in the generator's context (async generators run in
        # their own context copy).
        set_attachments_context(body)

        # Re-apply debug overrides inside the generator's context copy.
        if stream_overrides:
            _search_overrides.set(stream_overrides)

        yield sse({"type": "phase", "phase": "thinking"})

        from src.middleware.logging import ctx_request_id

        request_id = ctx_request_id.get()
        if request_id:
            yield sse({"type": "meta", "request_id": request_id})

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
        extractor = MessageFieldExtractor()
        generating_announced = False
        message_id = str(uuid.uuid4())

        # Queue used to multiplex workflow events and heartbeat ticks.
        # Items: ('event', event_obj) | ('heartbeat',) | ('done',) | ('error', exc)
        queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()

        async def _run_workflow_into_queue() -> None:
            try:
                async with asyncio.timeout(LLM_TIMEOUT_SECONDS):
                    async for event in wf.run(ctx.sanitised_message, stream=True):  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType]
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
                    if is_prompt_too_long(exc):
                        yield _sse_error(
                            "PROMPT_TOO_LONG", PROMPT_TOO_LONG_MESSAGE, retryable=False
                        )
                    elif isinstance(exc, TimeoutError):
                        yield _sse_error("TIMEOUT", "Request timed out. Please try again.")
                    elif isinstance(exc, OpenAIRateLimitError):
                        logger.warning(
                            "LLM rate limited in SSE stream — quota exhausted after retries"
                        )
                        yield _sse_error(
                            "RATE_LIMIT",
                            "The AI service is temporarily busy."
                            " Please wait a moment and try again.",
                        )
                    else:
                        logger.warning("Workflow error in SSE stream", exc_info=exc)
                        yield _sse_error(
                            "API_ERROR",
                            "The agent encountered an error. Please try again.",
                        )
                    return

                if kind == "heartbeat":
                    heartbeat_count += 1
                    if heartbeat_count == HEARTBEAT_WAIT_TICKS and not generating_announced:
                        # 10 seconds elapsed with no output — tell the client to show
                        # a "still working" message (e.g. during a 429 retry window).
                        yield sse({"type": "phase", "phase": "waiting"})
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
                    yield sse({"type": "agent", "agent": routed_agent})
                    yield sse({"type": "phase", "phase": "generating"})
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
                            extractor = MessageFieldExtractor()
                            domain_agent_json_buf = ""
                            yield sse({"type": "delta_reset"})
                        yield sse({"type": "phase", "phase": "retrieving"})

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
                                yield sse({"type": "phase", "phase": "generating"})
                                generating_announced = True
                            yield sse({"type": "delta", "content": extracted})

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
                        yield _sse_error(
                            "RATE_LIMIT",
                            "The AI service is temporarily busy."
                            " Please wait a moment and try again.",
                        )
                    else:
                        yield _sse_error(
                            "API_ERROR",
                            "The agent encountered an error. Please try again.",
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
                yield sse({"type": "agent", "agent": "coordinator"})
                yield sse({"type": "phase", "phase": "generating"})
                generating_announced = True
            response_text = coordinator_buf
            chunk_size = 40  # characters per drip
            for i in range(0, len(coordinator_buf), chunk_size):
                yield sse({"type": "delta", "content": coordinator_buf[i : i + chunk_size]})
                await asyncio.sleep(0)  # yield control so each chunk is a separate HTTP frame
        elif not generating_announced:
            yield sse({"type": "agent", "agent": routed_agent})
            yield sse({"type": "phase", "phase": "generating"})

        # Debug: log what we got from the workflow
        from src.config.settings import get_settings as _get_settings
        if _get_settings().trace_prompt_content:
            logger.info(
                "workflow output: structured_result=%s response_text=%r buf_start=%r",
                structured_result is not None,
                response_text[:100] if response_text else None,
                domain_agent_json_buf[:300] if domain_agent_json_buf else None,
            )
        else:
            logger.info(
                "workflow output: structured_result=%s response_text_len=%d buf_len=%d",
                structured_result is not None,
                len(response_text) if response_text else 0,
                len(domain_agent_json_buf) if domain_agent_json_buf else 0,
            )

        gate_result = None
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

        # Post-processing pipeline (quality gate, source recovery, proofread, URL stripping)
        agent_response, gate_result = await process_agent_response(
            agent_response, rag_collector, routed_agent
        )

        enriched = enrich_agent_response(agent_response)

        # Persist BEFORE final SSE events so messages are saved even if the
        # client disconnects after receiving [DONE] (which would stop the
        # generator and skip any code after the last yield).
        if ctx.db_available:
            ok = await persist_exchange(
                ctx,
                ctx.sanitised_message,
                enriched.message,
                agent_response,
                routed_agent,
                message_id,
            )
            if not ok:
                ctx.db_available = False

        yield sse({"type": "phase", "phase": "verifying"})
        yield sse({"type": "confidence", "breakdown": enriched.confidence.model_dump()})
        yield sse({"type": "verification", "result": enriched.verification.model_dump()})

        # Emit token usage summary before the done event.
        try:
            usages = token_usage_collector.get()
            total_input = sum(u.input_tokens for u in usages)
            total_output = sum(u.output_tokens for u in usages)
            yield sse({
                "type": "usage",
                "input_tokens": total_input,
                "output_tokens": total_output,
                "calls": [
                    {"model": u.model_id, "input": u.input_tokens, "output": u.output_tokens}
                    for u in usages
                ],
            })
            # Record to OpenTelemetry counter.
            record_token_usage(total_input, total_output, agent_name=routed_agent or "unknown")
        except LookupError:
            pass

        # Emit debug event with search details and quality gate outcome
        # when the X-Surf-Debug request header is present.
        if emit_debug:
            try:
                debug = search_debug_info.get()
                if debug:
                    yield sse({
                        "type": "debug",
                        "search": {
                            "original_query": debug.original_query,
                            "rewritten_query": debug.rewritten_query,
                            "strategies_used": debug.strategies_used,
                            "results_per_strategy": debug.results_per_strategy,
                            "total_results": debug.total_results,
                            "tier_counts": debug.tier_counts,
                        },
                        "quality_gate": gate_result.check if gate_result else "not_run",
                        "agent": routed_agent,
                    })
            except LookupError:
                pass

        if not ctx.db_available:
            yield sse(
                {
                    "type": "warning",
                    "code": "db-unavailable",
                    "message": (
                        "Your message may not have been saved."
                        " Conversation history could be incomplete."
                    ),
                }
            )

        yield sse(
            {
                "type": "done",
                "response": enriched.model_dump(mode="json"),
                "conversation_id": ctx.conversation_id,
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
