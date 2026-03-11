"""Diagnose the SharePoint indexing pipeline end-to-end.

Checks four areas:
1. Blob storage — counts SharePoint blobs (files and pages), lists a sample
2. Search index — document count and sample documents with key fields
3. Indexer status — last run time, status, errors
4. Discrepancy check — compares blob count vs index document count

Usage:
    cd ingestion && uv run python scripts/diagnose_sharepoint.py
    cd ingestion && uv run python scripts/diagnose_sharepoint.py --blob-sample 10 --index-sample 10
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import click
import httpx
from azure.identity import DefaultAzureCredential
from azure.storage.blob import ContainerClient
from dotenv import load_dotenv

from scripts.search_api import SearchApiClient

load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_env(name: str, default: str | None = None) -> str:
    val = os.environ.get(name, default or "")
    if not val:
        click.echo(f"ERROR: {name} environment variable is required", err=True)
        sys.exit(1)
    return val


def _resolve_index_name() -> str:
    return os.environ.get("AZURE_SEARCH_SHAREPOINT_INDEX", "surf-sharepoint-index")


def _resolve_indexer_name(index_name: str) -> str:
    return f"{index_name}-indexer"


def _header(title: str) -> None:
    click.echo("")
    click.echo("=" * 60)
    click.echo(f"  {title}")
    click.echo("=" * 60)


def _sub_header(title: str) -> None:
    click.echo(f"\n--- {title} ---")


# ---------------------------------------------------------------------------
# 1. Blob Storage
# ---------------------------------------------------------------------------


def check_blob_storage(sample_size: int) -> dict[str, int]:
    """Check blob storage for SharePoint blobs. Returns blob counts by category."""
    _header("BLOB STORAGE")

    account_url = _get_env("AZURE_STORAGE_ACCOUNT_URL")
    container_name = os.environ.get("AZURE_STORAGE_CONTAINER", "documents")
    blob_prefix = os.environ.get("AZURE_STORAGE_BLOB_PREFIX", "sharepoint/")

    click.echo(f"  Account:   {account_url}")
    click.echo(f"  Container: {container_name}")
    click.echo(f"  Prefix:    {blob_prefix}")

    credential = DefaultAzureCredential()
    container = ContainerClient(
        account_url=account_url,
        container_name=container_name,
        credential=credential,
    )

    files_prefix = f"{blob_prefix}files/"
    pages_prefix = f"{blob_prefix}pages/"

    # Count files
    file_blobs: list[str] = []
    for blob in container.list_blobs(name_starts_with=files_prefix):
        file_blobs.append(blob.name)

    # Count pages
    page_blobs: list[str] = []
    for blob in container.list_blobs(name_starts_with=pages_prefix):
        page_blobs.append(blob.name)

    total = len(file_blobs) + len(page_blobs)

    _sub_header("Blob Counts")
    click.echo(f"  Files: {len(file_blobs)}")
    click.echo(f"  Pages: {len(page_blobs)}")
    click.echo(f"  Total: {total}")

    if file_blobs:
        _sub_header(f"Sample File Blobs (up to {sample_size})")
        for name in file_blobs[:sample_size]:
            click.echo(f"    {name}")

    if page_blobs:
        _sub_header(f"Sample Page Blobs (up to {sample_size})")
        for name in page_blobs[:sample_size]:
            click.echo(f"    {name}")

    if not total:
        click.echo("\n  WARNING: No SharePoint blobs found in storage.")

    return {"files": len(file_blobs), "pages": len(page_blobs), "total": total}


# ---------------------------------------------------------------------------
# 2. Search Index
# ---------------------------------------------------------------------------


def check_search_index(api: SearchApiClient, index_name: str, sample_size: int) -> int:
    """Check the search index for document count and sample documents. Returns count."""
    _header("SEARCH INDEX")

    click.echo(f"  Index: {index_name}")

    # Document count
    resp = api.request("GET", f"indexes/{index_name}/docs/$count")
    if resp.status_code != 200:
        click.echo(f"  ERROR: Failed to get document count: {resp.status_code} {resp.text}")
        return 0

    doc_count = int(resp.text)
    click.echo(f"  Document count: {doc_count}")

    if doc_count == 0:
        click.echo("  WARNING: Index is empty — no documents found.")
        return 0

    # Sample documents
    _sub_header(f"Sample Documents (up to {sample_size})")

    search_body: dict[str, Any] = {
        "search": "*",
        "top": sample_size,
        "select": "title,domain,document_type,source_type,source_url,content",
    }
    resp = api.request("POST", f"indexes/{index_name}/docs/search", search_body)
    if resp.status_code != 200:
        click.echo(f"  ERROR: Failed to search index: {resp.status_code} {resp.text}")
        return doc_count

    results = resp.json().get("value", [])
    for i, doc in enumerate(results):
        title = doc.get("title", "(none)")
        domain = doc.get("domain", "(none)")
        document_type = doc.get("document_type", "(none)")
        source_type = doc.get("source_type", "(none)")
        source_url = doc.get("source_url", "(none)")
        content = doc.get("content", "") or ""
        content_preview = content[:200].replace("\n", " ")
        if len(content) > 200:
            content_preview += "..."

        click.echo(f"\n  [{i + 1}] {title}")
        click.echo(f"      domain:        {domain}")
        click.echo(f"      document_type: {document_type}")
        click.echo(f"      source_type:   {source_type}")
        click.echo(f"      source_url:    {source_url}")
        click.echo(f"      content:       {content_preview}")

    return doc_count


# ---------------------------------------------------------------------------
# 3. Indexer Status
# ---------------------------------------------------------------------------


def check_indexer_status(api: SearchApiClient, index_name: str) -> None:
    """Check the Azure AI Search indexer status."""
    _header("INDEXER STATUS")

    indexer_name = _resolve_indexer_name(index_name)
    click.echo(f"  Indexer: {indexer_name}")

    resp = api.request("GET", f"indexers/{indexer_name}/status")
    if resp.status_code != 200:
        click.echo(f"  ERROR: Failed to get indexer status: {resp.status_code}")
        if resp.status_code == 404:
            click.echo(f"  Indexer '{indexer_name}' does not exist.")
        else:
            click.echo(f"  Response: {resp.text[:500]}")
        return

    data = resp.json()
    overall_status = data.get("status", "unknown")
    click.echo(f"  Overall status: {overall_status}")

    last_result = data.get("lastResult")
    if not last_result:
        click.echo("  No previous run found.")
        return

    _sub_header("Last Run")
    click.echo(f"  Status:          {last_result.get('status', 'unknown')}")
    click.echo(f"  Start time:      {last_result.get('startTime', 'unknown')}")
    click.echo(f"  End time:        {last_result.get('endTime', 'unknown')}")
    click.echo(f"  Items processed: {last_result.get('itemsProcessed', 0)}")
    click.echo(f"  Items failed:    {last_result.get('itemsFailed', 0)}")

    error_message = last_result.get("errorMessage")
    if error_message:
        _sub_header("Last Run Error")
        click.echo(f"  {error_message}")

    # Show per-item errors if any
    errors = last_result.get("errors", [])
    warnings = last_result.get("warnings", [])

    if errors:
        _sub_header(f"Errors ({len(errors)})")
        for err in errors[:10]:
            key = err.get("key", "(unknown key)")
            msg = err.get("errorMessage", "(no message)")
            click.echo(f"  - [{key}] {msg}")
        if len(errors) > 10:
            click.echo(f"  ... and {len(errors) - 10} more errors")

    if warnings:
        _sub_header(f"Warnings ({len(warnings)})")
        for warn in warnings[:10]:
            key = warn.get("key", "(unknown key)")
            msg = warn.get("message", "(no message)")
            click.echo(f"  - [{key}] {msg}")
        if len(warnings) > 10:
            click.echo(f"  ... and {len(warnings) - 10} more warnings")

    if not errors and not warnings:
        click.echo("  No errors or warnings.")

    # Execution history summary
    execution_history = data.get("executionHistory", [])
    if execution_history:
        _sub_header(f"Recent Execution History ({len(execution_history)} runs)")
        for run in execution_history[:5]:
            status = run.get("status", "unknown")
            start = run.get("startTime", "unknown")
            processed = run.get("itemsProcessed", 0)
            failed = run.get("itemsFailed", 0)
            click.echo(f"  {start}  {status:20s}  processed={processed}  failed={failed}")


# ---------------------------------------------------------------------------
# 4. Discrepancy Check
# ---------------------------------------------------------------------------


def check_discrepancies(blob_counts: dict[str, int], index_doc_count: int) -> None:
    """Compare blob count vs index document count and flag issues."""
    _header("DISCREPANCY CHECK")

    blob_total = blob_counts["total"]
    click.echo(f"  Blob count (total):  {blob_total}")
    click.echo(f"  Index document count: {index_doc_count}")

    if blob_total == 0 and index_doc_count == 0:
        click.echo("\n  RESULT: Both blob storage and index are empty.")
        click.echo("  ACTION: Run the SharePoint sync connector to populate blobs,")
        click.echo("          then trigger the indexer.")
        return

    if blob_total == 0 and index_doc_count > 0:
        click.echo("\n  WARNING: Index has documents but blob storage is empty.")
        click.echo("  ACTION: This is unusual. Check if blobs were deleted or if")
        click.echo("          the storage account / container / prefix is correct.")
        return

    if blob_total > 0 and index_doc_count == 0:
        click.echo("\n  WARNING: Blobs exist but index is empty.")
        click.echo("  ACTION: The indexer may not have run or may have failed.")
        click.echo("          Check the indexer status above and trigger a run.")
        return

    # Both have data — compare.
    # Note: index may have more docs than blobs due to chunking (one blob -> multiple chunks).
    if index_doc_count >= blob_total:
        ratio = index_doc_count / blob_total if blob_total else 0
        click.echo(f"  Ratio (index/blobs): {ratio:.1f}x")
        click.echo(f"\n  OK: Index has {index_doc_count} documents from {blob_total} blobs.")
        click.echo("  This is expected — documents are chunked during indexing.")
    else:
        click.echo(
            f"\n  WARNING: Index ({index_doc_count}) has fewer documents than blobs ({blob_total})."
        )
        click.echo("  ACTION: Some blobs may not have been indexed. Check:")
        click.echo("    - Indexer errors (see above)")
        click.echo("    - Skillset configuration")
        click.echo("    - Blob format compatibility")

    click.echo("\n  Blobs breakdown:")
    click.echo(f"    Files: {blob_counts['files']}")
    click.echo(f"    Pages: {blob_counts['pages']}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@click.command()
@click.option(
    "--blob-sample",
    default=5,
    show_default=True,
    help="Number of sample blobs to list per category",
)
@click.option(
    "--index-sample",
    default=5,
    show_default=True,
    help="Number of sample documents to show from the index",
)
def main(blob_sample: int, index_sample: int) -> None:
    """Diagnose the SharePoint indexing pipeline end-to-end."""
    click.echo("SharePoint Indexing Pipeline Diagnostics")
    click.echo("========================================")

    # 1. Blob storage
    blob_counts = check_blob_storage(blob_sample)

    # 2–3. Search index and indexer (share the HTTP client)
    index_name = _resolve_index_name()
    with httpx.Client(timeout=30) as client:
        api = SearchApiClient(client)

        # 2. Search index
        index_doc_count = check_search_index(api, index_name, index_sample)

        # 3. Indexer status
        check_indexer_status(api, index_name)

    # 4. Discrepancy check
    check_discrepancies(blob_counts, index_doc_count)

    click.echo("\n" + "=" * 60)
    click.echo("  DIAGNOSTICS COMPLETE")
    click.echo("=" * 60)


if __name__ == "__main__":
    main()
