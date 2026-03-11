"""End-to-end SharePoint ingestion test.

Orchestrates the full pipeline with assertions:
1. Upload a test document to SharePoint
2. Sync to blob storage
3. Trigger indexer and wait
4. Query the index for the document
5. Verify the document is found with correct metadata
6. Optionally clean up (delete test document from SharePoint)

Usage:
    cd ingestion && uv run python -m scripts.test_e2e_sharepoint /path/to/test.pdf
    cd ingestion && uv run python -m scripts.test_e2e_sharepoint /path/to/test.pdf --cleanup
    cd ingestion && uv run python -m scripts.test_e2e_sharepoint \\
        /path/to/test.pdf --query "search terms"
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import click
import httpx
from dotenv import load_dotenv

from scripts.search_api import SearchApiClient

load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env")


def _step(number: int, title: str) -> None:
    click.echo(f"\n{'=' * 60}")
    click.echo(f"  Step {number}: {title}")
    click.echo(f"{'=' * 60}")


def _run(args: list[str], label: str) -> subprocess.CompletedProcess[str]:
    """Run a subprocess, streaming output."""
    click.echo(f"  Running: {' '.join(args)}")
    result = subprocess.run(args, capture_output=True, text=True, cwd=Path(__file__).parent.parent)
    if result.stdout:
        for line in result.stdout.strip().splitlines():
            click.echo(f"    {line}")
    if result.returncode != 0:
        click.echo(f"  FAILED: {label}")
        if result.stderr:
            for line in result.stderr.strip().splitlines():
                click.echo(f"    {line}")
        sys.exit(1)
    return result


def step_upload(file_path: str, folder: str) -> None:
    """Step 1: Upload test file to SharePoint."""
    _step(1, "Upload test document to SharePoint")
    _run(
        [
            "uv",
            "run",
            "python",
            "-m",
            "scripts.upload_to_sharepoint",
            file_path,
            "--folder",
            folder,
        ],
        "Upload to SharePoint",
    )
    click.echo("  OK: File uploaded to SharePoint.")


def step_sync() -> None:
    """Step 2: Sync SharePoint to blob storage."""
    _step(2, "Sync SharePoint to blob storage")
    _run(
        ["uv", "run", "python", "-m", "src", "sync-sharepoint"],
        "SharePoint sync",
    )
    click.echo("  OK: Sync completed.")


def step_index() -> None:
    """Step 3: Trigger indexer and wait for completion."""
    _step(3, "Run indexer")
    _run(
        ["uv", "run", "python", "-m", "scripts.run_indexer", "--wait"],
        "Indexer run",
    )
    click.echo("  OK: Indexer completed.")


def step_query(query: str, expected_title: str) -> None:
    """Step 4: Query index and verify results."""
    _step(4, f"Query index for '{query}'")

    index_name = os.environ.get("AZURE_SEARCH_SHAREPOINT_INDEX", "surf-sharepoint-index")

    with httpx.Client(timeout=30) as client:
        api = SearchApiClient(client)
        resp = api.request(
            "POST",
            f"indexes/{index_name}/docs/search",
            {
                "search": query,
                "top": 5,
                "select": "title,content,domain,document_type",
                "queryType": "simple",
            },
        )

        if resp.status_code != 200:
            click.echo(f"  FAILED: Search returned HTTP {resp.status_code}")
            sys.exit(1)

        results = resp.json().get("value", [])
        if not results:
            click.echo("  FAILED: No search results returned.")
            sys.exit(1)

        # Check if the expected document appears in results
        found = False
        for i, doc in enumerate(results):
            title = doc.get("title", "")
            content = (doc.get("content", "") or "")[:150].replace("\n", " ")
            click.echo(f"  [{i + 1}] {title}")
            click.echo(f"      {content}...")
            if expected_title.lower() in title.lower():
                found = True

        if found:
            click.echo(f"\n  OK: '{expected_title}' found in search results.")
        else:
            click.echo(f"\n  FAILED: '{expected_title}' not found in search results.")
            sys.exit(1)


def step_validate() -> None:
    """Step 5: Run full index validation."""
    _step(5, "Validate index")
    _run(
        ["uv", "run", "python", "-m", "scripts.validate_sharepoint_index"],
        "Index validation",
    )
    click.echo("  OK: All validation checks passed.")


def step_cleanup(file_name: str, folder: str) -> None:
    """Step 6: Delete test document from SharePoint."""
    _step(6, "Clean up test document")

    from urllib.parse import urlparse

    from azure.identity import ClientSecretCredential, DefaultAzureCredential

    site_url = os.environ.get("SHAREPOINT_SITE_URL", "")
    tenant_id = os.environ.get("SHAREPOINT_TENANT_ID", "")
    client_id = os.environ.get("SHAREPOINT_CLIENT_ID", "")
    client_secret = os.environ.get("SHAREPOINT_CLIENT_SECRET", "")
    library_name = os.environ.get("SHAREPOINT_LIBRARY_NAME") or None

    if client_secret:
        credential = ClientSecretCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
        )
    else:
        credential = DefaultAzureCredential()

    token = credential.get_token("https://graph.microsoft.com/.default").token

    parsed = urlparse(site_url)
    hostname = parsed.hostname or ""
    site_path = parsed.path.rstrip("/")
    site_identifier = hostname if not site_path else f"{hostname}:{site_path}"

    with httpx.Client(timeout=60) as client:
        headers = {"Authorization": f"Bearer {token}"}

        # Resolve site and drive
        resp = client.get(
            f"https://graph.microsoft.com/v1.0/sites/{site_identifier}",
            headers=headers,
        )
        resp.raise_for_status()
        full_site_id = resp.json()["id"]

        resp = client.get(
            f"https://graph.microsoft.com/v1.0/sites/{full_site_id}/drives",
            headers=headers,
        )
        resp.raise_for_status()
        drives = resp.json().get("value", [])

        drive_id = None
        if library_name:
            for d in drives:
                if d.get("name") == library_name:
                    drive_id = d["id"]
                    break
        if not drive_id and drives:
            drive_id = drives[0]["id"]

        if not drive_id:
            click.echo("  WARNING: Could not resolve drive ID for cleanup.")
            return

        remote_path = f"{folder}/{file_name}" if folder else file_name
        resp = client.delete(
            f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/{remote_path}",
            headers=headers,
        )
        if resp.status_code in (200, 204):
            click.echo(f"  OK: Deleted '{remote_path}' from SharePoint.")
        elif resp.status_code == 404:
            click.echo(f"  OK: '{remote_path}' already deleted or not found.")
        else:
            click.echo(f"  WARNING: Delete returned HTTP {resp.status_code}: {resp.text}")


@click.command()
@click.argument("file_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--folder", default="policies", show_default=True, help="Target SharePoint folder")
@click.option("--query", default=None, help="Search query (default: derived from file name)")
@click.option("--cleanup", is_flag=True, help="Delete test document from SharePoint after test")
@click.option("--skip-upload", is_flag=True, help="Skip upload step (file already in SharePoint)")
def main(file_path: str, folder: str, query: str | None, cleanup: bool, skip_upload: bool) -> None:
    """Run end-to-end SharePoint ingestion test."""
    local_path = Path(file_path)
    file_name = local_path.name
    expected_title = file_name

    if query is None:
        # Derive query from filename: "development-conditions.pdf" -> "development conditions"
        query = local_path.stem.replace("-", " ").replace("_", " ")

    click.echo("SharePoint E2E Ingestion Test")
    click.echo(f"  File:  {local_path}")
    click.echo(f"  Query: {query}")
    click.echo(f"  Title: {expected_title}")

    try:
        if not skip_upload:
            step_upload(file_path, folder)
        else:
            click.echo("\n  Skipping upload (--skip-upload).")

        step_sync()
        step_index()
        step_query(query, expected_title)
        step_validate()

        click.echo(f"\n{'=' * 60}")
        click.echo("  ALL STEPS PASSED")
        click.echo(f"{'=' * 60}")
    finally:
        if cleanup:
            step_cleanup(file_name, folder)


if __name__ == "__main__":
    main()
