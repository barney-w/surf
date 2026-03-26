"""Tests for GraphService — Microsoft Graph API OBO client."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.services.graph import GraphService, UserProfile


@pytest.fixture
def mock_msal_app() -> MagicMock:
    """Mock msal.ConfidentialClientApplication."""
    return MagicMock()


@pytest.fixture
def mock_http() -> AsyncMock:
    """Mock httpx.AsyncClient used by GraphService."""
    return AsyncMock(spec=httpx.AsyncClient)


@pytest.fixture
def service(mock_msal_app: MagicMock, mock_http: AsyncMock) -> GraphService:
    """Create a GraphService with mocked internals (no real Entra credentials)."""
    with patch("src.services.graph.get_settings") as mock_settings:
        settings = MagicMock()
        settings.entra_client_id = "test-client-id"
        settings.entra_client_secret = "test-secret"
        settings.entra_tenant_id = "test-tenant-id"
        mock_settings.return_value = settings

        msal_path = "src.services.graph.msal.ConfidentialClientApplication"
        with patch(msal_path, return_value=mock_msal_app):
            svc = GraphService()

    # Replace the real httpx client with our mock
    svc._http = mock_http
    return svc


@pytest.fixture
def unconfigured_service() -> GraphService:
    """Create a GraphService without Entra credentials (OBO disabled)."""
    with patch("src.services.graph.get_settings") as mock_settings:
        settings = MagicMock()
        settings.entra_client_id = None
        settings.entra_client_secret = None
        settings.entra_tenant_id = None
        mock_settings.return_value = settings

        svc = GraphService()
    return svc


class TestAvailableProperty:
    def test_available_when_configured(self, service: GraphService) -> None:
        assert service.available is True

    def test_not_available_when_unconfigured(self, unconfigured_service: GraphService) -> None:
        assert unconfigured_service.available is False


class TestGetGraphToken:
    @pytest.mark.asyncio
    async def test_returns_token_on_successful_obo(
        self, service: GraphService, mock_msal_app: MagicMock
    ) -> None:
        mock_msal_app.acquire_token_on_behalf_of.return_value = {
            "access_token": "graph-access-token-123",
        }

        token = await service.get_graph_token("user-assertion-jwt")

        assert token == "graph-access-token-123"
        mock_msal_app.acquire_token_on_behalf_of.assert_called_once_with(
            user_assertion="user-assertion-jwt",
            scopes=["User.Read"],
        )

    @pytest.mark.asyncio
    async def test_returns_none_when_msal_fails(
        self, service: GraphService, mock_msal_app: MagicMock
    ) -> None:
        mock_msal_app.acquire_token_on_behalf_of.return_value = {
            "error": "invalid_grant",
            "error_description": "Token expired",
        }

        token = await service.get_graph_token("expired-assertion")

        assert token is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_msal_app(self, unconfigured_service: GraphService) -> None:
        token = await unconfigured_service.get_graph_token("any-assertion")

        assert token is None


class TestGetUserProfile:
    @pytest.mark.asyncio
    async def test_parses_graph_response_correctly(
        self, service: GraphService, mock_http: AsyncMock
    ) -> None:
        response = MagicMock()
        response.status_code = 200
        response.raise_for_status = MagicMock()
        response.json.return_value = {
            "displayName": "Jane Smith",
            "givenName": "Jane",
            "department": "Engineering",
            "jobTitle": "Developer",
            "officeLocation": "Level 5",
            "mail": "jane@example.com",
        }
        mock_http.get.return_value = response

        profile = await service.get_user_profile("graph-token")

        assert isinstance(profile, UserProfile)
        assert profile.display_name == "Jane Smith"
        assert profile.given_name == "Jane"
        assert profile.department == "Engineering"
        assert profile.job_title == "Developer"
        assert profile.office_location == "Level 5"
        assert profile.mail == "jane@example.com"

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(
        self, service: GraphService, mock_http: AsyncMock
    ) -> None:
        response = MagicMock()
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "403 Forbidden",
            request=MagicMock(),
            response=MagicMock(status_code=403),
        )
        mock_http.get.return_value = response

        profile = await service.get_user_profile("graph-token")

        assert profile is None

    @pytest.mark.asyncio
    async def test_returns_none_on_network_timeout(
        self, service: GraphService, mock_http: AsyncMock
    ) -> None:
        mock_http.get.side_effect = httpx.TimeoutException("Connection timed out")

        profile = await service.get_user_profile("graph-token")

        assert profile is None


class TestGetUserPhoto:
    @pytest.mark.asyncio
    async def test_returns_bytes_on_success(
        self, service: GraphService, mock_http: AsyncMock
    ) -> None:
        fake_jpeg = b"\xff\xd8\xff\xe0JFIF-photo-data"
        response = MagicMock()
        response.status_code = 200
        response.raise_for_status = MagicMock()
        response.content = fake_jpeg
        mock_http.get.return_value = response

        photo = await service.get_user_photo("graph-token")

        assert photo == fake_jpeg

    @pytest.mark.asyncio
    async def test_returns_none_on_404(self, service: GraphService, mock_http: AsyncMock) -> None:
        response = MagicMock()
        response.status_code = 404
        mock_http.get.return_value = response

        photo = await service.get_user_photo("graph-token")

        assert photo is None

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(
        self, service: GraphService, mock_http: AsyncMock
    ) -> None:
        mock_http.get.side_effect = httpx.ConnectError("Connection refused")

        photo = await service.get_user_photo("graph-token")

        assert photo is None


class TestGetAppToken:
    @pytest.mark.asyncio
    async def test_returns_token_on_success(
        self, service: GraphService, mock_msal_app: MagicMock
    ) -> None:
        mock_msal_app.acquire_token_for_client.return_value = {
            "access_token": "app-token-123",
        }

        token = await service.get_app_token()

        assert token == "app-token-123"
        mock_msal_app.acquire_token_for_client.assert_called_once_with(
            scopes=["https://graph.microsoft.com/.default"],
        )

    @pytest.mark.asyncio
    async def test_returns_none_on_failure(
        self, service: GraphService, mock_msal_app: MagicMock
    ) -> None:
        mock_msal_app.acquire_token_for_client.return_value = {
            "error": "unauthorized_client",
            "error_description": "Not configured",
        }

        token = await service.get_app_token()

        assert token is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_msal_app(self, unconfigured_service: GraphService) -> None:
        token = await unconfigured_service.get_app_token()

        assert token is None


class TestGetUserGroups:
    @pytest.mark.asyncio
    async def test_returns_list_of_group_names(
        self, service: GraphService, mock_msal_app: MagicMock, mock_http: AsyncMock
    ) -> None:
        mock_msal_app.acquire_token_for_client.return_value = {
            "access_token": "app-token",
        }
        response = MagicMock()
        response.status_code = 200
        response.raise_for_status = MagicMock()
        response.json.return_value = {
            "value": [
                {"displayName": "Engineering", "id": "g1"},
                {"displayName": "Admins", "id": "g2"},
                {"id": "g3"},  # No displayName — should be filtered out
            ]
        }
        mock_http.get.return_value = response

        groups = await service.get_user_groups("user-oid-123")

        assert groups == ["Engineering", "Admins"]
        # Verify it calls /users/{oid}/memberOf, not /me/memberOf
        call_url = mock_http.get.call_args[0][0]
        assert "/users/user-oid-123/memberOf" in call_url

    @pytest.mark.asyncio
    async def test_returns_empty_list_on_http_failure(
        self, service: GraphService, mock_msal_app: MagicMock, mock_http: AsyncMock
    ) -> None:
        mock_msal_app.acquire_token_for_client.return_value = {
            "access_token": "app-token",
        }
        mock_http.get.side_effect = httpx.HTTPError("Server error")

        groups = await service.get_user_groups("user-oid-123")

        assert groups == []

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_app_token(
        self, service: GraphService, mock_msal_app: MagicMock
    ) -> None:
        mock_msal_app.acquire_token_for_client.return_value = {
            "error": "unauthorized_client",
        }

        groups = await service.get_user_groups("user-oid-123")

        assert groups == []
