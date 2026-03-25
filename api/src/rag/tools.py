import asyncio
import contextlib
import logging
import re
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Annotated

from agent_framework import FunctionTool, tool
from anthropic import AsyncAnthropic
from azure.search.documents.aio import SearchClient
from pydantic import Field

from src.agents._base import RAGScope
from src.config.settings import get_settings
from src.rag.search import (
    SearchIndexNotFoundError,
    SearchInfrastructureError,
    SearchResult,
    search_index,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Debug override dataclass and ContextVar
# ---------------------------------------------------------------------------


@dataclass
class SearchOverrides:
    """Optional overrides for RAG search parameters, populated from debug headers."""

    top_k: int | None = None
    strong_threshold: float | None = None
    partial_threshold: float | None = None
    enable_vector: bool | None = None
    enable_stitching: bool | None = None
    enable_broadened: bool | None = None
    enable_keyword: bool | None = None
    enable_rewrite: bool | None = None
    enable_proofread: bool | None = None


_search_overrides: ContextVar[SearchOverrides | None] = ContextVar("search_overrides", default=None)


# ---------------------------------------------------------------------------
# Debug info dataclass and ContextVar
# ---------------------------------------------------------------------------


@dataclass
class SearchDebugInfo:
    """Structured debug information captured during a search_knowledge_base() call."""

    original_query: str = ""
    rewritten_query: str | None = None
    strategies_used: list[str] = field(default_factory=list)
    results_per_strategy: dict[str, int] = field(default_factory=dict)
    total_results: int = 0
    tier_counts: dict[str, int] = field(default_factory=dict)


search_debug_info: ContextVar[SearchDebugInfo | None] = ContextVar("search_debug", default=None)


# ---------------------------------------------------------------------------
# Header parsing helpers
# ---------------------------------------------------------------------------


def _int_or_none(value: str | None) -> int | None:
    """Parse an integer from a header value, returning None on failure."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _float_or_none(value: str | None) -> float | None:
    """Parse a float from a header value, returning None on failure."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _bool_or_none(value: str | None) -> bool | None:
    """Parse a boolean from a header value, returning None on failure.

    Accepts ``"true"``/``"1"`` as truthy and ``"false"``/``"0"`` as falsy.
    """
    if value is None:
        return None
    lower = value.strip().lower()
    if lower in ("true", "1"):
        return True
    if lower in ("false", "0"):
        return False
    return None


def parse_debug_overrides(headers: dict[str, str]) -> SearchOverrides | None:
    """Parse ``X-Surf-Debug-*`` headers into a :class:`SearchOverrides` instance.

    Returns ``None`` when no debug headers are present.
    """
    prefix = "x-surf-debug-"
    debug_headers = {k.lower(): v for k, v in headers.items() if k.lower().startswith(prefix)}
    if not debug_headers:
        return None
    return SearchOverrides(
        top_k=_int_or_none(debug_headers.get(f"{prefix}topk")),
        strong_threshold=_float_or_none(debug_headers.get(f"{prefix}strongthreshold")),
        partial_threshold=_float_or_none(debug_headers.get(f"{prefix}partialthreshold")),
        enable_vector=_bool_or_none(debug_headers.get(f"{prefix}enablevector")),
        enable_stitching=_bool_or_none(debug_headers.get(f"{prefix}enablestitching")),
        enable_broadened=_bool_or_none(debug_headers.get(f"{prefix}enablebroadened")),
        enable_keyword=_bool_or_none(debug_headers.get(f"{prefix}enablekeyword")),
        enable_rewrite=_bool_or_none(debug_headers.get(f"{prefix}enablerewrite")),
        enable_proofread=_bool_or_none(debug_headers.get(f"{prefix}enableproofread")),
    )


_search_clients: list[SearchClient] = []

_rewrite_client: AsyncAnthropic | None = None
_rewrite_model_id: str = "claude-haiku-4-5-20251001"


def set_rewrite_client(client: AsyncAnthropic, model_id: str) -> None:
    """Configure the LLM client used for query rewriting."""
    global _rewrite_client, _rewrite_model_id  # noqa: PLW0603
    _rewrite_client = client
    _rewrite_model_id = model_id


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


async def rewrite_query_with_llm(query: str) -> str:
    """Rewrite a conversational query into an optimised search query using an LLM.

    Returns the original query unchanged if:
    - The rewrite client is not configured
    - The LLM call fails or times out (>2s)
    - The rewritten query is empty
    """
    if _rewrite_client is None:
        return query

    system_prompt = (
        "You are a search query optimiser. Rewrite the user's conversational "
        "question into a concise, keyword-rich search query optimised for "
        "hybrid search (BM25 + vector) against an enterprise knowledge base. "
        "Rules:\n"
        "- Extract the core information need\n"
        "- Use domain-specific terminology where possible\n"
        "- Remove conversational filler, pronouns, and pleasantries\n"
        "- Output ONLY the rewritten query — no explanation, no quotes\n"
        "- If the query is already well-formed for search, return it unchanged"
    )

    try:
        response = await asyncio.wait_for(
            _rewrite_client.messages.create(
                model=_rewrite_model_id,
                max_tokens=100,
                system=system_prompt,
                messages=[{"role": "user", "content": query}],
            ),
            timeout=2.0,
        )
        # Capture token usage from the query rewrite call.
        try:
            from src.orchestrator.builder import TokenUsage, token_usage_collector

            usage = TokenUsage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                model_id=_rewrite_model_id,
            )
            with contextlib.suppress(LookupError):
                token_usage_collector.get().append(usage)
        except Exception:
            pass  # Never let usage capture break the rewrite path

        rewritten = getattr(response.content[0], "text", "").strip()
        if rewritten:
            if get_settings().trace_prompt_content:
                logger.info(
                    "query_rewrite: %r -> %r",
                    query[:100],
                    rewritten[:100],
                )
            else:
                logger.info(
                    "query_rewrite: rewrote query (%d chars -> %d chars)",
                    len(query),
                    len(rewritten),
                )
            return rewritten
        return query
    except Exception:
        logger.warning("query_rewrite failed, using original query", exc_info=True)
        return query


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


