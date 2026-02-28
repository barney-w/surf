import logging
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Annotated

from agent_framework import FunctionTool, tool
from azure.search.documents.aio import SearchClient
from pydantic import Field

from src.agents._base import RAGScope
from src.rag.search import SearchIndexNotFoundError, search_index

logger = logging.getLogger(__name__)

_search_client: SearchClient | None = None

# Mutable collector for RAG tool output text during a request.
# The chat endpoint sets this to a fresh list before running the workflow;
# the tool appends its output.  Using a mutable list (not ContextVar.set())
# ensures visibility even when the tool runs in a child async task.
rag_results_collector: ContextVar[list[str]] = ContextVar("rag_results")


def _stitch_adjacent_chunks(results: list) -> list:
    """Merge consecutive chunks from the same document into a single result.

    When hybrid search returns chunk N and chunk N+1 from the same document,
    merging them gives the LLM complete context across what was a chunk boundary.
    The merged result inherits the higher of the two scores.
    """
    if not results:
        return results

    by_doc: dict[str, list] = {}
    for r in results:
        by_doc.setdefault(r.document_id, []).append(r)

    stitched = []
    for chunks in by_doc.values():
        chunks.sort(key=lambda c: c.chunk_index)

        # Walk through sorted chunks, merging consecutive runs.
        groups: list[list] = []
        current_group = [chunks[0]]
        for chunk in chunks[1:]:
            if chunk.chunk_index == current_group[-1].chunk_index + 1:
                current_group.append(chunk)
            else:
                groups.append(current_group)
                current_group = [chunk]
        groups.append(current_group)

        for group in groups:
            if len(group) == 1:
                stitched.append(group[0])
            else:
                from src.rag.search import SearchResult
                merged = SearchResult(
                    document_id=group[0].document_id,
                    title=group[0].title,
                    section_heading=group[0].section_heading,
                    content="\n\n".join(c.content for c in group),
                    score=max(c.score for c in group),
                    source_url=group[0].source_url,
                    domain=group[0].domain,
                    document_type=group[0].document_type,
                    chunk_index=group[0].chunk_index,
                )
                stitched.append(merged)
                logger.debug(
                    "_stitch_adjacent_chunks: merged %d chunks for doc=%s (idx %d-%d)",
                    len(group),
                    group[0].document_id,
                    group[0].chunk_index,
                    group[-1].chunk_index,
                )

    stitched.sort(key=lambda r: r.score, reverse=True)
    return stitched
_embed_func: Callable[[str], Awaitable[list[float]]] | None = None


def set_search_client(client: SearchClient) -> None:
    """Set the module-level search client (called once at startup)."""
    global _search_client  # noqa: PLW0603
    _search_client = client


def set_embed_func(func: Callable[[str], Awaitable[list[float]]]) -> None:
    """Set the module-level embedding function (called once at startup)."""
    global _embed_func  # noqa: PLW0603
    _embed_func = func


def _get_search_client() -> SearchClient:
    """Return the module-level search client, raising if not initialised."""
    if _search_client is None:
        msg = (
            "Search client not initialised. "
            "Call set_search_client() during application startup."
        )
        raise RuntimeError(msg)
    return _search_client


def create_rag_tool(scope: RAGScope | None = None) -> FunctionTool:
    """Factory that creates a scoped RAG search tool for an agent."""

    @tool(name="search_knowledge_base")
    async def search_knowledge_base(
        query: Annotated[
            str,
            Field(description="Search query describing what information is needed"),
        ],
        document_type: Annotated[
            str | None,
            Field(description="Optional filter: policy, procedure, agreement, guideline, form"),
        ] = None,
    ) -> str:
        """Search the knowledge base for policies, procedures, and documents."""
        filters: dict[str, str | list[str]] = {}
        if scope:
            filters["domain"] = scope.domain
            if scope.document_types:
                filters["document_type_in"] = scope.document_types
        if document_type:
            filters["document_type"] = document_type

        logger.info("search_knowledge_base called: query=%r document_type=%r filters=%r", query, document_type, filters)
        try:
            results = await search_index(
                query=query,
                search_client=_get_search_client(),
                filters=filters,
                top_k=8,
                use_hybrid=True,
                embed_query=_embed_func,
            )
        except SearchIndexNotFoundError as exc:
            return (
                "Knowledge search is unavailable because the configured Azure AI Search "
                f"index could not be found. Details: {exc}"
            )

        if not results:
            return "No relevant documents found for this query."

        results = _stitch_adjacent_chunks(results)

        # Normalise scores within the result set so the top result is always 1.0.
        # This is robust to both keyword (BM25, scores ~0-10) and hybrid search
        # (RRF, scores ~0.001-0.1) which have different absolute scales.
        max_score = max(r.score for r in results) or 1.0
        formatted = []
        for i, r in enumerate(results, 1):
            relevance = round(r.score / max_score, 2)
            section_line = f'section: "{r.section_heading}"' if r.section_heading else "section: null"
            url_line = f'url: "{r.source_url}"' if r.source_url else "url: null"
            snippet_text = r.content[:200].rstrip()
            if len(r.content) > 200:
                snippet_text += "..."
            formatted.append(
                f"=== SOURCE {i} ===\n"
                f'title: "{r.title}"\n'
                f"{section_line}\n"
                f'document_id: "{r.document_id}"\n'
                f"relevance: {relevance}\n"
                f"{url_line}\n"
                f'snippet: "{snippet_text}"\n\n'
                f"CONTENT:\n{r.content}\n\n"
                f"=== END SOURCE {i} ==="
            )
        output = "\n\n".join(formatted)
        try:
            rag_results_collector.get().append(output)
        except LookupError:
            pass  # collector not initialised — no-op outside chat endpoints
        logger.info("search_knowledge_base returning %d results, first 200 chars: %r", len(results), output[:200])
        return output

    return search_knowledge_base
