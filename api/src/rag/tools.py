import logging
import re
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Annotated

from agent_framework import FunctionTool, tool
from azure.search.documents.aio import SearchClient
from pydantic import Field

from src.agents._base import RAGScope
from src.rag.search import SearchIndexNotFoundError, SearchResult, search_index

logger = logging.getLogger(__name__)

_search_clients: list[SearchClient] = []

# Mutable collector for RAG tool output text during a request.
# The chat endpoint sets this to a fresh list before running the workflow;
# RAGCollectorMiddleware appends tool output.  Using a mutable list (not
# ContextVar.set()) ensures visibility even when the tool runs in a child
# async task.
rag_results_collector: ContextVar[list[str]] = ContextVar("rag_results")


def stitch_adjacent_chunks(results: list[SearchResult]) -> list[SearchResult]:
    """Merge consecutive chunks from the same document into a single result.

    When hybrid search returns chunk N and chunk N+1 from the same document,
    merging them gives the LLM complete context across what was a chunk boundary.
    The merged result inherits the higher of the two scores.
    """
    if not results:
        return results

    by_doc: dict[str, list[SearchResult]] = {}
    for r in results:
        by_doc.setdefault(r.document_id, []).append(r)

    stitched: list[SearchResult] = []
    for chunks in by_doc.values():
        chunks.sort(key=lambda c: c.chunk_index)

        # Walk through sorted chunks, merging consecutive runs.
        groups: list[list[SearchResult]] = []
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
                merged = SearchResult(
                    document_id=group[0].document_id,
                    title=group[0].title,
                    section_heading=group[0].section_heading,
                    content="\n\n".join(c.content for c in group),
                    score=max(c.score for c in group),
                    source_url=group[0].source_url,
                    domain=group[0].domain,
                    document_type=group[0].document_type,
                    content_source=group[0].content_source,
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


# Common stop words and conversational filler to strip from search queries.
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "shall",
        "can",
        "need",
        "must",
        "i",
        "me",
        "my",
        "we",
        "our",
        "you",
        "your",
        "they",
        "them",
        "their",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "what",
        "which",
        "who",
        "how",
        "when",
        "where",
        "why",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "by",
        "from",
        "about",
        "into",
        "through",
        "during",
        "before",
        "after",
        "between",
        "under",
        "and",
        "or",
        "but",
        "not",
        "no",
        "so",
        "if",
        "then",
        "tell",
        "know",
        "find",
        "get",
        "give",
        "show",
        "please",
        "thanks",
        "hi",
        "hello",
    }
)

_CONVERSATIONAL_PHRASES = [
    "tell me about",
    "what does .+ say about",
    "can you tell me",
    "i want to know",
    "i need to know",
    "in relation to",
    "with respect to",
    "regarding",
    "in relation to my role as",
    "as a",
]


def _extract_keywords(query: str) -> str:
    """Extract searchable keywords from a conversational query.

    Strips common stop words and conversational filler phrases,
    returning significant terms joined by spaces. Returns the
    original query unchanged if stripping would leave nothing.
    """
    cleaned = query.lower().strip()
    # Remove conversational phrases first (order: longest first to avoid partial matches)
    for phrase in sorted(_CONVERSATIONAL_PHRASES, key=len, reverse=True):
        cleaned = re.sub(phrase, " ", cleaned)
    # Remove stop words
    words = cleaned.split()
    keywords = [w for w in words if w not in _STOP_WORDS and len(w) > 1]
    result = " ".join(keywords).strip()
    return result if result else query


def _merge_and_deduplicate(
    primary: list[SearchResult],
    secondary: list[SearchResult],
    top_k: int,
) -> list[SearchResult]:
    """Merge two result lists, deduplicating by (document_id, chunk_index).

    Keeps the higher-scoring entry for duplicates. Returns results sorted
    by score descending, trimmed to top_k.
    """
    seen: dict[tuple[str, int], SearchResult] = {}
    for r in primary:
        key = (r.document_id, r.chunk_index)
        seen[key] = r
    for r in secondary:
        key = (r.document_id, r.chunk_index)
        if key not in seen or r.score > seen[key].score:
            seen[key] = r
    merged = sorted(seen.values(), key=lambda r: r.score, reverse=True)
    return merged[:top_k]


_embed_func: Callable[[str], Awaitable[list[float]]] | None = None


def set_search_client(client: SearchClient) -> None:
    """Add a search client (called once per index at startup)."""
    _search_clients.append(client)


def clear_search_clients() -> None:
    """Remove all search clients (used by tests)."""
    _search_clients.clear()


def set_embed_func(func: Callable[[str], Awaitable[list[float]]]) -> None:
    """Set the module-level embedding function (called once at startup)."""
    global _embed_func  # noqa: PLW0603
    _embed_func = func


