"""Backfill content_source field on existing indexed documents.

Finds all documents in the Azure AI Search index where content_source is empty
and sets it to a specified value (default: "sharepoint") using merge_documents
to avoid overwriting other fields.

Usage:
    cd ingestion && uv run python scripts/backfill_content_source.py
    cd ingestion && uv run python scripts/backfill_content_source.py --dry-run
    cd ingestion && uv run python scripts/backfill_content_source.py --content-source website
"""

from __future__ import annotations

import os
from pathlib import Path

import click
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env")


def _resolve_index_name() -> str:
    """Resolve the search index name from environment variables."""
    return (
        os.environ.get("AZURE_SEARCH_INDEX_NAME")
        or os.environ.get("AZURE_SEARCH_INDEX")
        or "surf-index"
    )


def _resolve_endpoint() -> str:
    """Resolve the search endpoint from environment variables."""
    endpoint = os.environ.get("AZURE_SEARCH_ENDPOINT")
    if not endpoint:
        raise click.ClickException("AZURE_SEARCH_ENDPOINT environment variable is not set")
    return endpoint


@click.command()
@click.option(
    "--content-source",
    default="sharepoint",
    show_default=True,
    help="Value to set for the content_source field",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Count documents only, do not modify the index",
)
@click.option(
    "--batch-size",
    default=100,
    show_default=True,
    help="Number of documents to update per batch",
)
def backfill(content_source: str, dry_run: bool, batch_size: int) -> None:
    """Set content_source on all documents where it is currently empty."""
    endpoint = _resolve_endpoint()
    index_name = _resolve_index_name()

    click.echo(f"Index:          {index_name}")
    click.echo(f"Endpoint:       {endpoint}")
    click.echo(f"Content source: {content_source}")
    click.echo(f"Dry run:        {dry_run}")
    click.echo()

    credential = DefaultAzureCredential()
    client = SearchClient(
        endpoint=endpoint,
        index_name=index_name,
        credential=credential,
    )

    # Collect all document IDs with empty content_source
    click.echo("Searching for documents with empty content_source...")
    results = client.search(
        search_text="*",
        filter="content_source eq ''",
        select=["id"],
        top=1000,
    )

    doc_ids: list[str] = [doc["id"] for doc in results]
    total = len(doc_ids)

    click.echo(f"Found {total} document(s) with empty content_source.")

    if total == 0:
        click.echo("Nothing to do.")
        return

    if dry_run:
        click.echo("Dry run — no changes made.")
        return

    # Process in batches
    total_batches = (total + batch_size - 1) // batch_size
    updated = 0

    for batch_num in range(total_batches):
        start = batch_num * batch_size
        end = min(start + batch_size, total)
        batch_ids = doc_ids[start:end]

        documents = [{"id": doc_id, "content_source": content_source} for doc_id in batch_ids]
        result = client.merge_documents(documents=documents)

        succeeded = sum(1 for r in result if r.succeeded)
        failed = sum(1 for r in result if not r.succeeded)
        updated += succeeded

        click.echo(
            f"  Batch {batch_num + 1}/{total_batches}: "
            f"{succeeded} updated, {failed} failed "
            f"({end}/{total} processed)"
        )

        if failed > 0:
            for r in result:
                if not r.succeeded:
                    click.echo(f"    FAILED: {r.key} — {r.error_message}", err=True)

    click.echo()
    click.echo(f"Backfill complete. {updated}/{total} documents updated.")


if __name__ == "__main__":
    backfill()
