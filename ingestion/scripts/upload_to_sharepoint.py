"""Upload a local file to SharePoint via Microsoft Graph API.

Uses the same client-credentials auth as the sync connector.
Files under 4MB use simple PUT; larger files use an upload session.

Usage:
    cd ingestion && uv run python -m scripts.upload_to_sharepoint /path/to/file.pdf
    cd ingestion && uv run python -m scripts.upload_to_sharepoint /path/to/file.pdf --folder Policies
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import click
import httpx
from azure.identity import ClientSecretCredential, DefaultAzureCredential
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env")

GRAPH_SCOPE = "https://graph.microsoft.com/.default"
SIMPLE_UPLOAD_LIMIT = 4 * 1024 * 1024  # 4 MB


def _get_credential() -> ClientSecretCredential | DefaultAzureCredential:
    tenant_id = os.environ.get("SHAREPOINT_TENANT_ID", "")
    client_id = os.environ.get("SHAREPOINT_CLIENT_ID", "")
    client_secret = os.environ.get("SHAREPOINT_CLIENT_SECRET", "")

    if client_secret:
        if not tenant_id or not client_id:
            click.echo(
                "ERROR: SHAREPOINT_TENANT_ID and SHAREPOINT_CLIENT_ID are "
                "required when SHAREPOINT_CLIENT_SECRET is set.",
                err=True,
            )
            sys.exit(1)
        return ClientSecretCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
        )
    return DefaultAzureCredential()


def _resolve_site_and_drive(
    client: httpx.Client, token: str, site_url: str, library_name: str | None
) -> tuple[str, str]:
    """Resolve the Graph site ID and drive ID."""
    parsed = urlparse(site_url)
    hostname = parsed.hostname or ""
    site_path = parsed.path.rstrip("/")
    site_identifier = hostname if not site_path else f"{hostname}:{site_path}"

    headers = {"Authorization": f"Bearer {token}"}

    # Get full site ID
    resp = client.get(
        f"https://graph.microsoft.com/v1.0/sites/{site_identifier}",
        headers=headers,
    )
    resp.raise_for_status()
    full_site_id = resp.json()["id"]

    # List drives
    resp = client.get(
        f"https://graph.microsoft.com/v1.0/sites/{full_site_id}/drives",
        headers=headers,
    )
    resp.raise_for_status()
    drives = resp.json().get("value", [])

    if library_name:
        for drive in drives:
            if drive.get("name") == library_name:
                return full_site_id, drive["id"]
        available = [d.get("name") for d in drives]
        click.echo(
            f"ERROR: Library '{library_name}' not found. Available: {available}",
            err=True,
        )
        sys.exit(1)

    if not drives:
        click.echo("ERROR: No document libraries found on this site.", err=True)
        sys.exit(1)
    return full_site_id, drives[0]["id"]


def _upload_simple(
    client: httpx.Client, token: str, drive_id: str, remote_path: str, data: bytes
) -> dict:
    """Upload a file <4MB using simple PUT."""
    url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/{remote_path}:/content"
    resp = client.put(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/octet-stream",
        },
        content=data,
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()


def _upload_large(
    client: httpx.Client, token: str, drive_id: str, remote_path: str, data: bytes
) -> dict:
    """Upload a file >=4MB using an upload session."""
    # Create upload session
    url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/{remote_path}:/createUploadSession"
    resp = client.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={"item": {"@microsoft.graph.conflictBehavior": "replace"}},
    )
    resp.raise_for_status()
    upload_url = resp.json()["uploadUrl"]

    # Upload in 3.5MB chunks
    chunk_size = 3_500_000
    total = len(data)
    offset = 0
    result = {}

    while offset < total:
        end = min(offset + chunk_size, total)
        chunk = data[offset:end]
        content_range = f"bytes {offset}-{end - 1}/{total}"
        resp = client.put(
            upload_url,
            headers={
                "Content-Length": str(len(chunk)),
                "Content-Range": content_range,
            },
            content=chunk,
            timeout=120,
        )
        resp.raise_for_status()
        result = resp.json()
        offset = end

    return result


@click.command()
@click.argument("file_path", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--folder",
    default=None,
    help="Target folder within the document library (e.g. 'Policies')",
)
@click.option(
    "--library",
    default=None,
    help="Document library name (default: uses SHAREPOINT_LIBRARY_NAME or first library)",
)
def main(file_path: str, folder: str | None, library: str | None) -> None:
    """Upload a local file to SharePoint via Microsoft Graph API."""
    local_path = Path(file_path)
    file_data = local_path.read_bytes()
    file_name = local_path.name
    file_size = len(file_data)

    site_url = os.environ.get("SHAREPOINT_SITE_URL", "")
    if not site_url:
        click.echo("ERROR: SHAREPOINT_SITE_URL environment variable is required.", err=True)
        sys.exit(1)

    library_name = library or os.environ.get("SHAREPOINT_LIBRARY_NAME") or None

    click.echo(f"File:    {local_path} ({file_size:,} bytes)")
    click.echo(f"Site:    {site_url}")
    click.echo(f"Library: {library_name or '(default)'}")
    click.echo(f"Folder:  {folder or '(root)'}")

    credential = _get_credential()
    token = credential.get_token(GRAPH_SCOPE).token

    with httpx.Client(timeout=60) as client:
        _site_id, drive_id = _resolve_site_and_drive(client, token, site_url, library_name)

        remote_path = f"{folder}/{file_name}" if folder else file_name
        click.echo(f"\nUploading to: {remote_path}")

        if file_size < SIMPLE_UPLOAD_LIMIT:
            result = _upload_simple(client, token, drive_id, remote_path, file_data)
        else:
            result = _upload_large(client, token, drive_id, remote_path, file_data)

    web_url = result.get("webUrl", "(unknown)")
    item_id = result.get("id", "(unknown)")
    click.echo(f"\nUpload successful!")
    click.echo(f"  Item ID: {item_id}")
    click.echo(f"  URL:     {web_url}")


if __name__ == "__main__":
    main()