async def verify_rag_connectivity() -> dict[str, str]:
    """Test search and embedding connectivity. Returns status dict.

    Intended for startup probes and health checks. Performs a minimal
    search query and embedding call to verify the full pipeline works.
    """
    results: dict[str, str] = {}

    # Test search
    if not _search_clients:
        results["search"] = "not_configured"
    else:
        try:
            await search_index(
                query="connectivity test",
                search_client=_search_clients,
                top_k=1,
                use_hybrid=False,
            )
            results["search"] = "ok"
        except SearchIndexNotFoundError as exc:
            results["search"] = f"index_not_found: {exc}"
        except SearchInfrastructureError as exc:
            results["search"] = f"error: {exc}"

    # Test embedding
    if _embed_func is None:
        results["embedding"] = "not_configured"
    else:
        try:
            await _embed_func("connectivity test")
            results["embedding"] = "ok"
        except Exception as exc:
            results["embedding"] = f"error: {type(exc).__name__}: {exc}"

    return results


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

        # Read debug overrides from the ContextVar (set per-request from headers).
        overrides = _search_overrides.get()

        min_confident_results = 3
        top_k = overrides.top_k if overrides and overrides.top_k is not None else 15

        # Determine whether to use hybrid (vector) search.
        use_hybrid = (
            overrides.enable_vector if overrides and overrides.enable_vector is not None else True
        )

        if get_settings().trace_prompt_content:
            logger.info(
                "search_knowledge_base called: query=%r document_type=%r filters=%r overrides=%r",
                query,
                document_type,
                filters,
                overrides,
            )
        else:
            logger.info(
                "search_knowledge_base called: query_len=%d document_type=%r filters=%r",
                len(query),
                document_type,
                filters,
            )

        # Initialise debug info for this search invocation.
        debug = SearchDebugInfo(original_query=query)
        with contextlib.suppress(LookupError):
            search_debug_info.set(debug)

        try:
            # Strategy 0: LLM query rewrite (before primary search)
            search_query = query
            run_rewrite = (
                overrides.enable_rewrite
                if overrides and overrides.enable_rewrite is not None
                else True
            )
            if run_rewrite:
                rewritten_query = await rewrite_query_with_llm(query)
                if rewritten_query != query:
                    search_query = rewritten_query
                    debug.rewritten_query = rewritten_query

            # Strategy 1: Primary hybrid search (current behaviour)
            results = await search_index(
                query=search_query,
                search_client=_get_search_clients(),
                filters=filters,
                top_k=top_k,
                use_hybrid=use_hybrid,
                embed_query=_embed_func,
            )
            debug.strategies_used.append("primary_hybrid")
            debug.results_per_strategy["primary_hybrid"] = len(results)

            # Strategy 2: Broadened filters — keep identity filters (domain
            # and content_source) but drop document_type constraints.
            # This widens the search within the agent's domain/source boundary.
            identity_keys = {"domain", "content_source"}
            broadened_filters: dict[str, str | list[str]] = {
                k: v for k, v in filters.items() if k in identity_keys
            }

            run_broadened = (
                overrides.enable_broadened
                if overrides and overrides.enable_broadened is not None
                else True
            )
            if run_broadened and len(results) < min_confident_results:
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
                    use_hybrid=use_hybrid,
                    embed_query=_embed_func,
                )
                debug.strategies_used.append("broadened_filters")
                debug.results_per_strategy["broadened_filters"] = len(broad_results)
                results = _merge_and_deduplicate(results, broad_results, top_k)

            # Strategy 3: Keyword extraction fallback — strip conversational
            # phrasing and search with extracted keywords
            run_keyword = (
                overrides.enable_keyword
                if overrides and overrides.enable_keyword is not None
                else True
            )
            if run_keyword and len(results) < min_confident_results:
                keywords = _extract_keywords(query)
                if keywords != query.lower().strip():
                    if get_settings().trace_prompt_content:
                        logger.info(
                            "search_knowledge_base: strategies 1+2 returned %d results "
                            "(< %d), trying keyword extraction: %r",
                            len(results),
                            min_confident_results,
                            keywords,
                        )
                    else:
                        logger.info(
                            "search_knowledge_base: strategies 1+2 returned %d results "
                            "(< %d), trying keyword extraction",
                            len(results),
                            min_confident_results,
                        )
                    kw_results = await search_index(
                        query=keywords,
                        search_client=_get_search_clients(),
                        filters=broadened_filters or None,
                        top_k=top_k,
                        use_hybrid=use_hybrid,
                        embed_query=_embed_func,
                    )
                    debug.strategies_used.append("keyword_extraction")
                    debug.results_per_strategy["keyword_extraction"] = len(kw_results)
                    results = _merge_and_deduplicate(results, kw_results, top_k)

        except SearchIndexNotFoundError as exc:
            return (
                "Knowledge search is unavailable because the configured Azure AI Search "
                f"index could not be found. Details: {exc}"
            )
        except SearchInfrastructureError as exc:
            logger.error("RAG infrastructure failure: %s", exc)
            return (
                "SEARCH_INFRASTRUCTURE_ERROR: The knowledge base search system is currently "
                "experiencing a technical issue and cannot retrieve documents. "
                f"Error: {exc}"
            )

        if not results:
            return "No relevant documents found for this query."

        # Chunk stitching (can be disabled via override).
        run_stitching = (
            overrides.enable_stitching
            if overrides and overrides.enable_stitching is not None
            else True
        )
        if run_stitching:
            results = stitch_adjacent_chunks(results)

        # Resolve threshold values, applying overrides when present.
        strong_threshold = (
            overrides.strong_threshold
            if overrides and overrides.strong_threshold is not None
            else 0.7
        )
        partial_threshold = (
            overrides.partial_threshold
            if overrides and overrides.partial_threshold is not None
            else 0.4
        )

        # Normalise scores within the result set so the top result is always 1.0.
        # This is robust to both keyword (BM25, scores ~0-10) and hybrid search
        # (RRF, scores ~0.001-0.1) which have different absolute scales.
        max_score = max(r.score for r in results) or 1.0
        formatted: list[str] = []
        tier_counts = {"strong": 0, "partial": 0, "weak": 0}
        for i, r in enumerate(results, 1):
            relevance = round(r.score / max_score, 2)
            if relevance >= strong_threshold:
                tier = "STRONG MATCH"
                tier_counts["strong"] += 1
            elif relevance >= partial_threshold:
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
        # Record final debug statistics after tier classification.
        debug.total_results = len(results)
        debug.tier_counts = dict(tier_counts)

        summary = (
            f"Found {len(results)} results "
            f"({tier_counts['strong']} strong, {tier_counts['partial']} partial, "
            f"{tier_counts['weak']} weak). "
            f"Base your answer on the strong and partial matches."
        )
        output = summary + "\n\n" + "\n\n".join(formatted)
        if get_settings().trace_prompt_content:
            logger.info(
                "search_knowledge_base returning %d results, first 200 chars: %r",
                len(results),
                output[:200],
            )
        else:
            logger.info("search_knowledge_base returning %d results", len(results))
        return output

    return search_knowledge_base
