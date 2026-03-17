"""Azure AI Search indexing — index management and chunk upload."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    SearchableField,  # pyright: ignore[reportUnknownVariableType]
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SimpleField,  # pyright: ignore[reportUnknownVariableType]
    VectorSearch,
    VectorSearchProfile,
)

if TYPE_CHECKING:
    from azure.search.documents import SearchClient
    from azure.search.documents.indexes import SearchIndexClient

logger = logging.getLogger(__name__)

VECTOR_DIMENSIONS = 3072

INDEX_FIELDS = [
    SimpleField(name="id", type=SearchFieldDataType.String, key=True, filterable=True),
    SimpleField(name="document_id", type=SearchFieldDataType.String, filterable=True),
    SimpleField(name="domain", type=SearchFieldDataType.String, filterable=True, facetable=True),
    SimpleField(name="document_type", type=SearchFieldDataType.String, filterable=True),
    SimpleField(
        name="content_source", type=SearchFieldDataType.String, filterable=True, facetable=True,
    ),
    SearchableField(name="section_path", type=SearchFieldDataType.String),
    SearchableField(name="title", type=SearchFieldDataType.String),
    SearchableField(name="section_heading", type=SearchFieldDataType.String),
    SearchableField(name="content", type=SearchFieldDataType.String),
    SearchField(
        name="content_vector",
        type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
        searchable=True,
        vector_search_dimensions=VECTOR_DIMENSIONS,
        vector_search_profile_name="default-vector-profile",
    ),
    SimpleField(name="chunk_index", type=SearchFieldDataType.Int32, filterable=True, sortable=True),
    SimpleField(name="source_url", type=SearchFieldDataType.String, filterable=False),
    SimpleField(name="effective_date", type=SearchFieldDataType.DateTimeOffset, filterable=True),
    SimpleField(name="metadata", type=SearchFieldDataType.String, filterable=False),
]


def create_or_update_index(index_client: SearchIndexClient, index_name: str) -> None:
    """Create or update the AI Search index with the required schema."""
    vector_search = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name="default-hnsw")],
        profiles=[
            VectorSearchProfile(
                name="default-vector-profile",
                algorithm_configuration_name="default-hnsw",
            )
        ],
    )

    index = SearchIndex(
        name=index_name,
        fields=INDEX_FIELDS,
        vector_search=vector_search,
    )

    index_client.create_or_update_index(index)
    logger.info("Index '%s' created or updated.", index_name)


def _chunk_to_document(chunk: dict[str, Any]) -> dict[str, Any]:
    """Convert a chunk dict to a search document dict.

    Ensures metadata is serialised as a JSON string and effective_date is
    ISO-formatted if present.
    """
    doc = {
        "id": chunk["id"],
        "document_id": chunk["document_id"],
        "domain": chunk.get("domain", ""),
        "document_type": chunk.get("document_type", ""),
        "content_source": chunk.get("content_source", ""),
        "section_path": chunk.get("section_path", ""),
        "title": chunk.get("title", ""),
        "section_heading": chunk.get("section_heading") or "",
        "content": chunk.get("content", ""),
        "content_vector": chunk.get("content_vector", []),
        "chunk_index": chunk.get("chunk_index", 0),
        "source_url": chunk.get("source_url") or "",
        "effective_date": chunk.get("effective_date"),
        "metadata": (
            chunk.get("metadata")
            if isinstance(chunk.get("metadata"), str)
            else json.dumps(chunk.get("metadata", {}))
        ),
    }
    return doc


async def upload_chunks(
    search_client: SearchClient,
    chunks: list[dict[str, Any]],
    batch_size: int = 100,
) -> int:
    """Upload chunks to the search index. Returns count of uploaded documents."""
    if not chunks:
        return 0

    total_uploaded = 0

    for batch_start in range(0, len(chunks), batch_size):
        batch = chunks[batch_start : batch_start + batch_size]
        documents = [_chunk_to_document(c) for c in batch]
        result = search_client.upload_documents(documents=documents)  # pyright: ignore[reportUnknownMemberType]
        succeeded = sum(1 for r in result if r.succeeded)
        total_uploaded += succeeded
        logger.info(
            "Uploaded batch %d-%d: %d/%d succeeded.",
            batch_start,
            batch_start + len(batch),
            succeeded,
            len(batch),
        )

    return total_uploaded
