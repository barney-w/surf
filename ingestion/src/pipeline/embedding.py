"""Embedding generation — produces vector representations of text chunks."""

from __future__ import annotations

import asyncio
import logging

from openai import AzureOpenAI, RateLimitError

logger = logging.getLogger(__name__)


async def generate_embeddings(
    texts: list[str],
    client: AzureOpenAI,
    model: str = "text-embedding-3-large",
    batch_size: int = 16,
    max_retries: int = 5,
    progress_callback: callable = None,
) -> list[list[float]]:
    """Generate embeddings for a list of texts with batching and retry.

    Args:
        texts: The texts to embed.
        client: An ``AzureOpenAI`` client instance.
        model: The embedding model deployment name.
        batch_size: Number of texts per API call.
        max_retries: Maximum retry attempts on rate-limit errors.
        progress_callback: Optional callable(batch_num, total_batches) for progress reporting.

    Returns:
        A list of 3072-dimensional embedding vectors, one per input text.
    """
    if not texts:
        return []

    all_embeddings: list[list[float]] = []
    total_batches = (len(texts) + batch_size - 1) // batch_size

    for batch_num, batch_start in enumerate(range(0, len(texts), batch_size), start=1):
        batch = texts[batch_start : batch_start + batch_size]

        if progress_callback:
            progress_callback(batch_num, total_batches)

        for attempt in range(max_retries):
            try:
                response = await asyncio.to_thread(
                    client.embeddings.create,
                    input=batch,
                    model=model,
                )
                batch_embeddings = [item.embedding for item in response.data]
                all_embeddings.extend(batch_embeddings)
                await asyncio.sleep(0.5)
                break
            except RateLimitError:
                if attempt == max_retries - 1:
                    raise
                # Azure S0 tier requires at least 60s between retries on 429
                wait = max(65, 2 ** attempt)
                logger.warning(
                    "Rate limited on batch %d/%d, retrying in %ds (attempt %d/%d)",
                    batch_num,
                    total_batches,
                    wait,
                    attempt + 1,
                    max_retries,
                )
                await asyncio.sleep(wait)

    return all_embeddings
