"""Tests for the SharePoint sync connector."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from azure.core.exceptions import ResourceNotFoundError

from src.connectors.sharepoint_sync import SharePointSync, SyncConfig, SyncResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides: Any) -> SyncConfig:
    """Create a SyncConfig with sensible defaults, applying any overrides."""
    defaults: dict[str, Any] = {
        "site_url": "https://contoso.sharepoint.com/sites/intranet",
        "tenant_id": "tid",
        "client_id": "cid",
        "client_secret": "secret",
        "blob_account_url": "https://storage.blob.core.windows.net",
        "blob_container": "docs",
        "blob_prefix": "sp/",
        "max_retries": 2,
    }
    defaults.update(overrides)
    return SyncConfig(**defaults)


def _make_sync(config: SyncConfig | None = None) -> SharePointSync:
    """Build a SharePointSync with mocked credentials."""
    with (
        patch("src.connectors.sharepoint_sync.ClientSecretCredential"),
        patch("src.connectors.sharepoint_sync.DefaultAzureCredential"),
    ):
        return SharePointSync(config or _make_config())


def _mock_blob_client() -> MagicMock:
    """Return a mock async ContainerClient with async methods."""
    container = MagicMock()
    blob = MagicMock()
    blob.get_blob_properties = AsyncMock()
    blob.upload_blob = AsyncMock()
    container.get_blob_client.return_value = blob
    container.close = AsyncMock()
    return container


def _init_http(sync: SharePointSync) -> None:
    """Set up internal HTTP client state so methods can be called directly."""
    import asyncio

    sync._http = MagicMock(spec=httpx.AsyncClient)  # pyright: ignore[reportPrivateUsage]
    sync._semaphore = asyncio.Semaphore(10)  # pyright: ignore[reportPrivateUsage]


# ---------------------------------------------------------------------------
# SyncConfig.from_env
# ---------------------------------------------------------------------------


def test_from_env_minimal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the site URL is truly required; others have defaults."""
    monkeypatch.setenv("SHAREPOINT_SITE_URL", "https://contoso.sharepoint.com")
    monkeypatch.setenv("SHAREPOINT_TENANT_ID", "tid")
    monkeypatch.setenv("SHAREPOINT_CLIENT_ID", "cid")
    monkeypatch.delenv("SHAREPOINT_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("SHAREPOINT_LIBRARY_NAME", raising=False)
    monkeypatch.delenv("SHAREPOINT_FOLDER_PATH", raising=False)

    cfg = SyncConfig.from_env()
    assert cfg.site_url == "https://contoso.sharepoint.com"
    assert cfg.tenant_id == "tid"
    assert cfg.client_id == "cid"
    assert cfg.client_secret is None
    assert cfg.library_name is None
    assert cfg.folder_path is None
    assert cfg.blob_container == "documents"
    assert cfg.blob_prefix == "sharepoint/"
    assert cfg.sync_pages is True


def test_from_env_missing_site_url_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SHAREPOINT_SITE_URL", raising=False)
    with pytest.raises(ValueError, match="SHAREPOINT_SITE_URL"):
        SyncConfig.from_env()


def test_from_env_all_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHAREPOINT_SITE_URL", "https://x.sharepoint.com/sites/hr")
    monkeypatch.setenv("SHAREPOINT_TENANT_ID", "t")
    monkeypatch.setenv("SHAREPOINT_CLIENT_ID", "c")
    monkeypatch.setenv("SHAREPOINT_CLIENT_SECRET", "s")
    monkeypatch.setenv("SHAREPOINT_LIBRARY_NAME", "Shared Docs")
    monkeypatch.setenv("SHAREPOINT_FOLDER_PATH", "policies/2024")
    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_URL", "https://sa.blob.core.windows.net")
    monkeypatch.setenv("AZURE_STORAGE_CONTAINER", "mycontainer")
    monkeypatch.setenv("AZURE_STORAGE_BLOB_PREFIX", "prefix/")
    monkeypatch.setenv("SHAREPOINT_SYNC_PAGES", "false")

    cfg = SyncConfig.from_env()
    assert cfg.client_secret == "s"
    assert cfg.library_name == "Shared Docs"
    assert cfg.folder_path == "policies/2024"
    assert cfg.blob_account_url == "https://sa.blob.core.windows.net"
    assert cfg.blob_container == "mycontainer"
    assert cfg.blob_prefix == "prefix/"
    assert cfg.sync_pages is False


def test_from_env_sync_pages_default_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHAREPOINT_SITE_URL", "https://x.sharepoint.com")
    monkeypatch.setenv("SHAREPOINT_TENANT_ID", "t")
    monkeypatch.setenv("SHAREPOINT_CLIENT_ID", "c")
    monkeypatch.delenv("SHAREPOINT_SYNC_PAGES", raising=False)
    cfg = SyncConfig.from_env()
    assert cfg.sync_pages is True


def test_from_env_empty_library_name_becomes_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty string for SHAREPOINT_LIBRARY_NAME should resolve to None."""
    monkeypatch.setenv("SHAREPOINT_SITE_URL", "https://x.sharepoint.com")
    monkeypatch.setenv("SHAREPOINT_TENANT_ID", "t")
    monkeypatch.setenv("SHAREPOINT_CLIENT_ID", "c")
    monkeypatch.setenv("SHAREPOINT_LIBRARY_NAME", "")
    cfg = SyncConfig.from_env()
    assert cfg.library_name is None


def test_from_env_new_config_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """New config fields load from environment variables."""
    monkeypatch.setenv("SHAREPOINT_SITE_URL", "https://x.sharepoint.com")
    monkeypatch.setenv("SHAREPOINT_TENANT_ID", "t")
    monkeypatch.setenv("SHAREPOINT_CLIENT_ID", "c")
    monkeypatch.setenv("SHAREPOINT_MAX_CONCURRENT_REQUESTS", "20")
    monkeypatch.setenv("SHAREPOINT_MAX_RETRIES", "3")
    monkeypatch.setenv("SHAREPOINT_TIMEOUT_SECONDS", "90")
    monkeypatch.setenv("SHAREPOINT_DOWNLOAD_TIMEOUT_SECONDS", "600")
    monkeypatch.setenv("SHAREPOINT_MAX_FILE_SIZE_MB", "200")
    cfg = SyncConfig.from_env()
    assert cfg.max_concurrent_requests == 20
    assert cfg.max_retries == 3
    assert cfg.timeout_seconds == 90
    assert cfg.download_timeout_seconds == 600
    assert cfg.max_file_size_mb == 200


# ---------------------------------------------------------------------------
# SyncResult
# ---------------------------------------------------------------------------


def test_sync_result_total_synced() -> None:
    r = SyncResult(files_synced=3, pages_synced=2)
    assert r.total_synced == 5


def test_sync_result_total_synced_zero() -> None:
    r = SyncResult()
    assert r.total_synced == 0


def test_sync_result_has_oversized_counter() -> None:
    r = SyncResult(files_oversized=4)
    assert r.files_oversized == 4


# ---------------------------------------------------------------------------
# _resolve_site_id
# ---------------------------------------------------------------------------


def test_resolve_site_id_root_site() -> None:
    sync = _make_sync(_make_config(site_url="https://contoso.sharepoint.com"))
    site_id = sync._resolve_site_id()  # pyright: ignore[reportPrivateUsage]
    assert site_id == "contoso.sharepoint.com"


def test_resolve_site_id_subsite() -> None:
    sync = _make_sync(_make_config(site_url="https://contoso.sharepoint.com/sites/intranet"))
    site_id = sync._resolve_site_id()  # pyright: ignore[reportPrivateUsage]
    assert site_id == "contoso.sharepoint.com:/sites/intranet"


def test_resolve_site_id_trailing_slash() -> None:
    sync = _make_sync(_make_config(site_url="https://contoso.sharepoint.com/sites/intranet/"))
    site_id = sync._resolve_site_id()  # pyright: ignore[reportPrivateUsage]
    assert site_id == "contoso.sharepoint.com:/sites/intranet"


# ---------------------------------------------------------------------------
# _sanitise_blob_name
# ---------------------------------------------------------------------------


def test_sanitise_blob_name_clean() -> None:
    assert SharePointSync._sanitise_blob_name("sharepoint/files/doc.pdf") == (
        "sharepoint/files/doc.pdf"
    )


def test_sanitise_blob_name_illegal_chars() -> None:
    result = SharePointSync._sanitise_blob_name('files/report#2024?.pdf')
    assert "#" not in result
    assert "?" not in result
    assert result == "files/report_2024_.pdf"


def test_sanitise_blob_name_strips_dots_and_spaces() -> None:
    result = SharePointSync._sanitise_blob_name("  .leading-dots. ")
    assert not result.startswith(" ")
    assert not result.startswith(".")
    assert not result.endswith(" ")


def test_sanitise_blob_name_preserves_slashes() -> None:
    result = SharePointSync._sanitise_blob_name("path/to/file.pdf")
    assert result == "path/to/file.pdf"


def test_sanitise_blob_name_all_illegal() -> None:
    result = SharePointSync._sanitise_blob_name('a*b"c<d>e|f\\g')
    assert result == "a_b_c_d_e_f_g"


# ---------------------------------------------------------------------------
# _should_skip_sensitivity
# ---------------------------------------------------------------------------


def test_sensitivity_label_skips_confidential() -> None:
    """Files with label above threshold should be skipped."""
    sync = _make_sync(_make_config(sensitivity_label_threshold="internal"))
    item = {
        "name": "salary.xlsx",
        "sensitivityLabel": {"displayName": "Confidential"},
    }
    assert sync._should_skip_sensitivity(item) is True  # pyright: ignore[reportPrivateUsage]


def test_sensitivity_label_allows_general() -> None:
    """Files at or below threshold should pass."""
    sync = _make_sync(_make_config(sensitivity_label_threshold="internal"))
    item = {
        "name": "handbook.pdf",
        "sensitivityLabel": {"displayName": "General"},
    }
    assert sync._should_skip_sensitivity(item) is False  # pyright: ignore[reportPrivateUsage]


def test_sensitivity_label_allows_at_threshold() -> None:
    """Files exactly at the threshold should pass (not be skipped)."""
    sync = _make_sync(_make_config(sensitivity_label_threshold="confidential"))
    item = {
        "name": "report.pdf",
        "sensitivityLabel": {"displayName": "Confidential"},
    }
    assert sync._should_skip_sensitivity(item) is False  # pyright: ignore[reportPrivateUsage]


def test_sensitivity_label_no_threshold_allows_all() -> None:
    """No filtering when threshold is None."""
    sync = _make_sync(_make_config(sensitivity_label_threshold=None))
    item = {
        "name": "secret.pdf",
        "sensitivityLabel": {"displayName": "Highly Confidential"},
    }
    assert sync._should_skip_sensitivity(item) is False  # pyright: ignore[reportPrivateUsage]


def test_sensitivity_label_no_label_on_item() -> None:
    """Items without sensitivity labels should pass."""
    sync = _make_sync(_make_config(sensitivity_label_threshold="general"))
    item = {"name": "normal.pdf"}
    assert sync._should_skip_sensitivity(item) is False  # pyright: ignore[reportPrivateUsage]


def test_sensitivity_label_highly_confidential_normalised() -> None:
    """'Highly Confidential' (with space) should be normalised correctly."""
    sync = _make_sync(_make_config(sensitivity_label_threshold="general"))
    item = {
        "name": "top-secret.pdf",
        "sensitivityLabel": {"displayName": "Highly Confidential"},
    }
    assert sync._should_skip_sensitivity(item) is True  # pyright: ignore[reportPrivateUsage]


# ---------------------------------------------------------------------------
# _get_page_html
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_page_html_text_web_parts() -> None:
    sync = _make_sync()
    _init_http(sync)
    sync._graph_get = AsyncMock(  # pyright: ignore[reportPrivateUsage]
        return_value={
            "value": [
                {
                    "@odata.type": "#microsoft.graph.textWebPart",
                    "innerHtml": "<p>Hello</p>",
                },
                {
                    "@odata.type": "#microsoft.graph.textWebPart",
                    "innerHtml": "<p>World</p>",
                },
            ]
        }
    )
    html = await sync._get_page_html("site-id", "page-1", "My Page")  # pyright: ignore[reportPrivateUsage]
    assert "<title>My Page</title>" in html
    assert "<p>Hello</p>" in html
    assert "<p>World</p>" in html


@pytest.mark.asyncio
async def test_get_page_html_empty_page() -> None:
    sync = _make_sync()
    _init_http(sync)
    sync._graph_get = AsyncMock(return_value={"value": []})  # pyright: ignore[reportPrivateUsage]
    html = await sync._get_page_html("site-id", "page-1", "Empty")  # pyright: ignore[reportPrivateUsage]
    assert html == ""


@pytest.mark.asyncio
async def test_get_page_html_mixed_web_parts() -> None:
    """Only textWebPart content should be included; other types are ignored."""
    sync = _make_sync()
    _init_http(sync)
    sync._graph_get = AsyncMock(  # pyright: ignore[reportPrivateUsage]
        return_value={
            "value": [
                {
                    "@odata.type": "#microsoft.graph.textWebPart",
                    "innerHtml": "<p>Kept</p>",
                },
                {
                    "@odata.type": "#microsoft.graph.standardWebPart",
                    "data": {"properties": {}},
                },
            ]
        }
    )
    html = await sync._get_page_html("site-id", "p1", "Mixed")  # pyright: ignore[reportPrivateUsage]
    assert "<p>Kept</p>" in html
    assert "standardWebPart" not in html


@pytest.mark.asyncio
async def test_get_page_html_empty_inner_html_skipped() -> None:
    """Text web parts with empty innerHtml should not appear."""
    sync = _make_sync()
    _init_http(sync)
    sync._graph_get = AsyncMock(  # pyright: ignore[reportPrivateUsage]
        return_value={
            "value": [
                {
                    "@odata.type": "#microsoft.graph.textWebPart",
                    "innerHtml": "",
                },
            ]
        }
    )
    html = await sync._get_page_html("site-id", "p1", "Blank")  # pyright: ignore[reportPrivateUsage]
    assert html == ""


# ---------------------------------------------------------------------------
# _list_drive_items
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_drive_items_flat() -> None:
    """Simple case: a single page of file items, no folders."""
    sync = _make_sync()
    _init_http(sync)
    sync._graph_get = AsyncMock(  # pyright: ignore[reportPrivateUsage]
        return_value={
            "value": [
                {"id": "f1", "name": "doc.pdf", "file": {}},
                {"id": "f2", "name": "notes.docx", "file": {}},
            ],
        }
    )
    items = await sync._list_drive_items("drive-1")  # pyright: ignore[reportPrivateUsage]
    assert len(items) == 2
    assert items[0]["name"] == "doc.pdf"


@pytest.mark.asyncio
async def test_list_drive_items_pagination() -> None:
    """Items spanning two pages should all be returned."""
    sync = _make_sync()
    _init_http(sync)

    page1 = {
        "value": [{"id": "f1", "name": "a.pdf", "file": {}}],
        "@odata.nextLink": "https://graph.microsoft.com/page2",
    }
    page2 = {
        "value": [{"id": "f2", "name": "b.pdf", "file": {}}],
    }

    sync._graph_get = AsyncMock(side_effect=[page1, page2])  # pyright: ignore[reportPrivateUsage]
    items = await sync._list_drive_items("drive-1")  # pyright: ignore[reportPrivateUsage]
    assert len(items) == 2


@pytest.mark.asyncio
async def test_list_drive_items_nested_folders() -> None:
    """Folders should be recursed into; only files returned."""
    sync = _make_sync()
    _init_http(sync)

    root_response = {
        "value": [
            {"id": "folder-1", "name": "subfolder", "folder": {"childCount": 1}},
            {"id": "f1", "name": "root.pdf", "file": {}},
        ],
    }
    subfolder_response = {
        "value": [
            {"id": "f2", "name": "nested.docx", "file": {}},
        ],
    }

    sync._graph_get = AsyncMock(  # pyright: ignore[reportPrivateUsage]
        side_effect=[root_response, subfolder_response],
    )
    items = await sync._list_drive_items("drive-1")  # pyright: ignore[reportPrivateUsage]
    names = {item["name"] for item in items}
    assert names == {"root.pdf", "nested.docx"}


@pytest.mark.asyncio
async def test_list_drive_items_with_folder_path() -> None:
    """When folder_path is configured, the URL should include that path."""
    sync = _make_sync(_make_config(folder_path="/policies/2024/"))
    _init_http(sync)
    sync._graph_get = AsyncMock(return_value={"value": []})  # pyright: ignore[reportPrivateUsage]

    await sync._list_drive_items("drive-1")  # pyright: ignore[reportPrivateUsage]

    call_url: str = sync._graph_get.call_args[0][0]  # pyright: ignore[reportPrivateUsage]
    assert "/root:/policies/2024:/children" in call_url


# ---------------------------------------------------------------------------
# _sync_drive_items
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_drive_items_extension_filtering() -> None:
    """Files with extensions not in the configured set should be skipped."""
    sync = _make_sync(_make_config(extensions={".pdf"}))
    _init_http(sync)
    result = SyncResult()
    blob = _mock_blob_client()

    sync._resolve_drive_id = AsyncMock(return_value="d1")  # pyright: ignore[reportPrivateUsage]
    sync._list_drive_items = AsyncMock(  # pyright: ignore[reportPrivateUsage]
        return_value=[
            {"id": "1", "name": "keep.pdf", "file": {}, "size": 100, "lastModifiedDateTime": "t1"},
            {"id": "2", "name": "skip.exe", "file": {}, "size": 100, "lastModifiedDateTime": "t2"},
        ]
    )
    blob.get_blob_client.return_value.get_blob_properties = AsyncMock(
        side_effect=ResourceNotFoundError("not found")
    )
    sync._graph_get_bytes = AsyncMock(return_value=b"pdf-data")  # pyright: ignore[reportPrivateUsage]

    await sync._sync_drive_items("site", blob, result, dry_run=False)  # pyright: ignore[reportPrivateUsage]

    assert result.files_synced == 1
    assert result.files_skipped == 1


@pytest.mark.asyncio
async def test_sync_drive_items_incremental_skip() -> None:
    """Files unchanged since last sync (same lastModified) should be skipped."""
    sync = _make_sync()
    _init_http(sync)
    result = SyncResult()
    blob = _mock_blob_client()

    sync._resolve_drive_id = AsyncMock(return_value="d1")  # pyright: ignore[reportPrivateUsage]
    sync._list_drive_items = AsyncMock(  # pyright: ignore[reportPrivateUsage]
        return_value=[
            {
                "id": "1",
                "name": "report.pdf",
                "file": {},
                "size": 100,
                "lastModifiedDateTime": "2024-01-01T00:00:00Z",
            },
        ]
    )

    props_mock = MagicMock()
    props_mock.metadata = {"sp_last_modified": "2024-01-01T00:00:00Z"}
    blob.get_blob_client.return_value.get_blob_properties = AsyncMock(return_value=props_mock)

    await sync._sync_drive_items("site", blob, result, dry_run=False)  # pyright: ignore[reportPrivateUsage]

    assert result.files_synced == 0
    assert result.files_skipped == 1


@pytest.mark.asyncio
async def test_sync_drive_items_dry_run() -> None:
    """Dry-run should count files as synced but never download or upload."""
    sync = _make_sync()
    _init_http(sync)
    result = SyncResult()

    sync._resolve_drive_id = AsyncMock(return_value="d1")  # pyright: ignore[reportPrivateUsage]
    sync._list_drive_items = AsyncMock(  # pyright: ignore[reportPrivateUsage]
        return_value=[
            {"id": "1", "name": "file.pdf", "file": {}, "size": 123, "lastModifiedDateTime": "t"},
        ]
    )

    await sync._sync_drive_items(  # pyright: ignore[reportPrivateUsage]
        "site", None, result, dry_run=True
    )

    assert result.files_synced == 1


@pytest.mark.asyncio
async def test_sync_drive_items_download_error() -> None:
    """A download failure should be recorded in errors, not raised."""
    sync = _make_sync()
    _init_http(sync)
    result = SyncResult()
    blob = _mock_blob_client()

    sync._resolve_drive_id = AsyncMock(return_value="d1")  # pyright: ignore[reportPrivateUsage]
    sync._list_drive_items = AsyncMock(  # pyright: ignore[reportPrivateUsage]
        return_value=[
            {
                "id": "1",
                "name": "broken.pdf",
                "file": {},
                "size": 100,
                "lastModifiedDateTime": "t",
                "@microsoft.graph.downloadUrl": "https://example.com/broken",
            },
        ]
    )
    blob.get_blob_client.return_value.get_blob_properties = AsyncMock(
        side_effect=ResourceNotFoundError("no blob")
    )
    sync._graph_get_bytes = AsyncMock(  # pyright: ignore[reportPrivateUsage]
        side_effect=Exception("network error"),
    )

    await sync._sync_drive_items("site", blob, result, dry_run=False)  # pyright: ignore[reportPrivateUsage]

    assert result.files_synced == 0
    assert len(result.errors) == 1
    assert "broken.pdf" in result.errors[0]


@pytest.mark.asyncio
async def test_sync_drive_items_oversized_file_skipped() -> None:
    """Files exceeding max_file_size_mb should be skipped."""
    sync = _make_sync(_make_config(max_file_size_mb=1))  # 1MB limit
    _init_http(sync)
    result = SyncResult()
    blob = _mock_blob_client()

    sync._resolve_drive_id = AsyncMock(return_value="d1")  # pyright: ignore[reportPrivateUsage]
    sync._list_drive_items = AsyncMock(  # pyright: ignore[reportPrivateUsage]
        return_value=[
            {
                "id": "1",
                "name": "huge.pdf",
                "file": {},
                "size": 2 * 1024 * 1024,  # 2MB — exceeds 1MB limit
                "lastModifiedDateTime": "t",
            },
            {
                "id": "2",
                "name": "small.pdf",
                "file": {},
                "size": 100,
                "lastModifiedDateTime": "t",
            },
        ]
    )
    blob.get_blob_client.return_value.get_blob_properties = AsyncMock(
        side_effect=ResourceNotFoundError("not found")
    )
    sync._graph_get_bytes = AsyncMock(return_value=b"data")  # pyright: ignore[reportPrivateUsage]

    await sync._sync_drive_items("site", blob, result, dry_run=False)  # pyright: ignore[reportPrivateUsage]

    assert result.files_oversized == 1
    assert result.files_synced == 1


# ---------------------------------------------------------------------------
# _graph_request retry logic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_graph_request_retries_on_429() -> None:
    """HTTP 429 should trigger retry with Retry-After header."""
    sync = _make_sync(_make_config(max_retries=2))
    _init_http(sync)
    sync._get_token = AsyncMock(return_value="token")  # pyright: ignore[reportPrivateUsage]

    throttled = httpx.Response(
        429,
        headers={"Retry-After": "0"},
        request=httpx.Request("GET", "https://graph.microsoft.com/test"),
    )
    success = httpx.Response(
        200,
        json={"value": []},
        request=httpx.Request("GET", "https://graph.microsoft.com/test"),
    )

    sync._http.request = AsyncMock(side_effect=[throttled, success])  # pyright: ignore[reportPrivateUsage]

    resp = await sync._graph_request("GET", "https://graph.microsoft.com/test")  # pyright: ignore[reportPrivateUsage]
    assert resp.status_code == 200
    assert sync._http.request.call_count == 2  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
async def test_graph_request_retries_on_503() -> None:
    """HTTP 503 should trigger retry."""
    sync = _make_sync(_make_config(max_retries=1))
    _init_http(sync)
    sync._get_token = AsyncMock(return_value="token")  # pyright: ignore[reportPrivateUsage]

    unavailable = httpx.Response(
        503,
        request=httpx.Request("GET", "https://graph.microsoft.com/test"),
    )
    success = httpx.Response(
        200,
        json={},
        request=httpx.Request("GET", "https://graph.microsoft.com/test"),
    )

    sync._http.request = AsyncMock(side_effect=[unavailable, success])  # pyright: ignore[reportPrivateUsage]

    resp = await sync._graph_request("GET", "https://graph.microsoft.com/test")  # pyright: ignore[reportPrivateUsage]
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_graph_request_max_retries_exceeded() -> None:
    """Exceeding max retries should raise."""
    sync = _make_sync(_make_config(max_retries=1))
    _init_http(sync)
    sync._get_token = AsyncMock(return_value="token")  # pyright: ignore[reportPrivateUsage]

    throttled = httpx.Response(
        429,
        headers={"Retry-After": "0"},
        request=httpx.Request("GET", "https://graph.microsoft.com/test"),
    )

    sync._http.request = AsyncMock(return_value=throttled)  # pyright: ignore[reportPrivateUsage]

    with pytest.raises(httpx.HTTPStatusError):
        await sync._graph_request("GET", "https://graph.microsoft.com/test")  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
async def test_graph_request_retries_on_transport_error() -> None:
    """Network errors should be retried."""
    sync = _make_sync(_make_config(max_retries=1))
    _init_http(sync)
    sync._get_token = AsyncMock(return_value="token")  # pyright: ignore[reportPrivateUsage]

    success = httpx.Response(
        200,
        json={},
        request=httpx.Request("GET", "https://graph.microsoft.com/test"),
    )

    sync._http.request = AsyncMock(  # pyright: ignore[reportPrivateUsage]
        side_effect=[httpx.ConnectError("conn refused"), success]
    )

    resp = await sync._graph_request("GET", "https://graph.microsoft.com/test")  # pyright: ignore[reportPrivateUsage]
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_graph_request_transport_error_max_retries() -> None:
    """Persistent transport errors should raise after max retries."""
    sync = _make_sync(_make_config(max_retries=1))
    _init_http(sync)
    sync._get_token = AsyncMock(return_value="token")  # pyright: ignore[reportPrivateUsage]

    sync._http.request = AsyncMock(  # pyright: ignore[reportPrivateUsage]
        side_effect=httpx.ConnectError("conn refused")
    )

    with pytest.raises(httpx.ConnectError):
        await sync._graph_request("GET", "https://graph.microsoft.com/test")  # pyright: ignore[reportPrivateUsage]


# ---------------------------------------------------------------------------
# Token caching
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_token_caches() -> None:
    """Subsequent calls should return cached token without re-acquiring."""
    import time

    sync = _make_sync()

    mock_token = MagicMock()
    mock_token.token = "cached-token"
    mock_token.expires_on = time.time() + 3600  # 1 hour from now

    with patch.object(sync, "_graph_credential") as mock_cred:
        mock_cred.get_token.return_value = mock_token

        loop = asyncio.get_running_loop()
        with patch.object(loop, "run_in_executor", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_token
            t1 = await sync._get_token()  # pyright: ignore[reportPrivateUsage]
            t2 = await sync._get_token()  # pyright: ignore[reportPrivateUsage]

    assert t1 == "cached-token"
    assert t2 == "cached-token"
    # run_in_executor should only be called once (second call uses cache)
    assert mock_exec.call_count == 1


# ---------------------------------------------------------------------------
# _build_graph_credential
# ---------------------------------------------------------------------------


def test_build_graph_credential_with_secret() -> None:
    """When client_secret is provided, should use ClientSecretCredential."""
    with (
        patch("src.connectors.sharepoint_sync.DefaultAzureCredential"),
        patch("src.connectors.sharepoint_sync.ClientSecretCredential") as mock_csc,
    ):
        sync = SharePointSync(_make_config(client_secret="my-secret"))
        mock_csc.assert_called_once()
        assert sync._graph_credential is mock_csc.return_value  # pyright: ignore[reportPrivateUsage]


def test_build_graph_credential_without_secret() -> None:
    """Without client_secret, should fall back to DefaultAzureCredential."""
    with (
        patch("src.connectors.sharepoint_sync.DefaultAzureCredential") as mock_dac,
        patch("src.connectors.sharepoint_sync.ClientSecretCredential") as mock_csc,
    ):
        sync = SharePointSync(_make_config(client_secret=None))
        mock_csc.assert_not_called()
        assert sync._graph_credential is mock_dac.return_value  # pyright: ignore[reportPrivateUsage]


# ---------------------------------------------------------------------------
# _sync_pages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_pages_incremental_skip() -> None:
    """Pages unchanged since last sync should be skipped."""
    sync = _make_sync()
    _init_http(sync)
    result = SyncResult()
    blob = _mock_blob_client()

    sync._graph_get = AsyncMock(  # pyright: ignore[reportPrivateUsage]
        side_effect=[
            {"id": "full-site-id"},
            {
                "value": [
                    {
                        "id": "p1",
                        "title": "Welcome",
                        "name": "welcome.aspx",
                        "lastModifiedDateTime": "2024-06-01T00:00:00Z",
                    },
                ],
            },
        ]
    )

    props_mock = MagicMock()
    props_mock.metadata = {"sp_last_modified": "2024-06-01T00:00:00Z"}
    blob.get_blob_client.return_value.get_blob_properties = AsyncMock(return_value=props_mock)

    await sync._sync_pages("site", blob, result, dry_run=False)  # pyright: ignore[reportPrivateUsage]

    assert result.pages_synced == 0
    assert result.pages_skipped == 1


@pytest.mark.asyncio
async def test_sync_pages_empty_content_skipped() -> None:
    """Pages with no text web parts should be skipped."""
    sync = _make_sync()
    _init_http(sync)
    result = SyncResult()
    blob = _mock_blob_client()

    sync._graph_get = AsyncMock(  # pyright: ignore[reportPrivateUsage]
        side_effect=[
            {"id": "full-site-id"},
            {
                "value": [
                    {
                        "id": "p1",
                        "title": "Empty Page",
                        "name": "empty.aspx",
                        "lastModifiedDateTime": "2024-06-01T00:00:00Z",
                    },
                ],
            },
            {"value": []},
        ]
    )

    blob.get_blob_client.return_value.get_blob_properties = AsyncMock(
        side_effect=ResourceNotFoundError("not found")
    )

    await sync._sync_pages("site", blob, result, dry_run=False)  # pyright: ignore[reportPrivateUsage]

    assert result.pages_synced == 0
    assert result.pages_skipped == 1


@pytest.mark.asyncio
async def test_sync_pages_dry_run() -> None:
    """Dry-run should count pages without fetching content or uploading."""
    sync = _make_sync()
    _init_http(sync)
    result = SyncResult()

    sync._graph_get = AsyncMock(  # pyright: ignore[reportPrivateUsage]
        side_effect=[
            {"id": "full-site-id"},
            {
                "value": [
                    {
                        "id": "p1",
                        "title": "Page A",
                        "name": "page-a.aspx",
                        "lastModifiedDateTime": "t",
                    },
                ],
            },
        ]
    )

    await sync._sync_pages(  # pyright: ignore[reportPrivateUsage]
        "site", None, result, dry_run=True
    )

    assert result.pages_synced == 1


@pytest.mark.asyncio
async def test_sync_pages_uploads_html() -> None:
    """A page with text content should be uploaded as HTML to blob storage."""
    sync = _make_sync()
    _init_http(sync)
    result = SyncResult()
    blob = _mock_blob_client()
    blob_instance = blob.get_blob_client.return_value

    blob_instance.get_blob_properties = AsyncMock(
        side_effect=ResourceNotFoundError("not found")
    )

    sync._graph_get = AsyncMock(  # pyright: ignore[reportPrivateUsage]
        side_effect=[
            {"id": "full-site-id"},
            {
                "value": [
                    {
                        "id": "p1",
                        "title": "News",
                        "name": "news.aspx",
                        "lastModifiedDateTime": "2024-06-01T00:00:00Z",
                    },
                ],
            },
            {
                "value": [
                    {
                        "@odata.type": "#microsoft.graph.textWebPart",
                        "innerHtml": "<p>Breaking news</p>",
                    },
                ],
            },
        ]
    )

    await sync._sync_pages("site", blob, result, dry_run=False)  # pyright: ignore[reportPrivateUsage]

    assert result.pages_synced == 1
    blob_instance.upload_blob.assert_called_once()
    uploaded_bytes: bytes = blob_instance.upload_blob.call_args[0][0]
    assert b"Breaking news" in uploaded_bytes
    assert b"<title>News</title>" in uploaded_bytes

    call_kwargs: dict[str, Any] = blob_instance.upload_blob.call_args[1]
    assert call_kwargs["metadata"]["sp_source_type"] == "site_page"
    assert call_kwargs["metadata"]["sp_title"] == "News"


# ---------------------------------------------------------------------------
# sync() public API
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_dry_run_no_blob_url_needed() -> None:
    """Dry run should work without AZURE_STORAGE_ACCOUNT_URL."""
    sync = _make_sync(_make_config(blob_account_url=""))
    sync._sync_drive_items = AsyncMock(return_value=set())  # pyright: ignore[reportPrivateUsage]
    sync._sync_pages = AsyncMock(return_value=set())  # pyright: ignore[reportPrivateUsage]

    result = await sync.sync(dry_run=True)

    assert isinstance(result, SyncResult)
    sync._sync_drive_items.assert_called_once()  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
async def test_sync_non_dry_run_requires_blob_url() -> None:
    """Non-dry-run sync should raise without storage URL."""
    sync = _make_sync(_make_config(blob_account_url=""))

    with pytest.raises(ValueError, match="AZURE_STORAGE_ACCOUNT_URL"):
        await sync.sync(dry_run=False)


@pytest.mark.asyncio
async def test_sync_closes_http_client() -> None:
    """HTTP client should be closed even if sync raises."""
    sync = _make_sync()
    sync._sync_drive_items = AsyncMock(  # pyright: ignore[reportPrivateUsage]
        side_effect=RuntimeError("boom")
    )

    mock_container = MagicMock()
    mock_container.close = AsyncMock()

    with (
        patch(
            "src.connectors.sharepoint_sync.ContainerClient",
            return_value=mock_container,
        ),
        pytest.raises(RuntimeError, match="boom"),
    ):
        await sync.sync(dry_run=False)

    assert sync._http is None  # pyright: ignore[reportPrivateUsage]


# ---------------------------------------------------------------------------
# _reconcile_deletions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_deletions_removes_orphaned_blobs() -> None:
    """Blobs not in the expected set should be deleted."""
    sync = _make_sync()
    _init_http(sync)
    result = SyncResult()
    blob = _mock_blob_client()

    # Simulate 3 blobs in storage, only 1 is expected
    # Note: MagicMock(name=...) sets the mock's repr name, not an attribute.
    blob_items = []
    for n in ("sp/files/keep.pdf", "sp/files/orphan1.pdf", "sp/files/orphan2.docx"):
        m = MagicMock()
        m.name = n
        blob_items.append(m)

    async def _list_blobs(name_starts_with: str) -> Any:  # noqa: ANN401
        for item in blob_items:
            yield item

    blob.list_blobs = _list_blobs
    blob.delete_blob = AsyncMock()

    expected = {"sp/files/keep.pdf"}
    await sync._reconcile_deletions(  # pyright: ignore[reportPrivateUsage]
        blob, "sp/files/", expected, result,
        dry_run=False, counter_attr="files_deleted",
    )

    assert result.files_deleted == 2
    assert blob.delete_blob.call_count == 2


@pytest.mark.asyncio
async def test_reconcile_deletions_preserves_active_blobs() -> None:
    """Blobs in the expected set should not be deleted."""
    sync = _make_sync()
    _init_http(sync)
    result = SyncResult()
    blob = _mock_blob_client()

    active = MagicMock()
    active.name = "sp/files/active.pdf"
    blob_items = [active]

    async def _list_blobs(name_starts_with: str) -> Any:  # noqa: ANN401
        for item in blob_items:
            yield item

    blob.list_blobs = _list_blobs
    blob.delete_blob = AsyncMock()

    expected = {"sp/files/active.pdf"}
    await sync._reconcile_deletions(  # pyright: ignore[reportPrivateUsage]
        blob, "sp/files/", expected, result,
        dry_run=False, counter_attr="files_deleted",
    )

    assert result.files_deleted == 0
    blob.delete_blob.assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_deletions_dry_run_no_actual_delete() -> None:
    """Dry run should count deletions but not actually delete."""
    sync = _make_sync()
    _init_http(sync)
    result = SyncResult()
    blob = _mock_blob_client()

    old_page = MagicMock()
    old_page.name = "sp/pages/old.html"
    blob_items = [old_page]

    async def _list_blobs(name_starts_with: str) -> Any:  # noqa: ANN401
        for item in blob_items:
            yield item

    blob.list_blobs = _list_blobs
    blob.delete_blob = AsyncMock()

    await sync._reconcile_deletions(  # pyright: ignore[reportPrivateUsage]
        blob, "sp/pages/", set(), result,
        dry_run=True, counter_attr="pages_deleted",
    )

    assert result.pages_deleted == 1
    blob.delete_blob.assert_not_called()


# ---------------------------------------------------------------------------
# _resolve_drive_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_drive_id_named_library() -> None:
    """When library_name is set, the matching drive should be returned."""
    sync = _make_sync(_make_config(library_name="HR Policies"))
    _init_http(sync)
    sync._graph_get = AsyncMock(  # pyright: ignore[reportPrivateUsage]
        return_value={
            "value": [
                {"id": "d-other", "name": "Documents"},
                {"id": "d-hr", "name": "HR Policies"},
            ]
        }
    )

    drive_id = await sync._resolve_drive_id("site-id")  # pyright: ignore[reportPrivateUsage]
    assert drive_id == "d-hr"


@pytest.mark.asyncio
async def test_resolve_drive_id_named_library_not_found() -> None:
    """Missing library name should raise with available names."""
    sync = _make_sync(_make_config(library_name="Nonexistent"))
    _init_http(sync)
    sync._graph_get = AsyncMock(  # pyright: ignore[reportPrivateUsage]
        return_value={
            "value": [{"id": "d1", "name": "Documents"}]
        }
    )

    with pytest.raises(ValueError, match="Nonexistent"):
        await sync._resolve_drive_id("site-id")  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
async def test_resolve_drive_id_no_drives() -> None:
    """Empty drive list should raise."""
    sync = _make_sync(_make_config(library_name=None))
    _init_http(sync)
    sync._graph_get = AsyncMock(  # pyright: ignore[reportPrivateUsage]
        return_value={"value": []}
    )

    with pytest.raises(ValueError, match="No document libraries"):
        await sync._resolve_drive_id("site-id")  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
async def test_resolve_drive_id_default_first_drive() -> None:
    """Without library_name, first drive should be returned."""
    sync = _make_sync(_make_config(library_name=None))
    _init_http(sync)
    sync._graph_get = AsyncMock(  # pyright: ignore[reportPrivateUsage]
        return_value={
            "value": [
                {"id": "d-first", "name": "Documents"},
                {"id": "d-second", "name": "Archive"},
            ]
        }
    )

    drive_id = await sync._resolve_drive_id("site-id")  # pyright: ignore[reportPrivateUsage]
    assert drive_id == "d-first"


# ---------------------------------------------------------------------------
# _list_drive_items — additional edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_drive_items_empty_folder() -> None:
    """Empty folder should not cause errors."""
    sync = _make_sync()
    _init_http(sync)

    root_response = {
        "value": [
            {"id": "folder-1", "name": "empty", "folder": {"childCount": 0}},
        ],
    }
    subfolder_response = {"value": []}

    sync._graph_get = AsyncMock(  # pyright: ignore[reportPrivateUsage]
        side_effect=[root_response, subfolder_response],
    )
    items = await sync._list_drive_items("drive-1")  # pyright: ignore[reportPrivateUsage]
    assert items == []


# ---------------------------------------------------------------------------
# _sync_drive_items — metadata, sensitivity, boundary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_drive_items_uploads_correct_metadata() -> None:
    """All required metadata fields should be set on uploaded blobs."""
    sync = _make_sync(_make_config(domain="it", document_type="guideline"))
    _init_http(sync)
    result = SyncResult()
    blob = _mock_blob_client()
    blob_instance = blob.get_blob_client.return_value
    blob_instance.get_blob_properties = AsyncMock(
        side_effect=ResourceNotFoundError("not found")
    )

    sync._resolve_drive_id = AsyncMock(return_value="d1")  # pyright: ignore[reportPrivateUsage]
    sync._list_drive_items = AsyncMock(  # pyright: ignore[reportPrivateUsage]
        return_value=[
            {
                "id": "f1",
                "name": "guide.pdf",
                "file": {},
                "size": 100,
                "lastModifiedDateTime": "2024-01-01T00:00:00Z",
                "webUrl": "https://sp.com/guide.pdf",
            },
        ]
    )
    sync._graph_get_bytes = AsyncMock(return_value=b"data")  # pyright: ignore[reportPrivateUsage]

    await sync._sync_drive_items("site", blob, result, dry_run=False)  # pyright: ignore[reportPrivateUsage]

    call_kwargs: dict[str, Any] = blob_instance.upload_blob.call_args[1]
    meta = call_kwargs["metadata"]
    assert meta["sp_last_modified"] == "2024-01-01T00:00:00Z"
    assert meta["sp_item_id"] == "f1"
    assert meta["sp_web_url"] == "https://sp.com/guide.pdf"
    assert meta["sp_source_type"] == "drive_item"
    assert meta["sp_domain"] == "it"
    assert meta["sp_document_type"] == "guideline"


@pytest.mark.asyncio
async def test_sync_drive_items_sensitivity_skipped_not_in_expected_blobs() -> None:
    """Files skipped by sensitivity should NOT appear in expected_blobs set."""
    sync = _make_sync(
        _make_config(
            sensitivity_label_threshold="general",
            extensions={".pdf"},
        )
    )
    _init_http(sync)
    result = SyncResult()
    blob = _mock_blob_client()

    sync._resolve_drive_id = AsyncMock(return_value="d1")  # pyright: ignore[reportPrivateUsage]
    sync._list_drive_items = AsyncMock(  # pyright: ignore[reportPrivateUsage]
        return_value=[
            {
                "id": "1",
                "name": "public.pdf",
                "file": {},
                "size": 100,
                "lastModifiedDateTime": "t",
            },
            {
                "id": "2",
                "name": "secret.pdf",
                "file": {},
                "size": 100,
                "lastModifiedDateTime": "t",
                "sensitivityLabel": {"displayName": "Confidential"},
            },
        ]
    )
    blob.get_blob_client.return_value.get_blob_properties = AsyncMock(
        side_effect=ResourceNotFoundError("not found")
    )
    sync._graph_get_bytes = AsyncMock(return_value=b"data")  # pyright: ignore[reportPrivateUsage]

    expected = await sync._sync_drive_items("site", blob, result, dry_run=False)  # pyright: ignore[reportPrivateUsage]

    assert result.files_skipped_sensitivity == 1
    assert result.files_synced == 1
    # Only the public file should be in expected blobs
    assert len(expected) == 1
    assert any("public.pdf" in name for name in expected)
    assert not any("secret.pdf" in name for name in expected)


@pytest.mark.asyncio
async def test_sync_drive_items_file_size_at_boundary_syncs() -> None:
    """A file exactly at the size limit should be synced, not skipped."""
    sync = _make_sync(_make_config(max_file_size_mb=1, extensions={".pdf"}))
    _init_http(sync)
    result = SyncResult()
    blob = _mock_blob_client()

    exact_limit = 1 * 1024 * 1024  # exactly 1MB
    sync._resolve_drive_id = AsyncMock(return_value="d1")  # pyright: ignore[reportPrivateUsage]
    sync._list_drive_items = AsyncMock(  # pyright: ignore[reportPrivateUsage]
        return_value=[
            {
                "id": "1",
                "name": "exact.pdf",
                "file": {},
                "size": exact_limit,
                "lastModifiedDateTime": "t",
            },
        ]
    )
    blob.get_blob_client.return_value.get_blob_properties = AsyncMock(
        side_effect=ResourceNotFoundError("not found")
    )
    sync._graph_get_bytes = AsyncMock(return_value=b"data")  # pyright: ignore[reportPrivateUsage]

    await sync._sync_drive_items("site", blob, result, dry_run=False)  # pyright: ignore[reportPrivateUsage]

    assert result.files_synced == 1
    assert result.files_oversized == 0


@pytest.mark.asyncio
async def test_sync_drive_items_blob_auth_error_propagates() -> None:
    """Non-ResourceNotFoundError from blob check should propagate, not be swallowed.

    This verifies P0 #7 — only ResourceNotFoundError is caught at the blob
    existence check. Other errors (auth, network) must bubble up so they're
    not silently treated as "blob doesn't exist".
    """
    sync = _make_sync(_make_config(extensions={".pdf"}))
    _init_http(sync)
    result = SyncResult()
    blob = _mock_blob_client()

    sync._resolve_drive_id = AsyncMock(return_value="d1")  # pyright: ignore[reportPrivateUsage]
    sync._list_drive_items = AsyncMock(  # pyright: ignore[reportPrivateUsage]
        return_value=[
            {
                "id": "1",
                "name": "file.pdf",
                "file": {},
                "size": 100,
                "lastModifiedDateTime": "t",
            },
        ]
    )
    from azure.core.exceptions import ClientAuthenticationError

    blob.get_blob_client.return_value.get_blob_properties = AsyncMock(
        side_effect=ClientAuthenticationError("auth failed")
    )

    # Auth errors should propagate — not swallowed like the old bare except
    with pytest.raises(ClientAuthenticationError, match="auth failed"):
        await sync._sync_drive_items("site", blob, result, dry_run=False)  # pyright: ignore[reportPrivateUsage]


# ---------------------------------------------------------------------------
# _graph_request — Retry-After header, uninitialised guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_graph_request_uses_retry_after_value() -> None:
    """When Retry-After is a digit string, it should be used as wait time."""
    sync = _make_sync(_make_config(max_retries=1))
    _init_http(sync)
    sync._get_token = AsyncMock(return_value="token")  # pyright: ignore[reportPrivateUsage]

    throttled = httpx.Response(
        429,
        headers={"Retry-After": "0"},
        request=httpx.Request("GET", "https://graph.microsoft.com/test"),
    )
    success = httpx.Response(
        200,
        json={},
        request=httpx.Request("GET", "https://graph.microsoft.com/test"),
    )

    sync._http.request = AsyncMock(side_effect=[throttled, success])  # pyright: ignore[reportPrivateUsage]

    # Should succeed (the 0-second wait means instant retry)
    resp = await sync._graph_request("GET", "https://graph.microsoft.com/test")  # pyright: ignore[reportPrivateUsage]
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_graph_request_non_digit_retry_after_uses_backoff() -> None:
    """Non-digit Retry-After (e.g. HTTP date) should fall back to exponential backoff."""
    sync = _make_sync(_make_config(max_retries=1))
    _init_http(sync)
    sync._get_token = AsyncMock(return_value="token")  # pyright: ignore[reportPrivateUsage]

    throttled = httpx.Response(
        429,
        headers={"Retry-After": "Mon, 10 Mar 2025 12:00:00 GMT"},
        request=httpx.Request("GET", "https://graph.microsoft.com/test"),
    )
    success = httpx.Response(
        200,
        json={},
        request=httpx.Request("GET", "https://graph.microsoft.com/test"),
    )

    sync._http.request = AsyncMock(side_effect=[throttled, success])  # pyright: ignore[reportPrivateUsage]

    resp = await sync._graph_request("GET", "https://graph.microsoft.com/test")  # pyright: ignore[reportPrivateUsage]
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_graph_request_raises_when_not_initialised() -> None:
    """Calling _graph_request before sync() should raise RuntimeError."""
    sync = _make_sync()
    # Do NOT call _init_http — leave _http and _semaphore as None

    with pytest.raises(RuntimeError, match="HTTP client not initialised"):
        await sync._graph_request("GET", "https://graph.microsoft.com/test")  # pyright: ignore[reportPrivateUsage]


# ---------------------------------------------------------------------------
# _get_page_html — title XSS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_page_html_title_html_escaped() -> None:
    """HTML special characters in page titles should be escaped."""
    sync = _make_sync()
    _init_http(sync)
    sync._graph_get = AsyncMock(  # pyright: ignore[reportPrivateUsage]
        return_value={
            "value": [
                {
                    "@odata.type": "#microsoft.graph.textWebPart",
                    "innerHtml": "<p>Content</p>",
                },
            ]
        }
    )
    html = await sync._get_page_html(  # pyright: ignore[reportPrivateUsage]
        "site-id", "page-1", '<script>alert("xss")</script>'
    )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


# ---------------------------------------------------------------------------
# sync() — full flow integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_calls_reconciliation_when_enabled() -> None:
    """sync() should call _reconcile_deletions for both files and pages."""
    sync = _make_sync(
        _make_config(skip_deletion_reconciliation=False, sync_pages=True)
    )
    sync._sync_drive_items = AsyncMock(return_value={"sp/files/a.pdf"})  # pyright: ignore[reportPrivateUsage]
    sync._sync_pages = AsyncMock(return_value={"sp/pages/b.html"})  # pyright: ignore[reportPrivateUsage]
    sync._reconcile_deletions = AsyncMock()  # pyright: ignore[reportPrivateUsage]

    mock_container = MagicMock()
    mock_container.close = AsyncMock()

    with patch(
        "src.connectors.sharepoint_sync.ContainerClient",
        return_value=mock_container,
    ):
        await sync.sync(dry_run=False)

    assert sync._reconcile_deletions.call_count == 2  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
async def test_sync_skips_reconciliation_when_flag_set() -> None:
    """skip_deletion_reconciliation should prevent deletion phase."""
    sync = _make_sync(
        _make_config(skip_deletion_reconciliation=True, sync_pages=True)
    )
    sync._sync_drive_items = AsyncMock(return_value=set())  # pyright: ignore[reportPrivateUsage]
    sync._sync_pages = AsyncMock(return_value=set())  # pyright: ignore[reportPrivateUsage]
    sync._reconcile_deletions = AsyncMock()  # pyright: ignore[reportPrivateUsage]

    mock_container = MagicMock()
    mock_container.close = AsyncMock()

    with patch(
        "src.connectors.sharepoint_sync.ContainerClient",
        return_value=mock_container,
    ):
        await sync.sync(dry_run=False)

    sync._reconcile_deletions.assert_not_called()  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
async def test_sync_skips_pages_when_disabled() -> None:
    """sync_pages=False should skip page sync entirely."""
    sync = _make_sync(_make_config(sync_pages=False))
    sync._sync_drive_items = AsyncMock(return_value=set())  # pyright: ignore[reportPrivateUsage]
    sync._sync_pages = AsyncMock(return_value=set())  # pyright: ignore[reportPrivateUsage]

    result = await sync.sync(dry_run=True)

    sync._sync_pages.assert_not_called()  # pyright: ignore[reportPrivateUsage]
    assert isinstance(result, SyncResult)


@pytest.mark.asyncio
async def test_sync_drive_items_none_blob_client_raises_in_non_dry_run() -> None:
    """Passing None blob_client in non-dry-run should raise ValueError."""
    sync = _make_sync(_make_config(extensions={".pdf"}))
    _init_http(sync)
    result = SyncResult()

    sync._resolve_drive_id = AsyncMock(return_value="d1")  # pyright: ignore[reportPrivateUsage]
    sync._list_drive_items = AsyncMock(  # pyright: ignore[reportPrivateUsage]
        return_value=[
            {
                "id": "1",
                "name": "file.pdf",
                "file": {},
                "size": 100,
                "lastModifiedDateTime": "t",
            },
        ]
    )

    with pytest.raises(ValueError, match="blob_client is required"):
        await sync._sync_drive_items(  # pyright: ignore[reportPrivateUsage]
            "site", None, result, dry_run=False
        )


@pytest.mark.asyncio
async def test_sync_download_timeout_used_for_binary_downloads() -> None:
    """_graph_get_bytes should use download_timeout_seconds, not the default."""
    sync = _make_sync(_make_config(
        timeout_seconds=10,
        download_timeout_seconds=999,
    ))
    _init_http(sync)
    sync._get_token = AsyncMock(return_value="token")  # pyright: ignore[reportPrivateUsage]

    success = httpx.Response(
        200,
        content=b"binary-data",
        request=httpx.Request("GET", "https://graph.microsoft.com/download"),
    )
    sync._http.request = AsyncMock(return_value=success)  # pyright: ignore[reportPrivateUsage]

    await sync._graph_get_bytes("https://graph.microsoft.com/download")  # pyright: ignore[reportPrivateUsage]

    call_kwargs = sync._http.request.call_args  # pyright: ignore[reportPrivateUsage]
    assert call_kwargs.kwargs.get("timeout") == 999.0
