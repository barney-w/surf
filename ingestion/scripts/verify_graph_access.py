"""Verify Microsoft Graph API access to SharePoint.

Quick smoke test that the Entra app registration, permissions,
and credentials are all working correctly.

Uses raw httpx (matching the sync code path) rather than msgraph-sdk,
so this script verifies the same auth path as the actual sync.

Supports both client secret and DefaultAzureCredential (managed identity).

Usage:
    cd ingestion && uv run python scripts/verify_graph_access.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from azure.identity import ClientSecretCredential, DefaultAzureCredential
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env")

GRAPH_SCOPE = "https://graph.microsoft.com/.default"


def _get_credential() -> ClientSecretCredential | DefaultAzureCredential:
    """Build credential using the same logic as SharePointSync."""
    tenant_id = os.environ.get("SHAREPOINT_TENANT_ID", "")
    client_id = os.environ.get("SHAREPOINT_CLIENT_ID", "")
    client_secret = os.environ.get("SHAREPOINT_CLIENT_SECRET", "")

    if client_secret:
        if not tenant_id or not client_id:
            print(
                "ERROR: SHAREPOINT_TENANT_ID and SHAREPOINT_CLIENT_ID are "
                "required when SHAREPOINT_CLIENT_SECRET is set."
            )
            sys.exit(1)
        print("Using ClientSecretCredential (app registration).")
        return ClientSecretCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
        )

    print(
        "SHAREPOINT_CLIENT_SECRET not set — using DefaultAzureCredential (managed identity / CLI)."
    )
    return DefaultAzureCredential()


def _graph_get(client: httpx.Client, token: str, url: str) -> dict[str, Any]:
    """Make an authenticated GET to Microsoft Graph."""
    resp = client.get(url, headers={"Authorization": f"Bearer {token}"})
    resp.raise_for_status()
    return resp.json()


def run_checks() -> None:
    credential = _get_credential()
    token = credential.get_token(GRAPH_SCOPE).token
    site_url = os.environ.get("SHAREPOINT_SITE_URL", "")

    with httpx.Client(timeout=30) as client:
        # Test 1: List sites
        print("\n1. Listing SharePoint sites (Sites.Read.All)...")
        try:
            data = _graph_get(
                client,
                token,
                "https://graph.microsoft.com/v1.0/sites?$top=5",
            )
            sites = data.get("value", [])
            if sites:
                for site in sites:
                    print(f"   - {site.get('displayName')}: {site.get('webUrl')}")
            else:
                print("   No sites found (this is normal for a fresh tenant)")
        except httpx.HTTPStatusError as exc:
            print(f"   FAILED — HTTP {exc.response.status_code}")
            print("   Check that Sites.Read.All permission is granted with admin consent")
            sys.exit(1)

        # Test 2: Resolve configured site
        if site_url:
            print(f"\n2. Resolving configured site: {site_url}")
            try:
                parsed = urlparse(site_url)
                hostname = parsed.hostname or ""
                site_path = parsed.path.rstrip("/")
                site_id = hostname if not site_path else f"{hostname}:{site_path}"

                site = _graph_get(
                    client,
                    token,
                    f"https://graph.microsoft.com/v1.0/sites/{site_id}",
                )
                print(f"   OK — Site: {site.get('displayName')} (ID: {site.get('id')})")

                # List drives
                full_id = site.get("id", site_id)
                drives_data = _graph_get(
                    client,
                    token,
                    f"https://graph.microsoft.com/v1.0/sites/{full_id}/drives",
                )
                drives = drives_data.get("value", [])
                if drives:
                    print("   Document libraries:")
                    for drive in drives:
                        print(f"     - {drive.get('name')} (ID: {drive.get('id')})")
                else:
                    print("   No document libraries found.")
            except httpx.HTTPStatusError as exc:
                print(f"   FAILED — HTTP {exc.response.status_code}")
                print("   Check that SHAREPOINT_SITE_URL is correct")
                sys.exit(1)
        else:
            print("\n2. Skipping site resolution (SHAREPOINT_SITE_URL not set)")

    print("\nAll checks passed! Graph API access is working.")


if __name__ == "__main__":
    run_checks()
