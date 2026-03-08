"""Tests for the embedding generation module."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

from src.pipeline.embedding import generate_embeddings


@dataclass
class _EmbeddingItem:
    embedding: list[float]


@dataclass
class _EmbeddingResponse:
    data: list[_EmbeddingItem]


def _make_mock_client(dim: int = 3072):
    """Return a mock AzureOpenAI client that echoes back dummy embeddings."""
    client = MagicMock()

    def _create(*, input, model):  # noqa: A002
        items = [_EmbeddingItem(embedding=[0.1] * dim) for _ in input]
        return _EmbeddingResponse(data=items)

    client.embeddings.create = _create
    return client


class TestBatching:
    """Embedding batching logic."""

    def test_single_batch(self):
        """All texts fit in one batch."""
        client = _make_mock_client()
        texts = ["hello", "world"]
        result = asyncio.run(generate_embeddings(texts, client, batch_size=16))
        assert len(result) == 2
        assert len(result[0]) == 3072

    def test_multiple_batches(self):
        """Texts are split across multiple batches."""
        client = _make_mock_client()
        texts = [f"text {i}" for i in range(5)]
        result = asyncio.run(generate_embeddings(texts, client, batch_size=2))
        assert len(result) == 5

    def test_exact_batch_boundary(self):
        """Texts exactly fill batch_size multiples."""
        client = _make_mock_client()
        texts = [f"text {i}" for i in range(4)]
        result = asyncio.run(generate_embeddings(texts, client, batch_size=2))
        assert len(result) == 4


class TestRetry:
    """Rate-limit retry behaviour."""

    def test_retries_on_rate_limit(self):
        """Retries after a RateLimitError and eventually succeeds."""
        from openai import RateLimitError

        client = MagicMock()
        call_count = 0

        def _create(*, input, model):  # noqa: A002
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RateLimitError(
                    message="rate limited",
                    response=MagicMock(status_code=429, headers={}),
                    body=None,
                )
            items = [_EmbeddingItem(embedding=[0.5] * 3072) for _ in input]
            return _EmbeddingResponse(data=items)

        client.embeddings.create = _create

        with patch("src.pipeline.embedding.asyncio.sleep", return_value=None):
            result = asyncio.run(
                generate_embeddings(["test"], client, batch_size=16, max_retries=3)
            )
        assert len(result) == 1
        assert call_count == 2

    def test_raises_after_max_retries(self):
        """Raises RateLimitError after exhausting retries."""
        from openai import RateLimitError

        client = MagicMock()

        def _create(*, input, model):  # noqa: A002
            raise RateLimitError(
                message="rate limited",
                response=MagicMock(status_code=429, headers={}),
                body=None,
            )

        client.embeddings.create = _create

        with (
            patch("src.pipeline.embedding.asyncio.sleep", return_value=None),
            pytest.raises(RateLimitError),
        ):
            asyncio.run(generate_embeddings(["test"], client, batch_size=16, max_retries=2))


class TestEdgeCases:
    """Edge cases."""

    def test_empty_input(self):
        """Empty list returns empty list."""
        client = _make_mock_client()
        result = asyncio.run(generate_embeddings([], client))
        assert result == []
