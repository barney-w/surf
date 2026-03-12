"""Validate the website content in the search index.

Runs filter-based counts and sample queries to verify website content has been
indexed correctly, with proper content_source and document_type tagging.

Usage:
    cd ingestion && uv run python scripts/validate_website_index.py
    cd ingestion && uv run python scripts/validate_website_index.py --verbose
    cd ingestion && uv run python scripts/validate_website_index.py --index-name my-index
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import click
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env")


def _resolve_index_name(override: str | None = None) -> str:
    """Resolve the search index name from option, env, or default."""
    if override:
        return override
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


# ---------------------------------------------------------------------------
# Filter count checks
# ---------------------------------------------------------------------------

FILTER_CHECKS: list[tuple[str, str | None, str]] = [
    ("Total documents", None, "count"),
    ("Website content", "content_source eq 'website'", "positive"),
    (
        "Website HTML pages",
        "content_source eq 'website' and document_type eq 'web-page'",
        "positive",
    ),
    ("Website PDFs", "content_source eq 'website' and document_type eq 'web-pdf'", "positive"),
    ("Untagged documents", "content_source eq ''", "zero"),
]


def run_filter_checks(client: SearchClient, verbose: bool) -> list[bool]:
    """Run filter count checks and return pass/fail results."""
    results: list[bool] = []

    click.echo("Filter Count Checks")
    click.echo("-" * 50)

    for label, filter_expr, expectation in FILTER_CHECKS:
        search_kwargs: dict = {
            "search_text": "*",
            "top": 0,
            "include_total_count": True,
        }
        if filter_expr is not None:
            search_kwargs["filter"] = filter_expr

        search_results = client.search(**search_kwargs)
        count = search_results.get_count()

        if count is None:
            click.echo(f"  FAIL  {label}: could not retrieve count")
            results.append(False)
            continue

        count = int(count)

        if expectation == "positive":
            passed = count > 0
            status = "PASS" if passed else "FAIL"
        elif expectation == "zero":
            passed = count == 0
            status = "PASS" if passed else "FAIL"
        else:
            # "count" — just report, always pass
            passed = True
            status = "PASS"

        click.echo(f"  {status}  {label}: {count}")
        if verbose and filter_expr:
            click.echo(f"         filter: {filter_expr}")

        results.append(passed)

    return results


# ---------------------------------------------------------------------------
# Sample query checks
# ---------------------------------------------------------------------------

SAMPLE_QUERIES = [
    "waste recycling bins",
    "dog local laws",
    "planning scheme",
    "annual plan budget",
]


def run_sample_queries(client: SearchClient, verbose: bool) -> list[bool]:
    """Run sample hybrid queries and return pass/fail results."""
    results: list[bool] = []

    click.echo()
    click.echo("Sample Query Checks")
    click.echo("-" * 50)

    for query_text in SAMPLE_QUERIES:
        # Use keyword-only search for validation (no server-side vectoriser configured)
        search_results = client.search(
            search_text=query_text,
            filter="content_source eq 'website'",
            top=3,
            select=["title", "document_type", "source_url"],
            include_total_count=True,
        )

        hits = list(search_results)
        num_results = len(hits)

        if num_results >= 1:
            status = "PASS"
            passed = True
        else:
            status = "WARN"
            passed = False

        click.echo(f"  {status}  \"{query_text}\" — {num_results} result(s)")

        if num_results > 0:
            top_hit = hits[0]
            title = top_hit.get("title", "(no title)")
            score = top_hit.get("@search.score", "n/a")
            doc_type = top_hit.get("document_type", "n/a")
            source_url = top_hit.get("source_url", "n/a")

            # Truncate URL for display
            max_url_len = 60
            if isinstance(source_url, str) and len(source_url) > max_url_len:
                source_url = source_url[:max_url_len] + "..."

            click.echo(f"         top: {title}")
            click.echo(f"         score={score}  type={doc_type}")
            click.echo(f"         url={source_url}")

            if verbose and num_results > 1:
                for i, hit in enumerate(hits[1:], start=2):
                    h_title = hit.get("title", "(no title)")
                    h_score = hit.get("@search.score", "n/a")
                    h_type = hit.get("document_type", "n/a")
                    h_url = hit.get("source_url", "n/a")
                    if isinstance(h_url, str) and len(h_url) > max_url_len:
                        h_url = h_url[:max_url_len] + "..."
                    click.echo(f"         #{i}: {h_title}")
                    click.echo(f"              score={h_score}  type={h_type}")
                    click.echo(f"              url={h_url}")

        results.append(passed)

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@click.command()
@click.option(
    "--index-name",
    default=None,
    help="Override the search index name (default from env or 'surf-index')",
)
@click.option(
    "--verbose",
    is_flag=True,
    help="Show detailed result information for sample queries",
)
def main(index_name: str | None, verbose: bool) -> None:
    """Validate website content in the Azure AI Search index."""
    resolved_index = _resolve_index_name(index_name)
    endpoint = _resolve_endpoint()

    click.echo(f"Index:    {resolved_index}")
    click.echo(f"Endpoint: {endpoint}")
    click.echo()

    credential = DefaultAzureCredential()
    client = SearchClient(
        endpoint=endpoint,
        index_name=resolved_index,
        credential=credential,
    )

    filter_results = run_filter_checks(client, verbose)
    query_results = run_sample_queries(client, verbose)

    # Summary
    all_results = filter_results + query_results
    passed = sum(all_results)
    total = len(all_results)

    click.echo()
    click.echo(f"{passed}/{total} checks passed.")

    # Filter failures are hard fails; query warnings are soft
    filter_failures = sum(1 for r in filter_results if not r)
    query_warnings = sum(1 for r in query_results if not r)

    if filter_failures > 0:
        click.echo(
            f"Validation FAILED — {filter_failures} filter check(s) failed.",
            err=True,
        )
        sys.exit(1)
    elif query_warnings > 0:
        click.echo(f"Validation passed with {query_warnings} warning(s).")
    else:
        click.echo("Validation successful.")


if __name__ == "__main__":
    main()
