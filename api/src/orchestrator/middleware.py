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

        start = time.perf_counter()
        await call_next()
        duration_ms = (time.perf_counter() - start) * 1000

        result = str(context.result) if context.result is not None else ""

        # Count sources in the output (each starts with "=== SOURCE N ===").
        source_count = result.count("=== SOURCE ")

        logger.info(
            "RAG tool completed: %.1fms, %d sources returned",
            duration_ms,
            source_count,
        )

        # Collect for downstream source recovery in chat routes.
        if result and source_count > 0:
            with contextlib.suppress(LookupError):
                rag_results_collector.get().append(result)
