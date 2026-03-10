"""Sync files and pages from SharePoint to Azure Blob Storage via Microsoft Graph API.

All company-specific configuration comes from environment variables
or Key Vault — nothing is hardcoded.

Two content types are synced:
- **Drive items**: Files (PDF, DOCX, XLSX, etc.) from document libraries — downloaded as-is.
- **Site pages**: SharePoint pages — text web parts extracted as HTML.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass, field
from html import escape as html_escape
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlparse

import httpx
from azure.core.exceptions import ResourceNotFoundError
from azure.identity import ClientSecretCredential, DefaultAzureCredential
from azure.storage.blob.aio import ContainerClient

if TYPE_CHECKING:
    from azure.core.credentials import TokenCredential

logger = logging.getLogger(__name__)

_BLOB_NAME_ILLEGAL_RE = re.compile(r'[#?*"<>|\\]')
_GRAPH_SCOPE = "https://graph.microsoft.com/.default"
_TOKEN_REFRESH_BUFFER_SECONDS = 300

# Sensitivity label priority ordering (lower = less sensitive).
# Actual integer values depend on tenant configuration; these names are
# used as a configurable threshold string.
_SENSITIVITY_LEVELS = {
    "general": 0,
    "internal": 1,
    "confidential": 2,
    "highly_confidential": 3,
}


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class SyncConfig:
    """Loaded from environment variables at runtime."""

    site_url: str
    tenant_id: str
    client_id: str
    client_secret: str | None = None
    library_name: str | None = None
    folder_path: str | None = None
    blob_account_url: str = ""
    blob_container: str = "documents"
    blob_prefix: str = "sharepoint/"
    extensions: set[str] = field(
        default_factory=lambda: {
            ".pdf",
            ".docx",
            ".doc",
            ".pptx",
            ".ppt",
            ".xlsx",
            ".xls",
            ".html",
            ".htm",
            ".csv",
            ".md",
            ".rtf",
            ".msg",
            ".xml",
            ".odt",
            ".ods",
            ".odp",
            ".txt",
        }
    )
    sync_pages: bool = True
    skip_deletion_reconciliation: bool = False
    domain: str = "hr"
    document_type: str = "policy"
    sensitivity_label_threshold: str | None = None
    max_concurrent_requests: int = 10
    max_retries: int = 5
    timeout_seconds: int = 60
    download_timeout_seconds: int = 300
    max_file_size_mb: int = 100

    @classmethod
    def from_env(cls) -> SyncConfig:
        """Load config from environment variables."""
        site_url = os.environ.get("SHAREPOINT_SITE_URL", "")
        if not site_url:
            msg = "SHAREPOINT_SITE_URL environment variable is required"
            raise ValueError(msg)

        return cls(
            site_url=site_url,
            tenant_id=os.environ.get("SHAREPOINT_TENANT_ID", ""),
            client_id=os.environ.get("SHAREPOINT_CLIENT_ID", ""),
            client_secret=os.environ.get("SHAREPOINT_CLIENT_SECRET"),
            library_name=os.environ.get("SHAREPOINT_LIBRARY_NAME") or None,
            folder_path=os.environ.get("SHAREPOINT_FOLDER_PATH") or None,
            blob_account_url=os.environ.get("AZURE_STORAGE_ACCOUNT_URL", ""),
            blob_container=os.environ.get("AZURE_STORAGE_CONTAINER", "documents"),
            blob_prefix=os.environ.get("AZURE_STORAGE_BLOB_PREFIX", "sharepoint/"),
            sync_pages=(os.environ.get("SHAREPOINT_SYNC_PAGES", "true").lower() == "true"),
            skip_deletion_reconciliation=(
                os.environ.get("SHAREPOINT_SKIP_DELETION_RECONCILIATION", "false").lower() == "true"
            ),
            domain=os.environ.get("SHAREPOINT_DOMAIN", "hr"),
            document_type=os.environ.get("SHAREPOINT_DOCUMENT_TYPE", "policy"),
            sensitivity_label_threshold=(
                os.environ.get("SHAREPOINT_SENSITIVITY_LABEL_THRESHOLD") or None
            ),
            max_concurrent_requests=int(os.environ.get("SHAREPOINT_MAX_CONCURRENT_REQUESTS", "10")),
            max_retries=int(os.environ.get("SHAREPOINT_MAX_RETRIES", "5")),
            timeout_seconds=int(os.environ.get("SHAREPOINT_TIMEOUT_SECONDS", "60")),
            download_timeout_seconds=int(
                os.environ.get("SHAREPOINT_DOWNLOAD_TIMEOUT_SECONDS", "300")
            ),
            max_file_size_mb=int(os.environ.get("SHAREPOINT_MAX_FILE_SIZE_MB", "100")),
        )


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


@dataclass
class SyncResult:
    """Summary of a sync operation."""

    files_synced: int = 0
    files_skipped: int = 0
    files_oversized: int = 0
    files_skipped_sensitivity: int = 0
    files_deleted: int = 0
    pages_synced: int = 0
    pages_skipped: int = 0
    pages_deleted: int = 0
    errors: list[str] = field(default_factory=lambda: [])

    @property
    def total_synced(self) -> int:
        return self.files_synced + self.pages_synced


# ---------------------------------------------------------------------------
# SharePoint sync
# ---------------------------------------------------------------------------


class SharePointSync:
    """Download files and pages from SharePoint and upload to Azure Blob Storage."""

    def __init__(self, config: SyncConfig) -> None:
        self._config = config
        self._graph_credential: TokenCredential = self._build_graph_credential()
        self._azure_credential: TokenCredential = DefaultAzureCredential()
        self._http: httpx.AsyncClient | None = None
        self._semaphore: asyncio.Semaphore | None = None
        # Token cache
        self._cached_token: str | None = None
        self._token_expires_on: float = 0.0

    def _build_graph_credential(self) -> TokenCredential:
        if self._config.client_secret:
            return ClientSecretCredential(
                tenant_id=self._config.tenant_id,
                client_id=self._config.client_id,
                client_secret=self._config.client_secret,
            )
        return DefaultAzureCredential()

    async def _get_token(self) -> str:
        """Get an access token, using cache when possible."""
        now = time.time()
        if self._cached_token and self._token_expires_on > now + _TOKEN_REFRESH_BUFFER_SECONDS:
            return self._cached_token

        loop = asyncio.get_running_loop()
        token = await loop.run_in_executor(None, self._graph_credential.get_token, _GRAPH_SCOPE)
        self._cached_token = token.token
        self._token_expires_on = token.expires_on
        return token.token

    # -- HTTP layer with retry ----------------------------------------------

    async def _graph_request(
        self,
        method: str,
        url: str,
        *,
        timeout: float | None = None,
        follow_redirects: bool = False,
    ) -> httpx.Response:
        """Make an authenticated request to Microsoft Graph with retry.

        Handles HTTP 429 (throttled) and 503 (service unavailable) with
        exponential backoff. Honours the ``Retry-After`` header from Graph API.
        """
        if self._http is None or self._semaphore is None:
            msg = "HTTP client not initialised — call sync() first"
            raise RuntimeError(msg)

        effective_timeout = timeout or float(self._config.timeout_seconds)
        last_exc: Exception | None = None

        for attempt in range(self._config.max_retries + 1):
            token = await self._get_token()
            headers = {"Authorization": f"Bearer {token}"}

            try:
                async with self._semaphore:
                    resp = await self._http.request(
                        method,
                        url,
                        headers=headers,
                        follow_redirects=follow_redirects,
                        timeout=effective_timeout,
                    )
            except httpx.TransportError as exc:
                last_exc = exc
                if attempt >= self._config.max_retries:
                    raise
                wait = min(2**attempt, 60)
                logger.warning(
                    "Graph API transport error for %s %s: %s — retrying in %ds (attempt %d/%d)",
                    method,
                    url[:120],
                    exc,
                    wait,
                    attempt + 1,
                    self._config.max_retries,
                )
                await asyncio.sleep(wait)
                continue

            if resp.status_code in (429, 503):
                if attempt >= self._config.max_retries:
                    resp.raise_for_status()

                retry_after = resp.headers.get("Retry-After", "")
                wait = int(retry_after) if retry_after.isdigit() else min(2**attempt, 60)

                logger.warning(
                    "Graph API %s %s returned %d — retrying in %ds (attempt %d/%d)",
                    method,
                    url[:120],
                    resp.status_code,
                    wait,
                    attempt + 1,
                    self._config.max_retries,
                )
                await asyncio.sleep(wait)
                continue

            resp.raise_for_status()
            return resp

        # Unreachable in normal flow, but satisfies the type checker.
        if last_exc:
            raise last_exc
        msg = f"Max retries exceeded for {method} {url}"
        raise RuntimeError(msg)

    async def _graph_get(self, url: str) -> dict[str, Any]:
        """Make an authenticated GET request to Microsoft Graph."""
        resp = await self._graph_request("GET", url)
        return resp.json()

    async def _graph_get_bytes(self, url: str) -> bytes:
        """Download binary content from Microsoft Graph."""
        resp = await self._graph_request(
            "GET",
            url,
            timeout=float(self._config.download_timeout_seconds),
            follow_redirects=True,
        )
        return resp.content

    # -- Helpers ------------------------------------------------------------

    @staticmethod
    def _sanitise_blob_name(name: str) -> str:
        """Replace characters illegal in Azure Blob Storage names."""
        sanitised = _BLOB_NAME_ILLEGAL_RE.sub("_", name)
        sanitised = sanitised.strip(". ")
        if sanitised != name:
            logger.warning("Sanitised blob name: %r -> %r", name, sanitised)
        return sanitised

    def _should_skip_sensitivity(self, item: dict[str, Any]) -> bool:
        """Check if an item's sensitivity label exceeds the threshold.

        Returns True if the item should be skipped. If no threshold is
        configured, always returns False.
        """
        threshold = self._config.sensitivity_label_threshold
        if not threshold:
            return False

        threshold_level = _SENSITIVITY_LEVELS.get(threshold.lower())
        if threshold_level is None:
            logger.warning(
                "Unknown sensitivity threshold %r — skipping filter. Valid values: %s",
                threshold,
                ", ".join(_SENSITIVITY_LEVELS),
            )
            return False

        label_info = item.get("sensitivityLabel")
        if not label_info:
            return False  # No label = unrestricted

        # Graph API returns sensitivityLabel.displayName (string)
        label_name: str = ""
        if isinstance(label_info, dict):
            label_name = str(cast("dict[str, Any]", label_info).get("displayName", ""))
        elif isinstance(label_info, str):
            label_name = label_info

        # Normalise: "Highly Confidential" -> "highly_confidential"
        normalised: str = label_name.lower().replace(" ", "_")
        item_level = _SENSITIVITY_LEVELS.get(normalised, -1)

        if item_level > threshold_level:
            logger.info(
                "Skipping %s — sensitivity label %r exceeds threshold %r",
                item.get("name", "?"),
                label_name,
                threshold,
            )
            return True

        return False

    def _resolve_site_id(self) -> str:
        """Convert a site URL to a Graph API site identifier."""
        parsed = urlparse(self._config.site_url)
        hostname = parsed.hostname or ""
        site_path = parsed.path.rstrip("/")
        return hostname if not site_path else f"{hostname}:{site_path}"

    # -- Drive items (files) ------------------------------------------------

    async def _resolve_drive_id(self, site_id: str) -> str:
        """Get the drive ID for the configured library (or default)."""
        data = await self._graph_get(f"https://graph.microsoft.com/v1.0/sites/{site_id}/drives")
        drives = data.get("value", [])
        if self._config.library_name:
            for drive in drives:
                if drive.get("name") == self._config.library_name:
                    return drive["id"]
            msg = (
                f"Library '{self._config.library_name}' not found. "
                f"Available: {[d.get('name') for d in drives]}"
            )
            raise ValueError(msg)
        if not drives:
            msg = "No document libraries found on this site"
            raise ValueError(msg)
        return drives[0]["id"]

    async def _list_drive_items(self, drive_id: str) -> list[dict[str, Any]]:
        """List all files in the configured library/folder, recursively.

        Each returned item has an added ``_relative_path`` key containing the
        file's path relative to the sync root (preserves folder structure).
        """
        if self._config.folder_path:
            path = self._config.folder_path.strip("/")
            url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/{path}:/children"
        else:
            url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/root/children"

        all_items: list[dict[str, Any]] = []
        folders_to_visit: list[tuple[str, str]] = [(url, "")]

        while folders_to_visit:
            current_url, prefix = folders_to_visit.pop()
            page_url: str | None = current_url
            while page_url:
                data = await self._graph_get(page_url)
                for item in data.get("value", []):
                    name = item.get("name", "")
                    if "folder" in item:
                        child_url = (
                            "https://graph.microsoft.com/v1.0"
                            f"/drives/{drive_id}"
                            f"/items/{item['id']}/children"
                        )
                        child_prefix = f"{prefix}{name}/" if prefix else f"{name}/"
                        folders_to_visit.append((child_url, child_prefix))
                    elif "file" in item:
                        item["_relative_path"] = f"{prefix}{name}"
                        all_items.append(item)
                page_url = data.get("@odata.nextLink")

        return all_items

    async def _sync_drive_items(
        self,
        site_id: str,
        blob_client: ContainerClient | None,
        result: SyncResult,
        *,
        dry_run: bool,
    ) -> set[str]:
        """Sync files from a document library to blob storage.

        Returns the set of expected blob names (for deletion reconciliation).
        """
        drive_id = await self._resolve_drive_id(site_id)
        items = await self._list_drive_items(drive_id)
        logger.info("Found %d file(s) in document library", len(items))

        max_bytes = self._config.max_file_size_mb * 1024 * 1024
        expected_blobs: set[str] = set()

        for item in items:
            name = item.get("name", "")
            relative_path = item.get("_relative_path", name)
            ext = os.path.splitext(name)[1].lower()
            if ext not in self._config.extensions:
                logger.debug("Skipping %s (extension %s not in filter)", name, ext)
                result.files_skipped += 1
                continue

            if self._should_skip_sensitivity(item):
                result.files_skipped_sensitivity += 1
                continue

            file_size = item.get("size", 0)
            if file_size > max_bytes:
                logger.warning(
                    "Skipping %s — file size %d bytes exceeds limit of %dMB",
                    name,
                    file_size,
                    self._config.max_file_size_mb,
                )
                result.files_oversized += 1
                continue

            blob_name = self._sanitise_blob_name(f"{self._config.blob_prefix}files/{relative_path}")
            expected_blobs.add(blob_name)
            last_modified = item.get("lastModifiedDateTime", "")

            if not dry_run:
                if blob_client is None:
                    msg = "blob_client is required for non-dry-run sync"
                    raise ValueError(msg)
                try:
                    blob = blob_client.get_blob_client(blob_name)
                    props = await blob.get_blob_properties()
                    existing_modified = (props.metadata or {}).get("sp_last_modified", "")
                    if existing_modified == last_modified:
                        logger.debug("Skipping %s (unchanged)", name)
                        result.files_skipped += 1
                        continue
                except ResourceNotFoundError:
                    pass  # Blob doesn't exist yet — proceed to upload

            download_url = item.get("@microsoft.graph.downloadUrl", "")
            if not download_url:
                download_url = (
                    f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{item['id']}/content"
                )

            if dry_run:
                logger.info(
                    "[DRY RUN] Would sync: %s (%s bytes)",
                    name,
                    item.get("size", "?"),
                )
                result.files_synced += 1
                continue

            assert blob_client is not None  # guaranteed by dry_run guard above
            try:
                content = await self._graph_get_bytes(download_url)
                metadata = {
                    "sp_last_modified": last_modified,
                    "sp_item_id": item.get("id", ""),
                    "sp_web_url": item.get("webUrl", ""),
                    "sp_source_type": "drive_item",
                    "sp_domain": self._config.domain,
                    "sp_document_type": self._config.document_type,
                }
                blob = blob_client.get_blob_client(blob_name)
                await blob.upload_blob(content, overwrite=True, metadata=metadata)
                logger.info("Synced: %s (%d bytes)", name, len(content))
                result.files_synced += 1
            except Exception as exc:  # noqa: BLE001
                error_msg = f"Failed to sync {name}: {exc}"
                logger.error(error_msg)
                result.errors.append(error_msg)

        return expected_blobs

    # -- Site pages ---------------------------------------------------------

    async def _list_pages(self, site_id: str) -> list[dict[str, Any]]:
        """List all published site pages."""
        url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/pages"
        all_pages: list[dict[str, Any]] = []
        page_url: str | None = url
        while page_url:
            data = await self._graph_get(page_url)
            all_pages.extend(data.get("value", []))
            page_url = data.get("@odata.nextLink")
        return all_pages

    async def _get_page_html(self, site_id: str, page_id: str, title: str) -> str:
        """Fetch a page's text content as HTML.

        The ``innerHtml`` from SharePoint text web parts is included without
        additional sanitisation. SharePoint already sanitises user-authored
        content in text web parts, so this is intentional and safe.
        """
        url = (
            f"https://graph.microsoft.com/v1.0/sites/{site_id}"
            f"/pages/{page_id}/microsoft.graph.sitePage/webParts"
        )
        data = await self._graph_get(url)
        html_parts: list[str] = []
        for wp in data.get("value", []):
            if wp.get("@odata.type") == "#microsoft.graph.textWebPart":
                inner = wp.get("innerHtml", "")
                if inner:
                    html_parts.append(inner)

        if not html_parts:
            return ""

        escaped_title = html_escape(title)
        return (
            f"<html><head><title>{escaped_title}</title></head><body>\n"
            + "\n".join(html_parts)
            + "\n</body></html>"
        )

    async def _sync_pages(
        self,
        site_id: str,
        blob_client: ContainerClient | None,
        result: SyncResult,
        *,
        dry_run: bool,
    ) -> set[str]:
        """Sync site pages as HTML files to blob storage.

        Returns the set of expected blob names (for deletion reconciliation).
        """
        site_data = await self._graph_get(f"https://graph.microsoft.com/v1.0/sites/{site_id}")
        full_site_id = site_data.get("id", site_id)

        pages = await self._list_pages(full_site_id)
        logger.info("Found %d site page(s)", len(pages))
        expected_blobs: set[str] = set()

        for page in pages:
            page_id = page.get("id", "")
            title = page.get("title", "Untitled")
            name = page.get("name", f"{page_id}.aspx")
            last_modified = page.get("lastModifiedDateTime", "")

            html_name = name.replace(".aspx", ".html")
            blob_name = self._sanitise_blob_name(f"{self._config.blob_prefix}pages/{html_name}")
            expected_blobs.add(blob_name)

            if not dry_run:
                if blob_client is None:
                    msg = "blob_client is required for non-dry-run sync"
                    raise ValueError(msg)
                try:
                    blob = blob_client.get_blob_client(blob_name)
                    props = await blob.get_blob_properties()
                    existing_modified = (props.metadata or {}).get("sp_last_modified", "")
                    if existing_modified == last_modified:
                        logger.debug("Skipping page %s (unchanged)", title)
                        result.pages_skipped += 1
                        continue
                except ResourceNotFoundError:
                    pass  # Blob doesn't exist yet

            if dry_run:
                logger.info("[DRY RUN] Would sync page: %s", title)
                result.pages_synced += 1
                continue

            assert blob_client is not None  # guaranteed by dry_run guard above
            try:
                html = await self._get_page_html(full_site_id, page_id, title)
                if not html:
                    logger.debug("Skipping page %s (no text content)", title)
                    result.pages_skipped += 1
                    continue

                metadata = {
                    "sp_last_modified": last_modified,
                    "sp_page_id": page_id,
                    "sp_web_url": page.get("webUrl", ""),
                    "sp_source_type": "site_page",
                    "sp_title": title,
                    "sp_domain": self._config.domain,
                    "sp_document_type": self._config.document_type,
                }
                blob = blob_client.get_blob_client(blob_name)
                await blob.upload_blob(
                    html.encode("utf-8"),
                    overwrite=True,
                    metadata=metadata,
                )
                logger.info("Synced page: %s (%d bytes)", title, len(html))
                result.pages_synced += 1
            except Exception as exc:  # noqa: BLE001
                error_msg = f"Failed to sync page '{title}': {exc}"
                logger.error(error_msg)
                result.errors.append(error_msg)

        return expected_blobs

    # -- Deletion reconciliation -------------------------------------------

    async def _reconcile_deletions(
        self,
        blob_client: ContainerClient,
        prefix: str,
        expected_blobs: set[str],
        result: SyncResult,
        *,
        dry_run: bool,
        counter_attr: str,
    ) -> None:
        """Delete blobs under *prefix* that are not in *expected_blobs*.

        This removes orphaned blobs left behind when files are deleted or
        renamed in SharePoint. The blob soft-delete policy triggers
        automatic removal from the search index.
        """
        orphaned: list[str] = []
        async for blob_props in blob_client.list_blobs(name_starts_with=prefix):
            if blob_props.name not in expected_blobs:
                orphaned.append(blob_props.name)

        for blob_name in orphaned:
            if dry_run:
                logger.info("[DRY RUN] Would delete orphaned blob: %s", blob_name)
            else:
                await blob_client.delete_blob(blob_name)
                logger.info("Deleted orphaned blob: %s", blob_name)
            setattr(result, counter_attr, getattr(result, counter_attr) + 1)

        if orphaned:
            logger.info(
                "Reconciliation: %d orphaned blob(s) under %s",
                len(orphaned),
                prefix,
            )

    # -- Public API ---------------------------------------------------------

    async def sync(self, *, dry_run: bool = False) -> SyncResult:
        """Run the full sync: drive items + site pages -> blob storage.

        Args:
            dry_run: If True, list what would be synced without uploading.

        Returns:
            SyncResult with counts and any errors.
        """
        result = SyncResult()
        site_id = self._resolve_site_id()

        if not self._config.library_name:
            logger.warning(
                "SHAREPOINT_LIBRARY_NAME not set — syncing from the default "
                "document library. Ensure all content is appropriate for all "
                "chatbot users."
            )

        blob_client: ContainerClient | None = None
        self._http = httpx.AsyncClient(
            timeout=float(self._config.timeout_seconds),
        )
        self._semaphore = asyncio.Semaphore(self._config.max_concurrent_requests)

        try:
            if not dry_run:
                if not self._config.blob_account_url:
                    msg = "AZURE_STORAGE_ACCOUNT_URL is required for non-dry-run sync"
                    raise ValueError(msg)
                blob_client = ContainerClient(
                    account_url=self._config.blob_account_url,
                    container_name=self._config.blob_container,
                    credential=self._azure_credential,  # pyright: ignore[reportArgumentType]  # sync DAC works at runtime
                )

            expected_file_blobs = await self._sync_drive_items(
                site_id, blob_client, result, dry_run=dry_run
            )

            expected_page_blobs: set[str] = set()
            if self._config.sync_pages:
                expected_page_blobs = await self._sync_pages(
                    site_id, blob_client, result, dry_run=dry_run
                )

            # Deletion reconciliation — remove orphaned blobs
            if blob_client is not None and not self._config.skip_deletion_reconciliation:
                files_prefix = f"{self._config.blob_prefix}files/"
                await self._reconcile_deletions(
                    blob_client,
                    files_prefix,
                    expected_file_blobs,
                    result,
                    dry_run=dry_run,
                    counter_attr="files_deleted",
                )
                if self._config.sync_pages:
                    pages_prefix = f"{self._config.blob_prefix}pages/"
                    await self._reconcile_deletions(
                        blob_client,
                        pages_prefix,
                        expected_page_blobs,
                        result,
                        dry_run=dry_run,
                        counter_attr="pages_deleted",
                    )
        finally:
            if self._http:
                await self._http.aclose()
                self._http = None
            self._semaphore = None
            if blob_client:
                await blob_client.close()

        return result
