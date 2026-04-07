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
    async def test_does_not_collect_results(self):
        """Middleware no longer collects results — the tool function handles it.

        Result collection was moved into the tool function itself because the
        orchestration framework drops FunctionMiddleware when cloning agents
        for handoff routing.  The middleware remains for observability only.
        """
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

        # Middleware no longer appends — collection happens in the tool.
        assert len(collector) == 0

    @pytest.mark.asyncio
    async def test_ignores_non_rag_tools(self):
        """Middleware should pass through non-RAG tool calls without processing."""
        collector: list[str] = []
        rag_results_collector.set(collector)

        ctx = _make_context(func_name="some_other_tool")
        call_next = AsyncMock()

        middleware = RAGCollectorMiddleware()
        await middleware.process(ctx, call_next)

        call_next.assert_awaited_once()
        assert len(collector) == 0

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
