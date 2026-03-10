"""Trigger the SharePoint indexer and optionally wait for completion.

Usage:
    cd ingestion && uv run python scripts/run_indexer.py          # Trigger and return
    cd ingestion && uv run python scripts/run_indexer.py --wait   # Trigger and poll until complete
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

import click
import httpx
from dotenv import load_dotenv

from scripts.search_api import SearchApiClient

load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env")


def _resolve_indexer_name() -> str:
    index_name = os.environ.get("AZURE_SEARCH_SHAREPOINT_INDEX", "surf-sharepoint-index")
    return f"{index_name}-indexer"


def _trigger_indexer(api: SearchApiClient, indexer_name: str) -> None:
    resp = api.request("POST", f"indexers/{indexer_name}/run")
    if resp.status_code in (202, 204):
        click.echo(f"Indexer '{indexer_name}' triggered successfully.")
    elif resp.status_code == 409:
        click.echo(f"Indexer '{indexer_name}' is already running.")
    else:
        click.echo(
            f"ERROR: Failed to trigger indexer: {resp.status_code} {resp.text}",
            err=True,
        )
        sys.exit(1)


def _get_indexer_status(api: SearchApiClient, indexer_name: str) -> dict[str, Any]:
    resp = api.request("GET", f"indexers/{indexer_name}/status")
    if resp.status_code != 200:
        click.echo(
            f"ERROR: Failed to get indexer status: {resp.status_code} {resp.text}",
            err=True,
        )
        sys.exit(1)
    return resp.json()


def _poll_until_complete(api: SearchApiClient, indexer_name: str) -> None:
    click.echo("Waiting for indexer to complete...")

    while True:
        status_data = _get_indexer_status(api, indexer_name)
        last_result = status_data.get("lastResult", {})
        execution_status = last_result.get("status", "unknown")

        items_processed = last_result.get("itemsProcessed", 0)
        items_failed = last_result.get("itemsFailed", 0)
        click.echo(
            f"  Status: {execution_status} | Processed: {items_processed} | Failed: {items_failed}"
        )

        if execution_status != "inProgress":
            if execution_status == "success":
                click.echo(f"Indexer completed successfully. {items_processed} items processed.")
            elif execution_status == "transientFailure":
                click.echo(
                    f"WARNING: Indexer finished with transient failures. "
                    f"{items_processed} processed, {items_failed} failed.",
                    err=True,
                )
            else:
                error_message = last_result.get("errorMessage", "No error message available")
                click.echo(
                    f"ERROR: Indexer finished with status '{execution_status}': {error_message}",
                    err=True,
                )
                sys.exit(1)
            return

        time.sleep(10)


@click.command()
@click.option(
    "--wait",
    is_flag=True,
    help="Poll until the indexer run completes",
)
def main(wait: bool) -> None:
    """Trigger the SharePoint indexer and optionally wait for completion."""
    indexer_name = _resolve_indexer_name()
    click.echo(f"Indexer: {indexer_name}")

    with httpx.Client(timeout=30) as client:
        api = SearchApiClient(client)
        _trigger_indexer(api, indexer_name)
        if wait:
            _poll_until_complete(api, indexer_name)


if __name__ == "__main__":
    main()
