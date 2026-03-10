"""Validate the SharePoint index has correct content and search works.

Runs five checks against the live index:
1. Document count — at least 1 document
2. Vector fields — content_vector is populated with correct dimensionality (3072)
3. Required fields — chunk_id, title, content are non-empty
4. Text search — a simple keyword search returns results
5. Vector search — a vector query via the integrated vectoriser returns results

Usage:
    cd ingestion && uv run python scripts/validate_sharepoint_index.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, cast

import click
import httpx
from dotenv import load_dotenv

from scripts.search_api import SearchApiClient

load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env")

EXPECTED_VECTOR_DIMS = 3072


def _search(api: SearchApiClient, index_name: str, body: dict[str, Any]) -> dict[str, Any]:
    """Execute a search query against an index."""
    resp = api.request("POST", f"indexes/{index_name}/docs/search", body)
    if resp.status_code != 200:
        click.echo(
            f"ERROR: Search request failed: {resp.status_code} {resp.text}",
            err=True,
        )
        sys.exit(1)
    return resp.json()


def _resolve_index_name() -> str:
    return os.environ.get("AZURE_SEARCH_SHAREPOINT_INDEX", "surf-sharepoint-index")


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_document_count(api: SearchApiClient, index_name: str) -> bool:
    resp = api.request("GET", f"indexes/{index_name}/docs/$count")
    if resp.status_code != 200:
        click.echo(f"  FAIL  Document count — HTTP {resp.status_code}: {resp.text}")
        return False

    count = int(resp.text)
    if count >= 1:
        click.echo(f"  PASS  Document count: {count}")
        return True

    click.echo(f"  FAIL  Document count: {count} (expected >= 1)")
    return False


def check_vector_fields(api: SearchApiClient, index_name: str) -> bool:
    data = _search(
        api,
        index_name,
        {"search": "*", "top": 1, "select": "chunk_id,content_vector"},
    )
    results = data.get("value", [])
    if not results:
        click.echo("  FAIL  Vector fields — no documents returned")
        return False

    doc = results[0]
    vector = doc.get("content_vector")

    if not vector:
        click.echo("  FAIL  Vector fields — content_vector is null or missing")
        return False

    if not isinstance(vector, list):
        click.echo(
            f"  FAIL  Vector fields — content_vector is {type(vector).__name__}, expected list"
        )
        return False

    dims = len(cast(list[Any], vector))
    if dims != EXPECTED_VECTOR_DIMS:
        click.echo(
            f"  FAIL  Vector fields — dimensionality is {dims}, expected {EXPECTED_VECTOR_DIMS}"
        )
        return False

    click.echo(f"  PASS  Vector fields: content_vector present, {dims} dimensions")
    return True


def check_required_fields(api: SearchApiClient, index_name: str) -> bool:
    data = _search(
        api,
        index_name,
        {"search": "*", "top": 5, "select": "chunk_id,title,content"},
    )
    results = data.get("value", [])
    if not results:
        click.echo("  FAIL  Required fields — no documents returned")
        return False

    all_ok = True
    for i, doc in enumerate(results):
        for fld in ("chunk_id", "title", "content"):
            val = doc.get(fld)
            if not val or (isinstance(val, str) and not val.strip()):
                click.echo(f"  FAIL  Required fields — doc[{i}].{fld} is empty or missing")
                all_ok = False

    if all_ok:
        click.echo(
            f"  PASS  Required fields: chunk_id, title, content populated ({len(results)} sampled)"
        )
    return all_ok


def check_text_search(api: SearchApiClient, index_name: str) -> bool:
    data = _search(
        api,
        index_name,
        {
            "search": "policy",
            "top": 3,
            "select": "chunk_id,title",
            "queryType": "simple",
        },
    )
    results = data.get("value", [])
    if results:
        titles = [r.get("title", "(no title)") for r in results]
        click.echo(f"  PASS  Text search: {len(results)} results — {titles}")
        return True

    click.echo("  FAIL  Text search — query for 'policy' returned 0 results")
    return False


def check_vector_search(api: SearchApiClient, index_name: str) -> bool:
    data = _search(
        api,
        index_name,
        {
            "search": "*",
            "top": 3,
            "select": "chunk_id,title",
            "vectorQueries": [
                {
                    "kind": "text",
                    "text": "annual leave policy",
                    "fields": "content_vector",
                    "k": 3,
                },
            ],
        },
    )
    results = data.get("value", [])
    if results:
        titles = [r.get("title", "(no title)") for r in results]
        click.echo(f"  PASS  Vector search: {len(results)} results — {titles}")
        return True

    click.echo("  FAIL  Vector search — vectorised query returned 0 results")
    return False


@click.command()
def main() -> None:
    """Validate the SharePoint search index has correct content."""
    index_name = _resolve_index_name()
    click.echo(f"Validating index: {index_name}\n")

    checks = [
        check_document_count,
        check_vector_fields,
        check_required_fields,
        check_text_search,
        check_vector_search,
    ]

    with httpx.Client(timeout=30) as client:
        api = SearchApiClient(client)
        results = [check(api, index_name) for check in checks]

    passed = sum(results)
    total = len(results)
    click.echo(f"\n{passed}/{total} checks passed.")

    if all(results):
        click.echo("Index validation successful.")
    else:
        click.echo("Index validation FAILED.", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
