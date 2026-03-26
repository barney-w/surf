"""Agent Framework middleware for agent execution concerns.

This module contains AF-level middleware (as opposed to FastAPI HTTP middleware).
AF middleware intercepts agent runs, function/tool invocations, and chat client
calls — operating at the agent execution layer rather than the HTTP layer.
"""

import contextlib
import logging
import time
from collections.abc import Awaitable, Callable

from agent_framework import FunctionInvocationContext, FunctionMiddleware

from src.middleware.langfuse_utils import get_langfuse
from src.middleware.telemetry import rag_results_count, rag_search_duration
from src.rag.tools import rag_results_collector

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
        if langfuse:
            try:
                obs = langfuse.start_as_current_observation(
                    name="search_knowledge_base",
                    as_type="retriever",
                    input={"query": str(context.arguments) if context.arguments else ""},
                ).__enter__()
            except Exception:
                obs = None
        else:
            obs = None

        start = time.perf_counter()
        await call_next()
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
            try:
                obs.update(
                    output={
                        "source_count": source_count,
                        "duration_ms": round(duration_ms, 1),
                        "has_infra_error": "SEARCH_INFRASTRUCTURE_ERROR:" in result,
                    }
                )
                obs.__exit__(None, None, None)
            except Exception:
                pass

        # Collect ALL tool outputs (not just those with sources) so the
        # quality gate can distinguish skipped vs empty vs infra-error.
        if result:
            with contextlib.suppress(LookupError):
                rag_results_collector.get().append(result)
