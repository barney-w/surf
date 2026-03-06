import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from azure.core.exceptions import ResourceNotFoundError
from azure.search.documents.aio import SearchClient
from azure.search.documents.models import VectorizedQuery

logger = logging.getLogger(__name__)


class SearchIndexNotFoundError(RuntimeError):
    """Raised when the configured Azure AI Search index does not exist."""


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


async def search_index(
    query: str,
    search_client: SearchClient,
    filters: dict[str, str | list[str]] | None = None,
    top_k: int = 5,
    use_hybrid: bool = True,
    embed_query: Callable[[str], Awaitable[list[float]]] | None = None,
) -> list[SearchResult]:
    """Execute hybrid search (vector + keyword) against Azure AI Search.

    When *use_hybrid* is ``True`` and *embed_query* is provided the query is
    embedded client-side and submitted as a ``RawVectorQuery``.  If
    *embed_query* is ``None`` the search falls back to keyword-only mode.
    """
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

        results = await search_client.search(
            search_text=query,
            filter=odata_filter,
            top=top_k,
            vector_queries=vector_queries,
        )

        search_results: list[SearchResult] = []
        async for doc in results:
            search_results.append(
                SearchResult(
                    document_id=doc.get("document_id", ""),
                    title=doc.get("title", ""),
                    section_heading=doc.get("section_heading"),
                    content=doc.get("content", ""),
                    score=float(doc.get("@search.score", 0.0)),
                    source_url=doc.get("source_url"),
                    domain=doc.get("domain", ""),
                    document_type=doc.get("document_type", ""),
                    chunk_index=int(doc.get("chunk_index", 0)),
                )
            )
        return search_results
    except ResourceNotFoundError as exc:
        logger.warning(
            "Configured Azure AI Search index was not found for query=%r",
            query[:200] if query else query,
        )
        raise SearchIndexNotFoundError(str(exc)) from exc
    except Exception:
        logger.warning(
            "Search query failed for query=%r — returning empty results",
            query[:200] if query else query,
            exc_info=True,
        )
        return []
