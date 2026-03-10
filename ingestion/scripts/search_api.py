"""Shared Azure AI Search REST API client for ingestion scripts."""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING, Any

import click
from azure.identity import DefaultAzureCredential

if TYPE_CHECKING:
    import httpx

API_VERSION = "2024-07-01"
_TOKEN_SCOPE = "https://search.azure.com/.default"


def get_env(name: str) -> str:
    """Read a required environment variable or exit with an error."""
    val = os.environ.get(name, "")
    if not val:
        click.echo(f"ERROR: {name} environment variable is required", err=True)
        sys.exit(1)
    return val


class SearchApiClient:
    """Thin wrapper around the Azure AI Search REST API.

    Provides authenticated requests with a single credential instance.
    """

    def __init__(self, client: httpx.Client) -> None:
        self._client = client
        self._credential = DefaultAzureCredential()
        self._endpoint = get_env("AZURE_SEARCH_ENDPOINT").rstrip("/")

    def _headers(self) -> dict[str, str]:
        token = self._credential.get_token(_TOKEN_SCOPE).token
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Send an authenticated request to the Search REST API."""
        url = f"{self._endpoint}/{path}"
        params = {"api-version": API_VERSION}
        return self._client.request(method, url, params=params, headers=self._headers(), json=body)

    def check_response(self, resp: httpx.Response, resource: str) -> None:
        """Validate a response, exiting on failure."""
        if resp.status_code in (200, 201, 204):
            click.echo(f"  {resource} created/updated.")
        else:
            click.echo(
                f"  FAILED to create {resource}: {resp.status_code} {resp.text}",
                err=True,
            )
            sys.exit(1)
