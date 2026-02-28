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
from src.rag.tools import _stitch_adjacent_chunks, create_rag_tool, set_search_client


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
        result = build_odata_filter({
            "domain": "hr",
            "document_type_in": ["policy", "agreement"],
        })
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
        assert results[0].score == pytest.approx(0.95)
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
        mock_client.search = AsyncMock(
            side_effect=ResourceNotFoundError("Index not found")
        )

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
# _stitch_adjacent_chunks
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
        stitched = _stitch_adjacent_chunks(results)
        assert len(stitched) == 1
        assert "Clause starts here." in stitched[0].content
        assert "Clause continues here." in stitched[0].content
        assert stitched[0].score == pytest.approx(0.9)  # takes the higher score
        assert stitched[0].chunk_index == 3

    def test_does_not_merge_non_consecutive_chunks(self):
        results = [
            _make_result("doc-1", 2, 0.9, "Early chunk."),
            _make_result("doc-1", 5, 0.8, "Later chunk."),
        ]
        stitched = _stitch_adjacent_chunks(results)
        assert len(stitched) == 2

    def test_does_not_merge_chunks_from_different_docs(self):
        results = [
            _make_result("doc-1", 3, 0.9, "Doc 1 chunk."),
            _make_result("doc-2", 4, 0.8, "Doc 2 chunk."),
        ]
        stitched = _stitch_adjacent_chunks(results)
        assert len(stitched) == 2

    def test_merges_run_of_three_consecutive_chunks(self):
        results = [
            _make_result("doc-1", 0, 0.6, "Part A."),
            _make_result("doc-1", 1, 0.9, "Part B."),
            _make_result("doc-1", 2, 0.7, "Part C."),
        ]
        stitched = _stitch_adjacent_chunks(results)
        assert len(stitched) == 1
        assert "Part A." in stitched[0].content
        assert "Part B." in stitched[0].content
        assert "Part C." in stitched[0].content

    def test_returns_sorted_by_score_descending(self):
        results = [
            _make_result("doc-1", 0, 0.5, "Low score chunk."),
            _make_result("doc-2", 0, 0.95, "High score chunk."),
        ]
        stitched = _stitch_adjacent_chunks(results)
        assert stitched[0].score > stitched[1].score

    def test_empty_input_returns_empty(self):
        assert _stitch_adjacent_chunks([]) == []
