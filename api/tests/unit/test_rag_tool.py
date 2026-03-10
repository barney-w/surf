"""Tests for the RAG retrieval tool and search utilities."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from azure.core.exceptions import ResourceNotFoundError

from src.agents._base import RAGScope
from src.rag.search import (
    SearchIndexNotFoundError,
    SearchResult,
    build_odata_filter,
    search_index,
)
from src.rag.tools import (
    clear_search_clients,
    create_rag_tool,
    set_search_client,
    stitch_adjacent_chunks,
)

# ---------------------------------------------------------------------------
# OData filter builder
# ---------------------------------------------------------------------------


class TestBuildOdataFilter:
    def test_simple_eq_filter(self):
        result = build_odata_filter({"domain": "hr"})
        assert result == "domain eq 'hr'"

    def test_search_in_filter(self):
        result = build_odata_filter({"document_type_in": ["policy", "agreement"]})
        assert result == "search.in(document_type, 'policy,agreement')"

    def test_combined_filters(self):
        result = build_odata_filter(
            {
                "domain": "hr",
                "document_type_in": ["policy", "agreement"],
            }
        )
        assert result is not None
        assert "domain eq 'hr'" in result
        assert "search.in(document_type, 'policy,agreement')" in result
        assert " and " in result

    def test_empty_filters(self):
        assert build_odata_filter({}) is None


# ---------------------------------------------------------------------------
# search_index
# ---------------------------------------------------------------------------


class TestSearchIndex:
    @pytest.mark.asyncio
    async def test_search_index_returns_results(self):
        mock_doc = {
            "document_id": "doc-1",
            "title": "Leave Policy",
            "section_heading": "Annual Leave",
            "content": "Employees are entitled to 4 weeks annual leave.",
            "@search.score": 0.95,
            "source_url": "https://example.com/leave",
            "domain": "hr",
            "document_type": "policy",
            "chunk_index": 2,
        }

        async def _fake_results():
            yield mock_doc

        mock_results = _fake_results()
        mock_client = AsyncMock()
        mock_client.search = AsyncMock(return_value=mock_results)

        results = await search_index("annual leave", search_client=mock_client)

        assert len(results) == 1
        assert results[0].document_id == "doc-1"
        assert results[0].title == "Leave Policy"
        assert results[0].score == pytest.approx(0.95)  # pyright: ignore[reportUnknownMemberType]
        assert results[0].chunk_index == 2

    @pytest.mark.asyncio
    async def test_search_index_passes_filter(self):
        async def _empty():
            return
            yield  # make it an async generator  # noqa: RET504

        mock_client = AsyncMock()
        mock_client.search = AsyncMock(return_value=_empty())

        await search_index(
            "query",
            search_client=mock_client,
            filters={"domain": "finance"},
        )

        _, kwargs = mock_client.search.call_args
        assert kwargs["filter"] == "domain eq 'finance'"

    @pytest.mark.asyncio
    async def test_search_index_raises_when_index_missing(self):
        mock_client = AsyncMock()
        mock_client.search = AsyncMock(side_effect=ResourceNotFoundError("Index not found"))

        with pytest.raises(SearchIndexNotFoundError):
            await search_index("query", search_client=mock_client)


# ---------------------------------------------------------------------------
# create_rag_tool — factory
# ---------------------------------------------------------------------------


class TestCreateRagTool:
    def test_scoped_tool_is_callable(self):
        rag_tool = create_rag_tool(scope=RAGScope(domain="hr"))
        assert callable(rag_tool)

    def test_unscoped_tool_is_callable(self):
        rag_tool = create_rag_tool(scope=None)
        assert callable(rag_tool)

    def test_tool_has_name(self):
        rag_tool = create_rag_tool(scope=RAGScope(domain="hr"))
        assert rag_tool.name == "search_knowledge_base"


# ---------------------------------------------------------------------------
# Result formatting
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_search_clients():  # pyright: ignore[reportUnusedFunction]
    """Clear module-level search clients between tests."""
    clear_search_clients()
    yield
    clear_search_clients()


class TestResultFormatting:
    @pytest.mark.asyncio
    async def test_formatted_output_contains_citations(self):
        """Invoke the tool with a mocked search client and verify formatting."""
        mock_doc = {
            "document_id": "doc-42",
            "title": "Travel Policy",
            "section_heading": "Domestic Travel",
            "content": "All domestic travel requires pre-approval.",
            "@search.score": 0.88,
            "source_url": None,
            "domain": "hr",
            "document_type": "policy",
        }

        async def _fake_results():
            yield mock_doc

        mock_client = AsyncMock()
        mock_client.search = AsyncMock(return_value=_fake_results())
        set_search_client(mock_client)

        rag_tool = create_rag_tool(scope=RAGScope(domain="hr"))
        # Invoke underlying function via keyword-only arguments API
        result = await rag_tool.invoke(
            arguments={"query": "travel approval", "document_type": None},
        )

        assert "=== SOURCE 1 ===" in result
        assert 'title: "Travel Policy"' in result
        assert 'section: "Domestic Travel"' in result
        assert 'document_id: "doc-42"' in result
        assert "relevance: 1.0" in result  # single result normalises to 1.0
        assert "CONTENT:" in result
        assert "pre-approval" in result
        assert "=== END SOURCE 1 ===" in result

    @pytest.mark.asyncio
    async def test_no_results_message(self):
        async def _empty():
            return
            yield  # noqa: RET504

        mock_client = AsyncMock()
        mock_client.search = AsyncMock(return_value=_empty())
        set_search_client(mock_client)

        rag_tool = create_rag_tool(scope=None)
        result = await rag_tool.invoke(
            arguments={"query": "nonexistent topic", "document_type": None},
        )
        assert "No relevant documents found" in result

    @pytest.mark.asyncio
    async def test_missing_index_returns_actionable_message(self):  # noqa: D102
        mock_client = AsyncMock()
        mock_client.search = AsyncMock(
            side_effect=ResourceNotFoundError("The index 'surf-index' was not found.")
        )
        set_search_client(mock_client)

        rag_tool = create_rag_tool(scope=None)
        result = await rag_tool.invoke(
            arguments={"query": "annual leave", "document_type": None},
        )

        assert "Knowledge search is unavailable" in result
        assert "surf-index" in result


# ---------------------------------------------------------------------------
# stitch_adjacent_chunks
# ---------------------------------------------------------------------------


def _make_result(doc_id: str, chunk_index: int, score: float, content: str) -> SearchResult:
    return SearchResult(
        document_id=doc_id,
        title="Doc",
        section_heading=None,
        content=content,
        score=score,
        source_url=None,
        domain="hr",
        document_type="policy",
        chunk_index=chunk_index,
    )


class TestStitchAdjacentChunks:
    def test_merges_consecutive_chunks_from_same_doc(self):
        results = [
            _make_result("doc-1", 3, 0.9, "Clause starts here."),
            _make_result("doc-1", 4, 0.7, "Clause continues here."),
        ]
        stitched = stitch_adjacent_chunks(results)
        assert len(stitched) == 1
        assert "Clause starts here." in stitched[0].content
        assert "Clause continues here." in stitched[0].content
        assert stitched[0].score == pytest.approx(0.9)  # pyright: ignore[reportUnknownMemberType]  # takes the higher score
        assert stitched[0].chunk_index == 3

    def test_does_not_merge_non_consecutive_chunks(self):
        results = [
            _make_result("doc-1", 2, 0.9, "Early chunk."),
            _make_result("doc-1", 5, 0.8, "Later chunk."),
        ]
        stitched = stitch_adjacent_chunks(results)
        assert len(stitched) == 2

    def test_does_not_merge_chunks_from_different_docs(self):
        results = [
            _make_result("doc-1", 3, 0.9, "Doc 1 chunk."),
            _make_result("doc-2", 4, 0.8, "Doc 2 chunk."),
        ]
        stitched = stitch_adjacent_chunks(results)
        assert len(stitched) == 2

    def test_merges_run_of_three_consecutive_chunks(self):
        results = [
            _make_result("doc-1", 0, 0.6, "Part A."),
            _make_result("doc-1", 1, 0.9, "Part B."),
            _make_result("doc-1", 2, 0.7, "Part C."),
        ]
        stitched = stitch_adjacent_chunks(results)
        assert len(stitched) == 1
        assert "Part A." in stitched[0].content
        assert "Part B." in stitched[0].content
        assert "Part C." in stitched[0].content

    def test_returns_sorted_by_score_descending(self):
        results = [
            _make_result("doc-1", 0, 0.5, "Low score chunk."),
            _make_result("doc-2", 0, 0.95, "High score chunk."),
        ]
        stitched = stitch_adjacent_chunks(results)
        assert stitched[0].score > stitched[1].score

    def test_empty_input_returns_empty(self):
        assert stitch_adjacent_chunks([]) == []


# ---------------------------------------------------------------------------
# Multi-index search (P0 #12, E7)
# ---------------------------------------------------------------------------


class TestMultiIndexSearch:
    @pytest.mark.asyncio
    async def test_multi_index_merges_results_by_score(self):
        """Querying multiple indexes should merge results sorted by score."""

        async def _results_a():
            yield {
                "document_id": "doc-a1",
                "title": "From Index A",
                "content": "Content A",
                "@search.score": 0.90,
                "domain": "hr",
                "document_type": "policy",
            }

        async def _results_b():
            yield {
                "document_id": "doc-b1",
                "title": "From Index B",
                "content": "Content B",
                "@search.score": 0.95,
                "domain": "hr",
                "document_type": "policy",
            }

        mock_client_a = AsyncMock()
        mock_client_a.search = AsyncMock(return_value=_results_a())
        mock_client_b = AsyncMock()
        mock_client_b.search = AsyncMock(return_value=_results_b())

        results = await search_index(
            "test query",
            search_client=[mock_client_a, mock_client_b],
        )

        assert len(results) == 2
        # Higher score should come first
        assert results[0].title == "From Index B"
        assert results[1].title == "From Index A"

    @pytest.mark.asyncio
    async def test_multi_index_one_index_failure_returns_other(self):
        """If one index fails, results from the other should still be returned."""

        async def _results_ok():
            yield {
                "document_id": "doc-ok",
                "title": "Good result",
                "content": "Some content",
                "@search.score": 0.8,
                "domain": "hr",
                "document_type": "policy",
            }

        mock_client_ok = AsyncMock()
        mock_client_ok.search = AsyncMock(return_value=_results_ok())
        mock_client_fail = AsyncMock()
        mock_client_fail.search = AsyncMock(side_effect=RuntimeError("Connection refused"))

        results = await search_index(
            "test query",
            search_client=[mock_client_ok, mock_client_fail],
        )

        assert len(results) == 1
        assert results[0].title == "Good result"

    @pytest.mark.asyncio
    async def test_multi_index_trims_to_top_k(self):
        """Merged results should be trimmed to top_k."""

        async def _many_results():
            for i in range(5):
                yield {
                    "document_id": f"doc-{i}",
                    "title": f"Result {i}",
                    "content": f"Content {i}",
                    "@search.score": 0.5 + i * 0.1,
                    "domain": "hr",
                    "document_type": "policy",
                }

        mock_client_a = AsyncMock()
        mock_client_a.search = AsyncMock(return_value=_many_results())
        mock_client_b = AsyncMock()
        mock_client_b.search = AsyncMock(return_value=_many_results())

        results = await search_index(
            "test",
            search_client=[mock_client_a, mock_client_b],
            top_k=3,
        )

        assert len(results) == 3
        # Should be the highest 3 scores
        assert results[0].score >= results[1].score >= results[2].score

    @pytest.mark.asyncio
    async def test_single_client_backwards_compatible(self):
        """Passing a single client (not a list) should still work."""

        async def _results():
            yield {
                "document_id": "doc-1",
                "title": "Single",
                "content": "Content",
                "@search.score": 0.7,
                "domain": "hr",
                "document_type": "policy",
            }

        mock_client = AsyncMock()
        mock_client.search = AsyncMock(return_value=_results())

        results = await search_index(
            "test",
            search_client=mock_client,
        )

        assert len(results) == 1
        assert results[0].title == "Single"


# ---------------------------------------------------------------------------
# set_search_client / _get_search_clients (multi-client registration)
# ---------------------------------------------------------------------------


class TestSearchClientRegistry:
    def test_set_search_client_appends(self):
        """Multiple calls to set_search_client should register all clients."""
        from src.rag.tools import _get_search_clients  # pyright: ignore[reportPrivateUsage]

        mock_a = AsyncMock()
        mock_b = AsyncMock()
        set_search_client(mock_a)
        set_search_client(mock_b)

        clients = _get_search_clients()
        assert len(clients) == 2
        assert mock_a in clients
        assert mock_b in clients

    def test_clear_search_clients_empties_list(self):
        """clear_search_clients should remove all registered clients."""
        from src.rag.tools import _get_search_clients  # pyright: ignore[reportPrivateUsage]

        set_search_client(AsyncMock())
        clear_search_clients()

        with pytest.raises(RuntimeError, match="Search client not initialised"):
            _get_search_clients()
