import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, cast

from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
from azure.search.documents.aio import SearchClient
from azure.search.documents.models import VectorizedQuery, VectorQuery

logger = logging.getLogger(__name__)


class SearchIndexNotFoundError(RuntimeError):
    """Raised when the configured Azure AI Search index does not exist."""


class SearchInfrastructureError(RuntimeError):
    """Raised when search fails due to infrastructure issues (auth, network, etc).

    Distinguished from SearchIndexNotFoundError (misconfiguration) and empty
    results (legitimate). Callers should NOT swallow this — it indicates the
    RAG pipeline is non-functional.
    """


@dataclass
class SearchResult:
    """A single result from the Azure AI Search index."""

    document_id: str
    title: str
    section_heading: str | None
    content: str
    score: float
    source_url: str | None
    domain: str
    document_type: str
    content_source: str = ""
    chunk_index: int = 0


def build_odata_filter(filters: dict[str, str | list[str]]) -> str | None:
    """Convert a filter dict to an OData filter string.

    Supported patterns:
      - ``{"domain": "hr"}``  ->  ``"domain eq 'hr'"``
      - ``{"document_type_in": ["policy", "agreement"]}``
        ->  ``"search.in(document_type, 'policy,agreement')"``
    Keys ending with ``_in`` are treated as ``search.in()`` filters on the
    field name derived by stripping the ``_in`` suffix.
    """
    clauses: list[str] = []
    for key, value in filters.items():
        if key.endswith("_in") and isinstance(value, list):
            field = key.removesuffix("_in")
            escaped = [v.replace("'", "''") for v in value]
            joined = ",".join(escaped)
            clauses.append(f"search.in({field}, '{joined}')")
        elif isinstance(value, str):
            escaped_value = value.replace("'", "''")
            clauses.append(f"{key} eq '{escaped_value}'")
    if not clauses:
        return None
    return " and ".join(clauses)


async def _search_single_index(
    query: str,
    search_client: SearchClient,
    odata_filter: str | None,
    top_k: int,
    vector_queries: list[VectorizedQuery] | None,
) -> list[SearchResult]:
    """Query a single search index and return results."""
    results = await search_client.search(  # pyright: ignore[reportUnknownVariableType,reportUnknownMemberType]
        search_text=query,
        filter=odata_filter,
        top=top_k,
        vector_queries=cast("list[VectorQuery]", vector_queries) if vector_queries else None,
    )

    search_results: list[SearchResult] = []
    async for raw_doc in results:  # pyright: ignore[reportUnknownVariableType]
        doc: dict[str, Any] = dict(raw_doc)  # pyright: ignore[reportUnknownArgumentType]
        search_results.append(
            SearchResult(
                document_id=doc.get("document_id", doc.get("parent_id", "")),
                title=doc.get("title", ""),
                section_heading=doc.get("section_heading"),
                content=doc.get("content", ""),
                score=float(doc.get("@search.score") or 0.0),
                source_url=doc.get("source_url"),
                domain=doc.get("domain", ""),
                document_type=doc.get("document_type", ""),
                content_source=doc.get("content_source", ""),
                chunk_index=int(doc.get("chunk_index") or 0),
            )
        )
    return search_results


async def search_index(
    query: str,
    search_client: SearchClient | Sequence[SearchClient],
    filters: dict[str, str | list[str]] | None = None,
    top_k: int = 5,
    use_hybrid: bool = True,
    embed_query: Callable[[str], Awaitable[list[float]]] | None = None,
) -> list[SearchResult]:
    """Execute hybrid search (vector + keyword) against Azure AI Search.

    Accepts a single ``SearchClient`` or a sequence of clients to query
    multiple indexes concurrently.  Results are merged by score and
    trimmed to *top_k*.

    When *use_hybrid* is ``True`` and *embed_query* is provided the query is
    embedded client-side and submitted as a ``RawVectorQuery``.  If
    *embed_query* is ``None`` the search falls back to keyword-only mode.
    """
    clients: Sequence[SearchClient] = (
        search_client if isinstance(search_client, Sequence) else [search_client]
    )

    try:
        odata_filter = build_odata_filter(filters) if filters else None

        vector_queries: list[VectorizedQuery] | None = None
        if use_hybrid:
            if embed_query is not None:
                vector = await embed_query(query)
                vector_queries = [
                    VectorizedQuery(
                        vector=vector,
                        k_nearest_neighbors=top_k,
                        fields="content_vector",
                    )
                ]
            else:
                logger.debug(
                    "use_hybrid=True but no embed_query provided — "
                    "falling back to keyword-only search"
                )

        if len(clients) == 1:
            return await _search_single_index(
                query, clients[0], odata_filter, top_k, vector_queries
            )

        # Query all indexes concurrently and merge results.
        tasks = [
            _search_single_index(query, c, odata_filter, top_k, vector_queries) for c in clients
        ]
        all_results = await asyncio.gather(*tasks, return_exceptions=True)

        merged: list[SearchResult] = []
        failures: list[BaseException] = []
        for result_or_exc in all_results:
            if isinstance(result_or_exc, BaseException):
                failures.append(result_or_exc)
                logger.warning("Search query failed for one index: %s", result_or_exc)
                continue
            merged.extend(result_or_exc)

        # If every index failed, surface the first error rather than
        # silently returning empty results.
        if failures and not merged:
            first = failures[0]
            if isinstance(first, ResourceNotFoundError):
                raise SearchIndexNotFoundError(str(first)) from first
            raise SearchInfrastructureError(str(first)) from first

        merged.sort(key=lambda r: r.score, reverse=True)
        return merged[:top_k]

    except ResourceNotFoundError as exc:
        logger.warning(
            "Configured Azure AI Search index was not found for query=%r",
            query[:200] if query else query,
        )
        raise SearchIndexNotFoundError(str(exc)) from exc
    except HttpResponseError as exc:
        logger.error(
            "Search infrastructure error",
            extra={
                "event": "rag_infrastructure_error",
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "query": query[:200] if query else query,
            },
            exc_info=True,
        )
        raise SearchInfrastructureError(str(exc)) from exc
    except Exception as exc:
        logger.error(
            "Unexpected search error",
            extra={
                "event": "rag_infrastructure_error",
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "query": query[:200] if query else query,
            },
            exc_info=True,
        )
        raise SearchInfrastructureError(str(exc)) from exc
