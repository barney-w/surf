"""Agent Framework middleware for agent execution concerns.

This module contains AF-level middleware (as opposed to FastAPI HTTP middleware).
AF middleware intercepts agent runs, function/tool invocations, and chat client
calls — operating at the agent execution layer rather than the HTTP layer.
"""

import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import ExitStack

from agent_framework import FunctionInvocationContext, FunctionMiddleware

from src.middleware.langfuse_utils import get_langfuse
from src.middleware.telemetry import rag_results_count, rag_search_duration

logger = logging.getLogger(__name__)


class RAGCollectorMiddleware(FunctionMiddleware):
    """Collect RAG tool outputs and log invocation metrics.

    Replaces the previous pattern of appending to the ``rag_results_collector``
    context variable from inside the tool function itself.  Moving collection
    into middleware keeps the tool function pure (search-only) and gives us a
    single interception point for timing, logging, and result collection.
    """

    async def process(
        self,
        context: FunctionInvocationContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        if context.function.name != "search_knowledge_base":
            await call_next()
            return

        # Langfuse retriever span for search_knowledge_base invocations.
        langfuse = get_langfuse()
        lf_stack = ExitStack()
        obs = None
        if langfuse:
            with contextlib.suppress(Exception):
                obs = lf_stack.enter_context(
                    langfuse.start_as_current_observation(
                        name="search_knowledge_base",
                        as_type="retriever",
                        input={"query": str(context.arguments) if context.arguments else ""},
                    )
                )

        start = time.perf_counter()
        try:
            await call_next()
        finally:
            duration_ms = (time.perf_counter() - start) * 1000

            result = str(context.result) if context.result is not None else ""

            # Count sources in the output (each starts with "=== SOURCE N ===").
            source_count = result.count("=== SOURCE ")

            # Detect infrastructure errors
            if "SEARCH_INFRASTRUCTURE_ERROR:" in result:
                logger.error(
                    "RAG tool returned infrastructure error",
                    extra={
                        "event": "rag_tool_infrastructure_error",
                        "duration_ms": round(duration_ms, 1),
                    },
                )
            elif source_count > 0:
                logger.info(
                    "RAG tool completed: %.1fms, %d sources returned",
                    duration_ms,
                    source_count,
                )
            else:
                logger.info(
                    "RAG tool completed: %.1fms, no sources found",
                    duration_ms,
                )

            rag_status = "error" if "SEARCH_INFRASTRUCTURE_ERROR:" in result else "ok"
            rag_search_duration.record(duration_ms / 1000, {"status": rag_status})
            rag_results_count.record(source_count)

            if obs:
                with contextlib.suppress(Exception):
                    obs.update(
                        output={
                            "source_count": source_count,
                            "duration_ms": round(duration_ms, 1),
                            "has_infra_error": "SEARCH_INFRASTRUCTURE_ERROR:" in result,
                        }
                    )
            lf_stack.close()

        # NOTE: Result collection is handled inside the tool function itself
        # (via _collect_rag_output) to survive agent cloning during handoff
        # orchestration, which drops FunctionMiddleware.  This middleware
        # remains for observability (timing, source counting, Langfuse spans).
