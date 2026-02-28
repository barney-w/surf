"""Tests for the indexing module."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from unittest.mock import MagicMock, call

from src.pipeline.indexing import (
    INDEX_FIELDS,
    VECTOR_DIMENSIONS,
    _chunk_to_document,
    create_or_update_index,
    upload_chunks,
)


class TestCreateOrUpdateIndex:
    """Index creation / update tests."""

    def test_calls_create_or_update(self):
        """Calls create_or_update_index on the index client."""
        index_client = MagicMock()
        create_or_update_index(index_client, "test-index")
        index_client.create_or_update_index.assert_called_once()

    def test_index_has_correct_name(self):
        """The created index carries the requested name."""
        index_client = MagicMock()
        create_or_update_index(index_client, "my-index")
        created_index = index_client.create_or_update_index.call_args[0][0]
        assert created_index.name == "my-index"

    def test_index_schema_includes_all_required_fields(self):
        """The schema contains every required field name."""
        required = {
            "id",
            "document_id",
            "domain",
            "document_type",
            "title",
            "section_heading",
            "content",
            "content_vector",
            "chunk_index",
            "source_url",
            "effective_date",
            "metadata",
        }
        field_names = {f.name for f in INDEX_FIELDS}
        assert required == field_names

    def test_content_vector_dimensions(self):
        """content_vector has 3072 dimensions."""
        assert VECTOR_DIMENSIONS == 3072
        vector_field = next(f for f in INDEX_FIELDS if f.name == "content_vector")
        assert vector_field.vector_search_dimensions == 3072

    def test_domain_is_filterable_and_facetable(self):
        """domain field is filterable and facetable."""
        domain_field = next(f for f in INDEX_FIELDS if f.name == "domain")
        assert domain_field.filterable is True
        assert domain_field.facetable is True


class TestUploadChunks:
    """Chunk upload tests."""

    def _make_result(self, succeeded: bool = True):
        r = MagicMock()
        r.succeeded = succeeded
        return r

    def test_upload_returns_count(self):
        """upload_chunks returns the number of succeeded uploads."""
        client = MagicMock()
        client.upload_documents.return_value = [self._make_result(True)] * 2

        chunks = [
            {"id": "a", "document_id": "d1", "content": "hello", "content_vector": [0.1] * 3072},
            {"id": "b", "document_id": "d1", "content": "world", "content_vector": [0.2] * 3072},
        ]
        count = asyncio.run(upload_chunks(client, chunks))
        assert count == 2

    def test_upload_batching(self):
        """Chunks are split into batches of the specified size."""
        client = MagicMock()
        client.upload_documents.return_value = [self._make_result(True)]

        chunks = [
            {"id": str(i), "document_id": "d1", "content": f"text {i}"}
            for i in range(5)
        ]
        asyncio.run(upload_chunks(client, chunks, batch_size=2))
        assert client.upload_documents.call_count == 3  # 2+2+1

    def test_empty_chunks(self):
        """Empty list returns zero."""
        client = MagicMock()
        count = asyncio.run(upload_chunks(client, []))
        assert count == 0


class TestChunkToDocument:
    """_chunk_to_document conversion tests."""

    def test_converts_metadata_dict_to_json(self):
        """Dict metadata is serialised to a JSON string."""
        chunk = {
            "id": "c1",
            "document_id": "d1",
            "content": "text",
            "metadata": {"domain": "hr"},
        }
        doc = _chunk_to_document(chunk)
        assert doc["metadata"] == json.dumps({"domain": "hr"})

    def test_preserves_string_metadata(self):
        """String metadata is kept as-is."""
        chunk = {
            "id": "c1",
            "document_id": "d1",
            "content": "text",
            "metadata": '{"already": "json"}',
        }
        doc = _chunk_to_document(chunk)
        assert doc["metadata"] == '{"already": "json"}'
