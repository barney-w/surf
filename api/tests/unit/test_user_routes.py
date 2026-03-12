"""Tests for user profile routes (/api/v1/me and /api/v1/me/photo)."""

import importlib
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Import helpers
#
# src.routes.__init__ imports chat.py which cascades into anthropic,
# agent_framework, opentelemetry, etc.  src.routes.user itself only needs
# FastAPI and src.middleware.auth, so we load it directly via importlib to
# skip the package __init__.
#
# Similarly, src.middleware.__init__ re-exports telemetry which triggers an
# opentelemetry version conflict.  We stub that single sub-module then
# import auth directly.
# ---------------------------------------------------------------------------

# Stub telemetry to prevent opentelemetry import chain
if "src.middleware.telemetry" not in sys.modules:
    _telemetry_stub = types.ModuleType("src.middleware.telemetry")
    _telemetry_stub.setup_telemetry = lambda *a, **kw: None  # type: ignore[attr-defined]
    sys.modules["src.middleware.telemetry"] = _telemetry_stub

from src.middleware.auth import UserContext  # noqa: E402

# Load src.routes.user without triggering src.routes.__init__ (which imports chat)
_user_mod_path = Path(__file__).resolve().parents[2] / "src" / "routes" / "user.py"
_spec = importlib.util.spec_from_file_location("src.routes.user", _user_mod_path)
assert _spec and _spec.loader
_user_mod = importlib.util.module_from_spec(_spec)
sys.modules["src.routes.user"] = _user_mod
_spec.loader.exec_module(_user_mod)

_extract_bearer_token = _user_mod._extract_bearer_token  # type: ignore[attr-defined]
router = _user_mod.router  # type: ignore[attr-defined]


def _make_app(graph_service: object | None = None) -> FastAPI:
    """Create a minimal FastAPI app with user routes and optional graph service."""
    app = FastAPI()
    app.include_router(router)

    # Attach graph_service to app.state (mirrors main.py lifespan)
    if graph_service is not None:
        app.state.graph_service = graph_service

    # slowapi requires a limiter on app.state
    from slowapi import Limiter
    from slowapi.util import get_remote_address

    app.state.limiter = Limiter(key_func=get_remote_address)

    return app


_DEV_USER = UserContext(
    user_id="test-user-id",
    name="Jane Smith",
    email="jane@example.com",
    department="Engineering",
    job_title="Developer",
)


@pytest.fixture
def dev_user() -> UserContext:
    return _DEV_USER


class TestExtractBearerToken:
    """Tests for the _extract_bearer_token helper."""

    def test_returns_token_when_valid(self) -> None:
        request = MagicMock()
        request.headers.get.return_value = "Bearer abc123"
        assert _extract_bearer_token(request) == "abc123"

    def test_returns_none_when_missing_header(self) -> None:
        request = MagicMock()
        request.headers.get.return_value = ""
        assert _extract_bearer_token(request) is None

    def test_returns_none_when_not_bearer_scheme(self) -> None:
        request = MagicMock()
        request.headers.get.return_value = "Basic dXNlcjpwYXNz"
        assert _extract_bearer_token(request) is None

    def test_returns_none_when_bearer_but_empty_token(self) -> None:
        request = MagicMock()
        request.headers.get.return_value = "Bearer    "
        assert _extract_bearer_token(request) is None


