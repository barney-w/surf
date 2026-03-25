"""Tests for the RAG retrieval tool and search utilities."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from azure.core.exceptions import ResourceNotFoundError

from src.agents._base import RAGScope
from src.rag.search import (
    SearchIndexNotFoundError,
    SearchInfrastructureError,
    SearchResult,
    build_odata_filter,
    search_index,
)
from src.rag.tools import (
    _extract_keywords,  # pyright: ignore[reportPrivateUsage]
    _merge_and_deduplicate,  # pyright: ignore[reportPrivateUsage]
    clear_search_clients,
    create_rag_tool,
    rewrite_query_with_llm,
    set_rewrite_client,
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
# SearchInfrastructureError
# ---------------------------------------------------------------------------


class TestSearchInfrastructureError:
    @pytest.mark.asyncio
    async def test_http_error_raises_infrastructure_error(self):
        """HttpResponseError should raise SearchInfrastructureError, not return []."""
        from azure.core.exceptions import HttpResponseError

        mock_client = AsyncMock()
        mock_client.search = AsyncMock(side_effect=HttpResponseError("403 Forbidden"))

        with pytest.raises(SearchInfrastructureError):
            await search_index("test query", search_client=mock_client)

    @pytest.mark.asyncio
    async def test_permission_error_raises_infrastructure_error(self):
        """OpenAI PermissionDeniedError should raise SearchInfrastructureError."""
        mock_client = AsyncMock()
        mock_client.search = AsyncMock(side_effect=PermissionError("Public access is disabled"))

        with pytest.raises(SearchInfrastructureError):
            await search_index("test query", search_client=mock_client)

    @pytest.mark.asyncio
    async def test_unexpected_error_raises_infrastructure_error(self):
        """Any unexpected exception should raise SearchInfrastructureError."""
        mock_client = AsyncMock()
        mock_client.search = AsyncMock(side_effect=ConnectionError("Network unreachable"))

        with pytest.raises(SearchInfrastructureError):
            await search_index("test query", search_client=mock_client)

    @pytest.mark.asyncio
    async def test_resource_not_found_still_raises_index_error(self):
        """ResourceNotFoundError should still raise SearchIndexNotFoundError (not infra error)."""
        mock_client = AsyncMock()
        mock_client.search = AsyncMock(side_effect=ResourceNotFoundError("Index not found"))

        with pytest.raises(SearchIndexNotFoundError):
            await search_index("test query", search_client=mock_client)

    @pytest.mark.asyncio
    async def test_legitimate_empty_results_still_return_empty(self):
        """A search that succeeds but finds nothing should return [], not raise."""

        async def _empty():
            return
            yield  # noqa: RET504

        mock_client = AsyncMock()
        mock_client.search = AsyncMock(return_value=_empty())

        results = await search_index("obscure topic", search_client=mock_client)
        assert results == []


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
# RAG tool filter wiring — metadata_filters vs domain
# ---------------------------------------------------------------------------


class TestRagToolFilterWiring:
    """Verify create_rag_tool passes the correct filters to search_index."""

    @pytest.mark.asyncio
    async def test_metadata_filters_passed_without_domain(self):
        """A scope with metadata_filters and empty domain should include
        content_source but NOT domain in the search filters."""

        async def _empty():
            return
            yield  # noqa: RET504

        mock_client = AsyncMock()
        mock_client.search = AsyncMock(return_value=_empty())
        set_search_client(mock_client)

        scope = RAGScope(domain="", metadata_filters={"content_source": "website"})
        rag_tool = create_rag_tool(scope=scope)
        await rag_tool.invoke(arguments={"query": "recycling bins", "document_type": None})

        # Strategy 1 (first call) should include content_source filter
        _, kwargs = mock_client.search.call_args_list[0]
        odata = kwargs.get("filter", "")
        assert "content_source" in odata
        assert "domain eq" not in odata

    @pytest.mark.asyncio
    async def test_domain_filter_passed_without_metadata_filters(self):
        """A scope with domain set and no metadata_filters should include
        domain but NOT content_source in the search filters."""

        async def _empty():
            return
            yield  # noqa: RET504

        mock_client = AsyncMock()
        mock_client.search = AsyncMock(return_value=_empty())
        set_search_client(mock_client)

        scope = RAGScope(domain="hr")
        rag_tool = create_rag_tool(scope=scope)
        await rag_tool.invoke(arguments={"query": "leave policy", "document_type": None})

        # Strategy 1 (first call) should include domain filter
        _, kwargs = mock_client.search.call_args_list[0]
        odata = kwargs.get("filter", "")
        assert "domain eq 'hr'" in odata
        assert "content_source" not in odata


# ---------------------------------------------------------------------------
# Result formatting
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_search_clients():  # pyright: ignore[reportUnusedFunction]
    """Clear module-level search clients, embed func, and rewrite client between tests."""
    clear_search_clients()
    from src.rag import tools

    original_embed = tools._embed_func
    original_rewrite_client = tools._rewrite_client
    original_rewrite_model = tools._rewrite_model_id
    tools._embed_func = None
    tools._rewrite_client = None
    yield
    clear_search_clients()
    tools._embed_func = original_embed
    tools._rewrite_client = original_rewrite_client
    tools._rewrite_model_id = original_rewrite_model


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
        assert "relevance: 1.0 (STRONG MATCH)" in result  # single result normalises to 1.0
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


# ---------------------------------------------------------------------------
# verify_rag_connectivity
# ---------------------------------------------------------------------------


class TestVerifyRagConnectivity:
    @pytest.mark.asyncio
    async def test_returns_not_configured_when_no_clients(self):
        from src.rag.tools import verify_rag_connectivity

        result = await verify_rag_connectivity()
        assert result["search"] == "not_configured"
        assert result["embedding"] == "not_configured"

    @pytest.mark.asyncio
    async def test_search_ok_when_client_works(self):
        from src.rag.tools import verify_rag_connectivity

        async def _empty():
            return
            yield  # noqa: RET504

        mock_client = AsyncMock()
        mock_client.search = AsyncMock(return_value=_empty())
        set_search_client(mock_client)

        result = await verify_rag_connectivity()
        assert result["search"] == "ok"

    @pytest.mark.asyncio
    async def test_search_error_when_client_fails(self):
        from src.rag.tools import verify_rag_connectivity

        mock_client = AsyncMock()
        mock_client.search = AsyncMock(side_effect=RuntimeError("Connection refused"))
        set_search_client(mock_client)

        result = await verify_rag_connectivity()
        assert result["search"].startswith("error:")

    @pytest.mark.asyncio
    async def test_embedding_ok_when_func_works(self):
        from src.rag.tools import set_embed_func, verify_rag_connectivity

        async def _fake_embed(text: str) -> list[float]:
            return [0.1, 0.2, 0.3]

        set_embed_func(_fake_embed)

        result = await verify_rag_connectivity()
        assert result["embedding"] == "ok"

    @pytest.mark.asyncio
    async def test_embedding_error_when_func_fails(self):
        from src.rag.tools import set_embed_func, verify_rag_connectivity

        async def _broken_embed(text: str) -> list[float]:
            raise PermissionError("403 Public access disabled")

        set_embed_func(_broken_embed)

        result = await verify_rag_connectivity()
        assert result["embedding"].startswith("error:")
        assert "PermissionError" in result["embedding"]


# ---------------------------------------------------------------------------
# Tier labels and summary header
# ---------------------------------------------------------------------------


def _make_search_results_with_scores(*scores: float) -> list[dict]:
    """Create mock search result dicts with the given scores."""
    return [
        {
            "document_id": f"doc-{i}",
            "title": f"Document {i}",
            "section_heading": None,
            "content": f"Content for document {i}.",
            "@search.score": score,
            "source_url": None,
            "domain": "hr",
            "document_type": "policy",
        }
        for i, score in enumerate(scores, 1)
    ]


class TestTierLabels:
    """Verify tier labels (STRONG/PARTIAL/WEAK MATCH) and summary header."""

    @pytest.mark.asyncio
    async def test_strong_match_label(self):
        """A result with the highest score (normalised to 1.0) gets STRONG MATCH."""
        docs = _make_search_results_with_scores(1.0)

        async def _results():
            for d in docs:
                yield d

        mock_client = AsyncMock()
        mock_client.search = AsyncMock(return_value=_results())
        set_search_client(mock_client)

        rag_tool = create_rag_tool(scope=None)
        result = await rag_tool.invoke(arguments={"query": "test", "document_type": None})
        assert "relevance: 1.0 (STRONG MATCH)" in result

    @pytest.mark.asyncio
    async def test_partial_match_label(self):
        """A result with normalised score 0.5 gets PARTIAL MATCH."""
        docs = _make_search_results_with_scores(1.0, 0.5)

        async def _results():
            for d in docs:
                yield d

        mock_client = AsyncMock()
        mock_client.search = AsyncMock(return_value=_results())
        set_search_client(mock_client)

        rag_tool = create_rag_tool(scope=None)
        result = await rag_tool.invoke(arguments={"query": "test", "document_type": None})
        assert "relevance: 0.5 (PARTIAL MATCH)" in result

    @pytest.mark.asyncio
    async def test_weak_match_label(self):
        """A result with normalised score 0.2 gets WEAK MATCH."""
        docs = _make_search_results_with_scores(1.0, 0.2)

        async def _results():
            for d in docs:
                yield d

        mock_client = AsyncMock()
        mock_client.search = AsyncMock(return_value=_results())
        set_search_client(mock_client)

        rag_tool = create_rag_tool(scope=None)
        result = await rag_tool.invoke(arguments={"query": "test", "document_type": None})
        assert "relevance: 0.2 (WEAK MATCH)" in result

    @pytest.mark.asyncio
    async def test_tier_boundary_0_7(self):
        """Score exactly 0.7 (normalised) should be STRONG MATCH."""
        docs = _make_search_results_with_scores(1.0, 0.7)

        async def _results():
            for d in docs:
                yield d

        mock_client = AsyncMock()
        mock_client.search = AsyncMock(return_value=_results())
        set_search_client(mock_client)

        rag_tool = create_rag_tool(scope=None)
        result = await rag_tool.invoke(arguments={"query": "test", "document_type": None})
        assert "relevance: 0.7 (STRONG MATCH)" in result

    @pytest.mark.asyncio
    async def test_tier_boundary_0_4(self):
        """Score exactly 0.4 (normalised) should be PARTIAL MATCH."""
        docs = _make_search_results_with_scores(1.0, 0.4)

        async def _results():
            for d in docs:
                yield d

        mock_client = AsyncMock()
        mock_client.search = AsyncMock(return_value=_results())
        set_search_client(mock_client)

        rag_tool = create_rag_tool(scope=None)
        result = await rag_tool.invoke(arguments={"query": "test", "document_type": None})
        assert "relevance: 0.4 (PARTIAL MATCH)" in result

    @pytest.mark.asyncio
    async def test_summary_header_present(self):
        """Output should start with 'Found N results' summary line."""
        docs = _make_search_results_with_scores(0.9)

        async def _results():
            for d in docs:
                yield d

        mock_client = AsyncMock()
        mock_client.search = AsyncMock(return_value=_results())
        set_search_client(mock_client)

        rag_tool = create_rag_tool(scope=None)
        result = await rag_tool.invoke(arguments={"query": "test", "document_type": None})
        assert result.startswith("Found 1 results")

    @pytest.mark.asyncio
    async def test_summary_header_counts(self):
        """Verify strong/partial/weak counts in summary are correct."""
        # Scores: 1.0 -> 1.0 (strong), 0.7 -> 0.7 (strong),
        #         0.5 -> 0.5 (partial), 0.3 -> 0.3 (weak)
        docs = _make_search_results_with_scores(1.0, 0.7, 0.5, 0.3)

        async def _results():
            for d in docs:
                yield d

        mock_client = AsyncMock()
        mock_client.search = AsyncMock(return_value=_results())
        set_search_client(mock_client)

        rag_tool = create_rag_tool(scope=None)
        result = await rag_tool.invoke(arguments={"query": "test", "document_type": None})
        assert "Found 4 results (2 strong, 1 partial, 1 weak)" in result
        assert "Base your answer on the strong and partial matches." in result


# ---------------------------------------------------------------------------
# _extract_keywords
# ---------------------------------------------------------------------------


class TestExtractKeywords:
    def test_strips_conversational_phrasing(self):
        result = _extract_keywords(
            "tell me about the code of conduct in relation to my role as developer"
        )
        assert result == "code conduct developer"

    def test_strips_stop_words(self):
        result = _extract_keywords("what is the leave policy for annual leave")
        assert result == "leave policy annual leave"

    def test_preserves_keywords_only_query(self):
        result = _extract_keywords("code of conduct")
        assert result == "code conduct"

    def test_returns_original_if_empty_after_strip(self):
        result = _extract_keywords("tell me about the")
        assert result == "tell me about the"

    def test_handles_empty_string(self):
        assert _extract_keywords("") == ""

    def test_case_insensitive(self):
        result = _extract_keywords("Tell Me About The CODE OF CONDUCT")
        assert result == "code conduct"


# ---------------------------------------------------------------------------
# _merge_and_deduplicate
# ---------------------------------------------------------------------------


def _make_search_result(
    doc_id: str, chunk_index: int, score: float, content: str = "Test content"
) -> SearchResult:
    return SearchResult(
        document_id=doc_id,
        title="Test",
        section_heading=None,
        content=content,
        score=score,
        source_url=None,
        domain="hr",
        document_type="policy",
        chunk_index=chunk_index,
    )


class TestMergeAndDeduplicate:
    def test_deduplicates_by_doc_and_chunk(self):
        primary = [_make_search_result("doc1", 0, 0.8)]
        secondary = [_make_search_result("doc1", 0, 0.9)]
        result = _merge_and_deduplicate(primary, secondary, top_k=10)
        assert len(result) == 1
        assert result[0].score == 0.9  # keeps higher score

    def test_preserves_unique_results(self):
        primary = [_make_search_result("doc1", 0, 0.8)]
        secondary = [_make_search_result("doc2", 0, 0.9)]
        result = _merge_and_deduplicate(primary, secondary, top_k=10)
        assert len(result) == 2
        assert result[0].document_id == "doc2"  # higher score first
        assert result[1].document_id == "doc1"

    def test_trims_to_top_k(self):
        primary = [_make_search_result(f"doc-p{i}", 0, 0.5 + i * 0.01) for i in range(10)]
        secondary = [_make_search_result(f"doc-s{i}", 0, 0.6 + i * 0.01) for i in range(10)]
        result = _merge_and_deduplicate(primary, secondary, top_k=15)
        assert len(result) == 15

    def test_empty_secondary(self):
        primary = [_make_search_result("doc1", 0, 0.8), _make_search_result("doc2", 0, 0.6)]
        result = _merge_and_deduplicate(primary, [], top_k=10)
        assert len(result) == 2
        assert result[0].score >= result[1].score

    def test_empty_primary(self):
        secondary = [_make_search_result("doc1", 0, 0.7), _make_search_result("doc2", 0, 0.9)]
        result = _merge_and_deduplicate([], secondary, top_k=10)
        assert len(result) == 2
        assert result[0].score >= result[1].score

    def test_sorted_by_score_descending(self):
        primary = [
            _make_search_result("doc1", 0, 0.3),
            _make_search_result("doc2", 0, 0.9),
            _make_search_result("doc3", 0, 0.6),
        ]
        secondary = [
            _make_search_result("doc4", 0, 0.1),
            _make_search_result("doc5", 0, 0.7),
        ]
        result = _merge_and_deduplicate(primary, secondary, top_k=10)
        scores = [r.score for r in result]
        assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# Multi-strategy search cascade
# ---------------------------------------------------------------------------


def _sr(
    doc_id: str = "doc1",
    chunk_index: int = 0,
    score: float = 0.8,
    content: str = "Test content",
    domain: str = "hr",
    document_type: str = "policy",
) -> SearchResult:
    """Shorthand factory for SearchResult used by multi-strategy tests."""
    return SearchResult(
        document_id=doc_id,
        title="Test",
        section_heading=None,
        content=content,
        score=score,
        source_url=None,
        domain=domain,
        document_type=document_type,
        chunk_index=chunk_index,
    )


class TestMultiStrategySearch:
    """Verify the three-strategy search cascade in create_rag_tool."""

    @pytest.fixture(autouse=True)
    def _patch_search(self, monkeypatch):
        self.mock_search = AsyncMock()
        monkeypatch.setattr("src.rag.tools.search_index", self.mock_search)
        monkeypatch.setattr("src.rag.tools._search_clients", [MagicMock()])

    @pytest.mark.asyncio
    async def test_strategy1_sufficient_no_broadening(self):
        """When strategy 1 returns >= 3 results, no further strategies fire."""
        self.mock_search.return_value = [_sr(f"doc{i}", score=0.9 - i * 0.01) for i in range(5)]

        rag_tool = create_rag_tool(scope=RAGScope(domain="hr"))
        await rag_tool.invoke(arguments={"query": "leave policy", "document_type": "policy"})

        assert self.mock_search.call_count == 1

    @pytest.mark.asyncio
    async def test_strategy2_fires_on_sparse_results(self):
        """When strategy 1 returns < 3 results, strategy 2 broadens filters."""
        self.mock_search.side_effect = [
            # Strategy 1: sparse
            [_sr("doc1", score=0.9)],
            # Strategy 2: broadened
            [_sr(f"doc{i}", score=0.8 - i * 0.01) for i in range(2, 6)],
        ]

        rag_tool = create_rag_tool(scope=RAGScope(domain="hr", document_types=["policy"]))
        await rag_tool.invoke(arguments={"query": "leave policy", "document_type": "policy"})

        assert self.mock_search.call_count == 2
        # Strategy 2 should keep identity filters (domain) but drop document_type
        _, s2_kwargs = self.mock_search.call_args_list[1]
        s2_filters = s2_kwargs.get("filters") or {}
        assert s2_filters == {"domain": "hr"}
        assert "document_type" not in s2_filters
        assert "document_type_in" not in s2_filters

    @pytest.mark.asyncio
    async def test_strategy3_fires_on_very_sparse_results(self):
        """When strategies 1+2 combined return < 3 results, strategy 3 fires with keywords."""
        self.mock_search.side_effect = [
            # Strategy 1: nothing
            [],
            # Strategy 2: nothing
            [],
            # Strategy 3: keyword extraction yields results
            [_sr(f"doc{i}", score=0.7 - i * 0.01) for i in range(3)],
        ]

        rag_tool = create_rag_tool(scope=RAGScope(domain="hr"))
        await rag_tool.invoke(
            arguments={
                "query": "tell me about the code of conduct",
                "document_type": None,
            }
        )

        assert self.mock_search.call_count == 3
        # Strategy 3 call should use extracted keywords, not the original query
        _, s3_kwargs = self.mock_search.call_args_list[2]
        assert s3_kwargs["query"] != "tell me about the code of conduct"

    @pytest.mark.asyncio
    async def test_strategy3_skipped_when_keywords_match_query(self):
        """Strategy 3 is skipped when extracted keywords equal the lowered query."""
        self.mock_search.side_effect = [
            # Strategy 1: nothing
            [],
            # Strategy 2: nothing
            [],
        ]

        rag_tool = create_rag_tool(scope=RAGScope(domain="hr"))
        # "code conduct" after keyword extraction stays "code conduct"
        await rag_tool.invoke(arguments={"query": "code conduct", "document_type": None})

        assert self.mock_search.call_count == 2

    @pytest.mark.asyncio
    async def test_deduplication_across_strategies(self):
        """Overlapping results from strategies 1 and 2 should be deduplicated."""
        shared = _sr("shared-doc", chunk_index=0, score=0.85, content="Shared content")
        unique_s2 = _sr("unique-doc", chunk_index=0, score=0.75, content="Unique content")
        self.mock_search.side_effect = [
            # Strategy 1: one result
            [shared],
            # Strategy 2: overlapping + unique
            [
                _sr("shared-doc", chunk_index=0, score=0.80, content="Shared content"),
                unique_s2,
            ],
        ]

        rag_tool = create_rag_tool(scope=RAGScope(domain="hr"))
        result = await rag_tool.invoke(arguments={"query": "some query", "document_type": "policy"})

        # Should have exactly 2 SOURCE blocks (one for shared-doc, one for unique-doc)
        source_blocks = result.count("=== SOURCE")
        end_blocks = result.count("=== END SOURCE")
        assert source_blocks == 2
        assert end_blocks == 2
        # shared-doc should appear only once
        assert result.count('document_id: "shared-doc"') == 1

    @pytest.mark.asyncio
    async def test_strategy2_keeps_content_source_for_website_agent(self):
        """Strategy 2 keeps content_source as an identity filter."""
        self.mock_search.side_effect = [
            # Strategy 1 with content_source="website": sparse
            [_sr("doc1", score=0.9)],
            # Strategy 2 still filtered by content_source: finds more website results
            [_sr(f"doc{i}", score=0.8 - i * 0.01) for i in range(2, 6)],
        ]

        # Website agent has no domain, but has content_source metadata filter
        rag_tool = create_rag_tool(
            scope=RAGScope(
                domain="",
                document_types=[],
                metadata_filters={"content_source": "website"},
            )
        )
        await rag_tool.invoke(arguments={"query": "red bin waste", "document_type": None})

        assert self.mock_search.call_count == 2
        # Strategy 2 should keep content_source filter (identity filter)
        _, s2_kwargs = self.mock_search.call_args_list[1]
        s2_filters = s2_kwargs.get("filters") or {}
        assert s2_filters == {"content_source": "website"}

    @pytest.mark.asyncio
    async def test_no_results_all_strategies(self):
        """When all strategies return empty, the tool returns the no-results message."""
        self.mock_search.side_effect = [
            # Strategy 1
            [],
            # Strategy 2
            [],
            # Strategy 3
            [],
        ]

        rag_tool = create_rag_tool(scope=RAGScope(domain="hr"))
        result = await rag_tool.invoke(
            arguments={
                "query": "tell me about something obscure",
                "document_type": None,
            }
        )

        assert result == "No relevant documents found for this query."

    @pytest.mark.asyncio
    async def test_infrastructure_error_returns_sentinel(self):
        """SearchInfrastructureError should return SEARCH_INFRASTRUCTURE_ERROR sentinel."""
        self.mock_search.side_effect = SearchInfrastructureError("403 Forbidden")

        rag_tool = create_rag_tool(scope=RAGScope(domain="hr"))
        result = await rag_tool.invoke(arguments={"query": "test query", "document_type": None})

        assert result.startswith("SEARCH_INFRASTRUCTURE_ERROR:")
        assert "403 Forbidden" in result

    @pytest.mark.asyncio
    async def test_infrastructure_error_does_not_return_sources(self):
        """Infrastructure error should not contain SOURCE blocks."""
        self.mock_search.side_effect = SearchInfrastructureError("Connection refused")

        rag_tool = create_rag_tool(scope=RAGScope(domain="hr"))
        result = await rag_tool.invoke(arguments={"query": "test query", "document_type": None})

        assert "=== SOURCE" not in result


# ---------------------------------------------------------------------------
# rewrite_query_with_llm
# ---------------------------------------------------------------------------


class TestRewriteQueryWithLlm:
    """Verify LLM-based query rewrite behaviour."""

    @pytest.mark.asyncio
    async def test_rewrite_query_with_llm_success(self):
        """Mock AsyncAnthropic and verify the rewrite is applied."""
        mock_client = AsyncMock()
        mock_content_block = MagicMock()
        mock_content_block.text = "sick leave policy absence notification"
        mock_response = MagicMock()
        mock_response.content = [mock_content_block]
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        set_rewrite_client(mock_client, "claude-haiku-4-5-20251001")

        result = await rewrite_query_with_llm("what happens if I'm sick?")
        assert result == "sick leave policy absence notification"
        mock_client.messages.create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rewrite_query_with_llm_timeout(self):
        """Verify original query returned when the LLM call times out."""
        mock_client = AsyncMock()

        async def _slow_create(**kwargs):
            await asyncio.sleep(10)

        mock_client.messages.create = _slow_create

        set_rewrite_client(mock_client, "claude-haiku-4-5-20251001")

        result = await rewrite_query_with_llm("what happens if I'm sick?")
        assert result == "what happens if I'm sick?"

    @pytest.mark.asyncio
    async def test_rewrite_query_with_llm_no_client(self):
        """Verify original query returned when the rewrite client is None."""
        from src.rag import tools

        tools._rewrite_client = None

        result = await rewrite_query_with_llm("what happens if I'm sick?")
        assert result == "what happens if I'm sick?"

    @pytest.mark.asyncio
    async def test_strategy_0_uses_rewritten_query(self):
        """Strategy 1 should receive the rewritten query from Strategy 0."""
        mock_search = AsyncMock()
        mock_search.return_value = [_sr(f"doc{i}", score=0.9 - i * 0.01) for i in range(5)]

        # Set up rewrite client to return a rewritten query
        mock_rewrite_client = AsyncMock()
        mock_content_block = MagicMock()
        mock_content_block.text = "sick leave policy absence notification"
        mock_response = MagicMock()
        mock_response.content = [mock_content_block]
        mock_rewrite_client.messages.create = AsyncMock(return_value=mock_response)

        set_rewrite_client(mock_rewrite_client, "claude-haiku-4-5-20251001")

        with (
            patch("src.rag.tools.search_index", mock_search),
            patch("src.rag.tools._search_clients", [MagicMock()]),
        ):
            rag_tool = create_rag_tool(scope=RAGScope(domain="hr"))
            await rag_tool.invoke(
                arguments={"query": "what happens if I'm sick?", "document_type": None}
            )

        # Strategy 1 should have been called with the rewritten query
        _, s1_kwargs = mock_search.call_args_list[0]
        assert s1_kwargs["query"] == "sick leave policy absence notification"

    @pytest.mark.asyncio
    async def test_strategy_2_uses_original_query_after_rewrite(self):
        """Strategies 2-3 should use the original query, not the rewritten one."""
        mock_search = AsyncMock()
        mock_search.side_effect = [
            # Strategy 1: sparse (triggers strategy 2)
            [_sr("doc1", score=0.9)],
            # Strategy 2: more results
            [_sr(f"doc{i}", score=0.8 - i * 0.01) for i in range(2, 6)],
        ]

        # Set up rewrite client
        mock_rewrite_client = AsyncMock()
        mock_content_block = MagicMock()
        mock_content_block.text = "sick leave policy absence notification"
        mock_response = MagicMock()
        mock_response.content = [mock_content_block]
        mock_rewrite_client.messages.create = AsyncMock(return_value=mock_response)

        set_rewrite_client(mock_rewrite_client, "claude-haiku-4-5-20251001")

        original_query = "what happens if I'm sick?"

        with (
            patch("src.rag.tools.search_index", mock_search),
            patch("src.rag.tools._search_clients", [MagicMock()]),
        ):
            rag_tool = create_rag_tool(scope=RAGScope(domain="hr"))
            await rag_tool.invoke(arguments={"query": original_query, "document_type": None})

        # Strategy 1 should use rewritten query
        _, s1_kwargs = mock_search.call_args_list[0]
        assert s1_kwargs["query"] == "sick leave policy absence notification"

        # Strategy 2 should use the ORIGINAL query
        _, s2_kwargs = mock_search.call_args_list[1]
        assert s2_kwargs["query"] == original_query