def _get_search_clients() -> list[SearchClient]:
    """Return the module-level search clients, raising if none initialised."""
    if not _search_clients:
        msg = "Search client not initialised. Call set_search_client() during application startup."
        raise RuntimeError(msg)
    return _search_clients


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
            if scope.domain:
                filters["domain"] = scope.domain
            if scope.document_types:
                filters["document_type_in"] = scope.document_types
            filters.update(scope.metadata_filters)
        if document_type:
            filters["document_type"] = document_type

        min_confident_results = 3
        top_k = 15

        logger.info(
            "search_knowledge_base called: query=%r document_type=%r filters=%r",
            query,
            document_type,
            filters,
        )
        try:
            # Strategy 1: Primary hybrid search (current behaviour)
            results = await search_index(
                query=query,
                search_client=_get_search_clients(),
                filters=filters,
                top_k=top_k,
                use_hybrid=True,
                embed_query=_embed_func,
            )

            # Strategy 2: Broadened filters — keep identity filters (domain
            # and content_source) but drop document_type constraints.
            # This widens the search within the agent's domain/source boundary.
            identity_keys = {"domain", "content_source"}
            broadened_filters: dict[str, str | list[str]] = {
                k: v for k, v in filters.items() if k in identity_keys
            }

            if len(results) < min_confident_results:
                logger.info(
                    "search_knowledge_base: strategy 1 returned %d results (< %d), "
                    "broadening filters to %r",
                    len(results),
                    min_confident_results,
                    broadened_filters,
                )
                broad_results = await search_index(
                    query=query,
                    search_client=_get_search_clients(),
                    filters=broadened_filters or None,
                    top_k=top_k,
                    use_hybrid=True,
                    embed_query=_embed_func,
                )
                results = _merge_and_deduplicate(results, broad_results, top_k)

            # Strategy 3: Keyword extraction fallback — strip conversational
            # phrasing and search with extracted keywords
            if len(results) < min_confident_results:
                keywords = _extract_keywords(query)
                if keywords != query.lower().strip():
                    logger.info(
                        "search_knowledge_base: strategies 1+2 returned %d results "
                        "(< %d), trying keyword extraction: %r",
                        len(results),
                        min_confident_results,
                        keywords,
                    )
                    kw_results = await search_index(
                        query=keywords,
                        search_client=_get_search_clients(),
                        filters=broadened_filters or None,
                        top_k=top_k,
                        use_hybrid=True,
                        embed_query=_embed_func,
                    )
                    results = _merge_and_deduplicate(results, kw_results, top_k)

        except SearchIndexNotFoundError as exc:
            return (
                "Knowledge search is unavailable because the configured Azure AI Search "
                f"index could not be found. Details: {exc}"
            )

        if not results:
            return "No relevant documents found for this query."

        results = stitch_adjacent_chunks(results)

        # Normalise scores within the result set so the top result is always 1.0.
        # This is robust to both keyword (BM25, scores ~0-10) and hybrid search
        # (RRF, scores ~0.001-0.1) which have different absolute scales.
        max_score = max(r.score for r in results) or 1.0
        formatted: list[str] = []
        tier_counts = {"strong": 0, "partial": 0, "weak": 0}
        for i, r in enumerate(results, 1):
            relevance = round(r.score / max_score, 2)
            if relevance >= 0.7:
                tier = "STRONG MATCH"
                tier_counts["strong"] += 1
            elif relevance >= 0.4:
                tier = "PARTIAL MATCH"
                tier_counts["partial"] += 1
            else:
                tier = "WEAK MATCH"
                tier_counts["weak"] += 1
            section_line = (
                f'section: "{r.section_heading}"' if r.section_heading else "section: null"
            )
            url_line = f'url: "{r.source_url}"' if r.source_url else "url: null"
            source_line = (
                f'content_source: "{r.content_source}"'
                if r.content_source
                else "content_source: null"
            )
            snippet_text = r.content[:200].rstrip()
            if len(r.content) > 200:
                snippet_text += "..."
            formatted.append(
                f"=== SOURCE {i} ===\n"
                f'title: "{r.title}"\n'
                f"{section_line}\n"
                f'document_id: "{r.document_id}"\n'
                f"relevance: {relevance} ({tier})\n"
                f"{url_line}\n"
                f"{source_line}\n"
                f'snippet: "{snippet_text}"\n\n'
                f"CONTENT:\n{r.content}\n\n"
                f"=== END SOURCE {i} ==="
            )
        summary = (
            f"Found {len(results)} results "
            f"({tier_counts['strong']} strong, {tier_counts['partial']} partial, "
            f"{tier_counts['weak']} weak). "
            f"Base your answer on the strong and partial matches."
        )
        output = summary + "\n\n" + "\n\n".join(formatted)
        logger.info(
            "search_knowledge_base returning %d results, first 200 chars: %r",
            len(results),
            output[:200],
        )
        return output

    return search_knowledge_base