class TestGetMe:
    """Tests for GET /api/v1/me."""

    @patch("src.routes.user.get_current_user", new_callable=AsyncMock)
    def test_returns_jwt_profile_when_no_graph_service(
        self, mock_auth: AsyncMock, dev_user: UserContext
    ) -> None:
        """When no Graph service is on app.state, return JWT claims only."""
        mock_auth.return_value = dev_user
        app = _make_app(graph_service=None)
        client = TestClient(app)

        resp = client.get("/api/v1/me")

        assert resp.status_code == 200
        body = resp.json()
        assert body["displayName"] == "Jane Smith"
        assert body["givenName"] == "Jane"
        assert body["mail"] == "jane@example.com"
        assert body["department"] == "Engineering"
        assert body["jobTitle"] == "Developer"
        assert body["groups"] == []

    @patch("src.routes.user.get_current_user", new_callable=AsyncMock)
    def test_enriches_with_graph_data_when_available(
        self, mock_auth: AsyncMock, dev_user: UserContext
    ) -> None:
        """When Graph service succeeds, profile is enriched with Graph data."""
        mock_auth.return_value = dev_user

        graph = AsyncMock()
        graph.available = True
        graph.get_graph_token.return_value = "graph-token-xyz"

        # Simulate a Graph UserProfile
        profile_obj = MagicMock()
        profile_obj.display_name = "Jane A. Smith"
        profile_obj.given_name = "Jane"
        profile_obj.department = "Platform Engineering"
        profile_obj.job_title = "Senior Developer"
        profile_obj.office_location = "Level 3"
        profile_obj.mail = "jane.smith@corp.com"
        graph.get_user_profile.return_value = profile_obj
        graph.get_user_groups.return_value = ["Engineering", "Admins"]

        app = _make_app(graph_service=graph)
        client = TestClient(app)

        resp = client.get("/api/v1/me", headers={"Authorization": "Bearer my-jwt"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["displayName"] == "Jane A. Smith"
        assert body["department"] == "Platform Engineering"
        assert body["officeLocation"] == "Level 3"
        assert body["groups"] == ["Engineering", "Admins"]

    @patch("src.routes.user.get_current_user", new_callable=AsyncMock)
    def test_handles_graph_failure_gracefully(
        self, mock_auth: AsyncMock, dev_user: UserContext
    ) -> None:
        """When Graph token acquisition fails, fall back to JWT claims."""
        mock_auth.return_value = dev_user

        graph = AsyncMock()
        graph.available = True
        graph.get_graph_token.return_value = None  # OBO failed

        app = _make_app(graph_service=graph)
        client = TestClient(app)

        resp = client.get("/api/v1/me", headers={"Authorization": "Bearer my-jwt"})

        assert resp.status_code == 200
        body = resp.json()
        # Falls back to JWT claims
        assert body["displayName"] == "Jane Smith"
        assert body["groups"] == []

    @patch("src.routes.user.get_current_user", new_callable=AsyncMock)
    def test_graph_profile_none_falls_back_to_jwt(
        self, mock_auth: AsyncMock, dev_user: UserContext
    ) -> None:
        """When Graph profile returns None, JWT claims are preserved."""
        mock_auth.return_value = dev_user

        graph = AsyncMock()
        graph.available = True
        graph.get_graph_token.return_value = "graph-token"
        graph.get_user_profile.return_value = None
        graph.get_user_groups.return_value = []

        app = _make_app(graph_service=graph)
        client = TestClient(app)

        resp = client.get("/api/v1/me", headers={"Authorization": "Bearer my-jwt"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["displayName"] == "Jane Smith"
        assert body["mail"] == "jane@example.com"


class TestGetMePhoto:
    """Tests for GET /api/v1/me/photo."""

    @patch("src.routes.user.get_current_user", new_callable=AsyncMock)
    def test_returns_404_when_no_graph_service(
        self, mock_auth: AsyncMock, dev_user: UserContext
    ) -> None:
        mock_auth.return_value = dev_user
        app = _make_app(graph_service=None)
        client = TestClient(app)

        resp = client.get("/api/v1/me/photo")

        assert resp.status_code == 404

    @patch("src.routes.user.get_current_user", new_callable=AsyncMock)
    def test_returns_404_when_no_bearer_token(
        self, mock_auth: AsyncMock, dev_user: UserContext
    ) -> None:
        graph = AsyncMock()
        graph.available = True
        mock_auth.return_value = dev_user

        app = _make_app(graph_service=graph)
        client = TestClient(app)

        # No Authorization header
        resp = client.get("/api/v1/me/photo")

        assert resp.status_code == 404

    @patch("src.routes.user.get_current_user", new_callable=AsyncMock)
    def test_returns_image_bytes_on_success(
        self, mock_auth: AsyncMock, dev_user: UserContext
    ) -> None:
        mock_auth.return_value = dev_user

        fake_photo = b"\xff\xd8\xff\xe0JFIF-fake-photo-bytes"
        graph = AsyncMock()
        graph.available = True
        graph.get_graph_token.return_value = "graph-token"
        graph.get_user_photo.return_value = fake_photo

        app = _make_app(graph_service=graph)
        client = TestClient(app)

        resp = client.get(
            "/api/v1/me/photo",
            headers={"Authorization": "Bearer my-jwt"},
        )

        assert resp.status_code == 200
        assert resp.content == fake_photo
        assert resp.headers["content-type"] == "image/jpeg"
        assert "max-age=3600" in resp.headers.get("cache-control", "")

    @patch("src.routes.user.get_current_user", new_callable=AsyncMock)
    def test_returns_404_when_photo_not_set(
        self, mock_auth: AsyncMock, dev_user: UserContext
    ) -> None:
        mock_auth.return_value = dev_user

        graph = AsyncMock()
        graph.available = True
        graph.get_graph_token.return_value = "graph-token"
        graph.get_user_photo.return_value = None  # No photo

        app = _make_app(graph_service=graph)
        client = TestClient(app)

        resp = client.get(
            "/api/v1/me/photo",
            headers={"Authorization": "Bearer my-jwt"},
        )

        assert resp.status_code == 404

    @patch("src.routes.user.get_current_user", new_callable=AsyncMock)
    def test_returns_404_when_graph_token_fails(
        self, mock_auth: AsyncMock, dev_user: UserContext
    ) -> None:
        mock_auth.return_value = dev_user

        graph = AsyncMock()
        graph.available = True
        graph.get_graph_token.return_value = None

        app = _make_app(graph_service=graph)
        client = TestClient(app)

        resp = client.get(
            "/api/v1/me/photo",
            headers={"Authorization": "Bearer my-jwt"},
        )

        assert resp.status_code == 404
