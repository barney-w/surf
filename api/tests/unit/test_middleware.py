"""Tests for Agent Framework middleware (orchestrator/middleware.py)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.orchestrator.middleware import RAGCollectorMiddleware
from src.rag.tools import rag_results_collector


def _make_context(
    func_name: str = "search_knowledge_base",
    result: str | None = None,
) -> MagicMock:
    """Build a minimal FunctionInvocationContext-like mock."""
    ctx = MagicMock()
    ctx.function.name = func_name
    ctx.result = result
    return ctx


class TestRAGCollectorMiddleware:
    @pytest.mark.asyncio
    async def test_collects_rag_output(self):
        """Middleware should append tool result to the rag_results_collector."""
        collector: list[str] = []
        rag_results_collector.set(collector)

        rag_output = (
            '=== SOURCE 1 ===\ntitle: "Leave Policy"\nCONTENT:\nSome content\n=== END SOURCE 1 ==='
        )
        ctx = _make_context()

        async def call_next():
            ctx.result = rag_output

        middleware = RAGCollectorMiddleware()
        await middleware.process(ctx, call_next)

        assert len(collector) == 1
        assert collector[0] == rag_output

    @pytest.mark.asyncio
    async def test_collects_no_results_message(self):
        """Middleware should collect 'no results' messages for quality gate awareness."""
        collector: list[str] = []
        rag_results_collector.set(collector)

        ctx = _make_context()

        async def call_next():
            ctx.result = "No relevant documents found for this query."

        middleware = RAGCollectorMiddleware()
        await middleware.process(ctx, call_next)

        assert len(collector) == 1
        assert collector[0] == "No relevant documents found for this query."

    @pytest.mark.asyncio
    async def test_collects_infrastructure_error(self):
        """Middleware should collect infrastructure error sentinel."""
        collector: list[str] = []
        rag_results_collector.set(collector)

        error_msg = (
            "SEARCH_INFRASTRUCTURE_ERROR: The knowledge base search system"
            " is currently experiencing a technical issue."
        )
        ctx = _make_context()

        async def call_next():
            ctx.result = error_msg

        middleware = RAGCollectorMiddleware()
        await middleware.process(ctx, call_next)

        assert len(collector) == 1
        assert collector[0] == error_msg

    @pytest.mark.asyncio
    async def test_ignores_non_rag_tools(self):
        """Middleware should pass through non-RAG tool calls without collecting."""
        collector: list[str] = []
        rag_results_collector.set(collector)

        ctx = _make_context(func_name="some_other_tool")
        call_next = AsyncMock()

        middleware = RAGCollectorMiddleware()
        await middleware.process(ctx, call_next)

        call_next.assert_awaited_once()
        assert len(collector) == 0

    @pytest.mark.asyncio
    async def test_handles_missing_collector_gracefully(self):
        """Middleware should not raise when no collector context var is set."""
        # Ensure no collector is set in this context
        token = rag_results_collector.set([])
        rag_results_collector.reset(token)

        rag_output = '=== SOURCE 1 ===\ntitle: "Doc"\nCONTENT:\nText\n=== END SOURCE 1 ==='
        ctx = _make_context()

        async def call_next():
            ctx.result = rag_output

        middleware = RAGCollectorMiddleware()
        # Should not raise
        await middleware.process(ctx, call_next)

    @pytest.mark.asyncio
    async def test_calls_next_for_rag_tool(self):
        """Middleware must always call call_next to execute the actual tool."""
        collector: list[str] = []
        rag_results_collector.set(collector)

        ctx = _make_context()
        called = False

        async def call_next():
            nonlocal called
            called = True
            ctx.result = '=== SOURCE 1 ===\ntitle: "X"\n=== END SOURCE 1 ==='

        middleware = RAGCollectorMiddleware()
        await middleware.process(ctx, call_next)

        assert called

    @pytest.mark.asyncio
    async def test_counts_multiple_sources(self):
        """Middleware should correctly count sources in multi-source output."""
        collector: list[str] = []
        rag_results_collector.set(collector)

        rag_output = (
            '=== SOURCE 1 ===\ntitle: "A"\nCONTENT:\nA\n=== END SOURCE 1 ===\n\n'
            '=== SOURCE 2 ===\ntitle: "B"\nCONTENT:\nB\n=== END SOURCE 2 ===\n\n'
            '=== SOURCE 3 ===\ntitle: "C"\nCONTENT:\nC\n=== END SOURCE 3 ==='
        )
        ctx = _make_context()

        async def call_next():
            ctx.result = rag_output

        middleware = RAGCollectorMiddleware()
        await middleware.process(ctx, call_next)

        assert len(collector) == 1
        assert collector[0] == rag_output
