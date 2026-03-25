"""Tests for the SharePoint indexer scripts shared utilities."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from scripts.search_api import SearchApiClient, get_env
from scripts.setup_sharepoint_indexer import (
    _create_data_source,  # pyright: ignore[reportPrivateUsage]
    _create_index,  # pyright: ignore[reportPrivateUsage]
    _create_indexer,  # pyright: ignore[reportPrivateUsage]
    _create_skillset,  # pyright: ignore[reportPrivateUsage]
    _extract_storage_account_name,  # pyright: ignore[reportPrivateUsage]
    _get_subscription_id,  # pyright: ignore[reportPrivateUsage]
    _teardown,  # pyright: ignore[reportPrivateUsage]
)

# ---------------------------------------------------------------------------
# _extract_storage_account_name
# ---------------------------------------------------------------------------


class TestExtractStorageAccountName:
    def test_standard_url(self) -> None:
        result = _extract_storage_account_name("https://surfdev.blob.core.windows.net")
        assert result == "surfdev"

    def test_url_with_path(self) -> None:
        result = _extract_storage_account_name(
            "https://surfdev.blob.core.windows.net/container/blob"
        )
        assert result == "surfdev"

    def test_sovereign_cloud_url(self) -> None:
        """Non-.com domains (sovereign clouds) should work."""
        result = _extract_storage_account_name("https://surfdev.blob.core.chinacloudapi.cn")
        assert result == "surfdev"

    def test_trailing_slash(self) -> None:
        result = _extract_storage_account_name("https://mystorage.blob.core.windows.net/")
        assert result == "mystorage"

    def test_empty_url(self) -> None:
        result = _extract_storage_account_name("")
        assert result == ""


# ---------------------------------------------------------------------------
# _get_subscription_id
# ---------------------------------------------------------------------------


class TestGetSubscriptionId:
    def test_from_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "sub-123")
        result = _get_subscription_id()
        assert result == "sub-123"

    def test_fallback_to_az_cli(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AZURE_SUBSCRIPTION_ID", raising=False)
        with patch("scripts.setup_sharepoint_indexer.subprocess") as mock_sub:
            mock_result = MagicMock()
            mock_result.stdout = "cli-sub-456\n"
            mock_sub.run.return_value = mock_result
            result = _get_subscription_id()
            assert result == "cli-sub-456"


# ---------------------------------------------------------------------------
# SearchApiClient
# ---------------------------------------------------------------------------


class TestSearchApiClient:
    def test_request_adds_auth_and_api_version(self) -> None:
        mock_client = MagicMock(spec=httpx.Client)
        mock_response = MagicMock(spec=httpx.Response)
        mock_client.request.return_value = mock_response

        with (
            patch("scripts.search_api.DefaultAzureCredential") as mock_cred_cls,
            patch(
                "scripts.search_api.get_env",
                return_value="https://search.example.com",
            ),
        ):
            mock_cred = MagicMock()
            mock_token = MagicMock()
            mock_token.token = "test-token"
            mock_cred.get_token.return_value = mock_token
            mock_cred_cls.return_value = mock_cred

            api = SearchApiClient(mock_client)
            api.request("GET", "indexes/test-index")

        mock_client.request.assert_called_once()
        call_kwargs = mock_client.request.call_args
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers")
        assert headers["Authorization"] == "Bearer test-token"
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params")
        assert params["api-version"] == "2024-07-01"

    def test_check_response_success(self) -> None:
        mock_client = MagicMock(spec=httpx.Client)
        with (
            patch("scripts.search_api.DefaultAzureCredential"),
            patch(
                "scripts.search_api.get_env",
                return_value="https://search.example.com",
            ),
        ):
            api = SearchApiClient(mock_client)

        for status in (200, 201, 204):
            resp = MagicMock(spec=httpx.Response)
            resp.status_code = status
            # Should not raise
            api.check_response(resp, "test-resource")

    def test_check_response_failure_exits(self) -> None:
        mock_client = MagicMock(spec=httpx.Client)
        with (
            patch("scripts.search_api.DefaultAzureCredential"),
            patch(
                "scripts.search_api.get_env",
                return_value="https://search.example.com",
            ),
        ):
            api = SearchApiClient(mock_client)

        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 400
        resp.text = "Bad request"

        with pytest.raises(SystemExit):
            api.check_response(resp, "test-resource")


# ---------------------------------------------------------------------------
# get_env
# ---------------------------------------------------------------------------


class TestGetEnv:
    def test_returns_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_VAR_XYZ", "hello")
        assert get_env("TEST_VAR_XYZ") == "hello"

    def test_missing_var_exits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TEST_VAR_MISSING", raising=False)
        with pytest.raises(SystemExit):
            get_env("TEST_VAR_MISSING")


# ---------------------------------------------------------------------------
# Helpers for pipeline function tests
# ---------------------------------------------------------------------------


def _make_api() -> tuple[SearchApiClient, MagicMock]:
    """Create a SearchApiClient backed by a mock httpx.Client."""
    mock_client = MagicMock(spec=httpx.Client)
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.text = "{}"
    mock_client.request.return_value = mock_response

    with (
        patch("scripts.search_api.DefaultAzureCredential") as mock_cred_cls,
        patch(
            "scripts.search_api.get_env",
            return_value="https://search.example.com",
        ),
    ):
        mock_cred = MagicMock()
        mock_token = MagicMock()
        mock_token.token = "t"
        mock_cred.get_token.return_value = mock_token
        mock_cred_cls.return_value = mock_cred
        api = SearchApiClient(mock_client)

    return api, mock_client


def _extract_body(mock_client: MagicMock, call_index: int = 0) -> dict[str, Any]:
    """Extract the JSON body from a mock httpx.Client.request call."""
    c = mock_client.request.call_args_list[call_index]
    return c.kwargs.get("json") or c[1].get("json") or {}


# ---------------------------------------------------------------------------
# Pipeline function tests — index schema (P0 #10, E7)
# ---------------------------------------------------------------------------


class TestCreateIndex:
    def test_index_fields_aligned_with_surf_index(self) -> None:
        """SharePoint index fields should match surf-index naming conventions."""
        api, mock_client = _make_api()
        with patch(
            "scripts.setup_sharepoint_indexer.get_env",
            return_value="https://openai.example.com",
        ):
            _create_index(api, "surf-sharepoint-index")

        body = _extract_body(mock_client)
        field_names = {f["name"] for f in body["fields"]}
        # Core fields that must match surf-index (review item E7)
        assert "content" in field_names, "should use 'content' not 'chunk'"
        assert "content_vector" in field_names, "should use 'content_vector' not 'text_vector'"
        assert "domain" in field_names
        assert "document_type" in field_names
        assert "section_heading" in field_names
        assert "chunk_index" in field_names
        assert "chunk_id" in field_names

    def test_chunk_id_is_key_field(self) -> None:
        """chunk_id must be the key field for the index (P0 #11)."""
        api, mock_client = _make_api()
        with patch(
            "scripts.setup_sharepoint_indexer.get_env",
            return_value="https://openai.example.com",
        ):
            _create_index(api, "surf-sharepoint-index")

        body = _extract_body(mock_client)
        key_fields = [f for f in body["fields"] if f.get("key")]
        assert len(key_fields) == 1
        assert key_fields[0]["name"] == "chunk_id"

    def test_content_vector_dimensions_3072(self) -> None:
        """Embedding vector should be 3072 dimensions (text-embedding-3-large)."""
        api, mock_client = _make_api()
        with patch(
            "scripts.setup_sharepoint_indexer.get_env",
            return_value="https://openai.example.com",
        ):
            _create_index(api, "surf-sharepoint-index")

        body = _extract_body(mock_client)
        vector_field = next(f for f in body["fields"] if f["name"] == "content_vector")
        assert vector_field["dimensions"] == 3072

    def test_vector_profile_name(self) -> None:
        """Vector search profile should use aligned name."""
        api, mock_client = _make_api()
        with patch(
            "scripts.setup_sharepoint_indexer.get_env",
            return_value="https://openai.example.com",
        ):
            _create_index(api, "surf-sharepoint-index")

        body = _extract_body(mock_client)
        vector_field = next(f for f in body["fields"] if f["name"] == "content_vector")
        assert vector_field["vectorSearchProfile"] == "content-vector-profile"
        profiles = body["vectorSearch"]["profiles"]
        assert any(p["name"] == "content-vector-profile" for p in profiles)


# ---------------------------------------------------------------------------
# Pipeline function tests — skillset (P0 #10, E11)
# ---------------------------------------------------------------------------


class TestCreateSkillset:
    def test_chunk_overlap_is_200(self) -> None:
        """Chunk overlap must be 200, not 500 (P0 #10)."""
        api, mock_client = _make_api()
        with patch(
            "scripts.setup_sharepoint_indexer.get_env",
            return_value="https://openai.example.com",
        ):
            _create_skillset(api, "surf-sharepoint-index")

        body = _extract_body(mock_client)
        split_skill = next(s for s in body["skills"] if s.get("name") == "text-split")
        assert split_skill["pageOverlapLength"] == 200

    def test_projection_maps_content_not_chunk(self) -> None:
        """Projection mapping should use 'content' field name, not 'chunk' (E7)."""
        api, mock_client = _make_api()
        with patch(
            "scripts.setup_sharepoint_indexer.get_env",
            return_value="https://openai.example.com",
        ):
            _create_skillset(api, "surf-sharepoint-index")

        body = _extract_body(mock_client)
        selector = body["indexProjections"]["selectors"][0]
        mapping_names = {m["name"] for m in selector["mappings"]}
        assert "content" in mapping_names
        assert "chunk" not in mapping_names
        assert "content_vector" in mapping_names
        assert "text_vector" not in mapping_names

    def test_projection_includes_metadata_fields(self) -> None:
        """Skillset projection should map domain and document_type from blob metadata."""
        api, mock_client = _make_api()
        with patch(
            "scripts.setup_sharepoint_indexer.get_env",
            return_value="https://openai.example.com",
        ):
            _create_skillset(api, "surf-sharepoint-index")

        body = _extract_body(mock_client)
        selector = body["indexProjections"]["selectors"][0]
        mappings_by_name = {m["name"]: m for m in selector["mappings"]}
        assert "domain" in mappings_by_name
        assert mappings_by_name["domain"]["source"] == "/document/sp_domain"
        assert "document_type" in mappings_by_name
        assert mappings_by_name["document_type"]["source"] == "/document/sp_document_type"

    def test_parent_key_field_is_parent_id(self) -> None:
        """Index projection must set parentKeyFieldName to parent_id."""
        api, mock_client = _make_api()
        with patch(
            "scripts.setup_sharepoint_indexer.get_env",
            return_value="https://openai.example.com",
        ):
            _create_skillset(api, "surf-sharepoint-index")

        body = _extract_body(mock_client)
        selector = body["indexProjections"]["selectors"][0]
        assert selector["parentKeyFieldName"] == "parent_id"


# ---------------------------------------------------------------------------
# Pipeline function tests — data source
# ---------------------------------------------------------------------------


class TestCreateDataSource:
    def test_soft_delete_policy_configured(self) -> None:
        """Data source must have NativeBlobSoftDeleteDeletionDetectionPolicy."""
        api, mock_client = _make_api()
        with (
            patch(
                "scripts.setup_sharepoint_indexer.get_env",
                return_value="https://storage.blob.core.windows.net",
            ),
            patch(
                "scripts.setup_sharepoint_indexer._get_subscription_id",
                return_value="sub-1",
            ),
        ):
            _create_data_source(api, "test-index", "sharepoint/")

        body = _extract_body(mock_client)
        policy = body["dataDeletionDetectionPolicy"]
        assert "NativeBlobSoftDeleteDeletionDetectionPolicy" in policy["@odata.type"]


# ---------------------------------------------------------------------------
# Pipeline function tests — indexer
# ---------------------------------------------------------------------------


class TestCreateIndexer:
    def test_indexer_references_correct_resources(self) -> None:
        """Indexer should reference the correct data source, index, and skillset."""
        api, mock_client = _make_api()
        _create_indexer(api, "surf-sharepoint-index")

        body = _extract_body(mock_client)
        assert body["dataSourceName"] == "surf-sharepoint-index-datasource"
        assert body["targetIndexName"] == "surf-sharepoint-index"
        assert body["skillsetName"] == "surf-sharepoint-index-skillset"

    def test_indexer_has_hourly_schedule(self) -> None:
        """Indexer should run on an hourly schedule."""
        api, mock_client = _make_api()
        _create_indexer(api, "surf-sharepoint-index")

        body = _extract_body(mock_client)
        assert body["schedule"]["interval"] == "PT1H"

    def test_indexer_file_extensions_match_sync(self) -> None:
        """Indexed file extensions should cover the same formats as the sync."""
        api, mock_client = _make_api()
        _create_indexer(api, "surf-sharepoint-index")

        body = _extract_body(mock_client)
        extensions = body["parameters"]["configuration"]["indexedFileNameExtensions"]
        for ext in (".pdf", ".docx", ".pptx", ".xlsx", ".html", ".htm", ".csv", ".md"):
            assert ext in extensions


# ---------------------------------------------------------------------------
# Pipeline function tests — teardown
# ---------------------------------------------------------------------------


class TestTeardown:
    def test_teardown_deletes_in_correct_order(self) -> None:
        """Teardown should delete indexer, skillset, datasource, then index."""
        api, mock_client = _make_api()
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 204
        mock_client.request.return_value = mock_response

        _teardown(api, "test-index")

        # Extract the paths from the DELETE calls
        delete_calls = mock_client.request.call_args_list
        assert len(delete_calls) == 4
        # Should be: indexer, skillset, datasource, index (all DELETEs)
        methods = [c[0][0] for c in delete_calls]
        assert all(m == "DELETE" for m in methods)
        # Verify correct resource order
        paths = [
            c[0][1].split("/")[-1] if len(c[0]) > 1 else c.kwargs.get("url", "").split("/")[-1]
            for c in delete_calls
        ]
        assert paths[0] == "test-index-indexer"
        assert paths[1] == "test-index-skillset"
        assert paths[2] == "test-index-datasource"
        assert paths[3] == "test-index"
