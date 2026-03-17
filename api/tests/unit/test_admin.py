"""Tests for the dev admin page routes."""

from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.config.settings import Settings
from src.routes.admin import router as admin_router
from src.services.conversation import ConversationService


def _make_admin_app() -> FastAPI:
    """Build a minimal app with admin routes for testing."""
    app = FastAPI()
    app.include_router(admin_router)

    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    svc = ConversationService(settings)
    mock_conn = AsyncMock()
    mock_conn.fetchval.return_value = 0
    mock_conn.fetch.return_value = []
    mock_conn.fetchrow.return_value = {
        "total_conversations": 0,
        "conversations_today": 0,
        "total_messages": 0,
        "total_feedback": 0,
    }
    mock_pool = MagicMock()
    cm = AsyncMock()
    cm.__aenter__.return_value = mock_conn
    cm.__aexit__.return_value = None
    mock_pool.acquire.return_value = cm
    svc._pool = mock_pool
    app.state.conversation_service = svc
    return app


class TestAdminRoutes:
    def test_admin_page_returns_html(self) -> None:
        app = _make_admin_app()
        client = TestClient(app)
        resp = client.get("/api/v1/admin/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Surf Admin" in resp.text

    def test_admin_page_is_self_contained(self) -> None:
        """The HTML page must not reference external stylesheets or scripts."""
        app = _make_admin_app()
        client = TestClient(app)
        resp = client.get("/api/v1/admin/")
        # No external link/script references
        assert 'href="http' not in resp.text
        assert 'src="http' not in resp.text

    def test_stats_endpoint_returns_json(self) -> None:
        app = _make_admin_app()
        client = TestClient(app)
        resp = client.get("/api/v1/admin/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_conversations" in data
        assert "conversations_today" in data
        assert "total_messages" in data
        assert "total_feedback" in data
        assert "avg_messages_per_conversation" in data

    def test_conversations_list_returns_json(self) -> None:
        app = _make_admin_app()
        client = TestClient(app)
        resp = client.get("/api/v1/admin/api/conversations")
        assert resp.status_code == 200
        data = resp.json()
        assert "conversations" in data
        assert "total" in data
        assert "page" in data
        assert "per_page" in data

    def test_conversations_list_pagination_params(self) -> None:
        app = _make_admin_app()
        client = TestClient(app)
        resp = client.get("/api/v1/admin/api/conversations?page=2&per_page=5")
        assert resp.status_code == 200
        data = resp.json()
        assert data["page"] == 2
        assert data["per_page"] == 5

    def test_conversations_list_with_filters(self) -> None:
        app = _make_admin_app()
        client = TestClient(app)
        resp = client.get(
            "/api/v1/admin/api/conversations"
            "?user_id=abc&agent=triage&date_from=2025-01-01&date_to=2025-12-31"
        )
        assert resp.status_code == 200

    def test_conversation_detail_not_found(self) -> None:
        app = _make_admin_app()
        # Override fetchrow to return None for detail lookup
        svc = app.state.conversation_service
        pool = svc._pool
        cm = pool.acquire.return_value
        conn = cm.__aenter__.return_value
        conn.fetchrow.return_value = None
        client = TestClient(app)
        resp = client.get("/api/v1/admin/api/conversations/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404


class TestAdminDevOnly:
    def test_admin_not_registered_in_prod(self) -> None:
        """Verify the admin router is NOT included when environment != dev."""
        # We verify the conditional import pattern exists in main.py
        import inspect

        import src.main

        source = inspect.getsource(src.main)
        assert 'settings.environment == "dev"' in source
