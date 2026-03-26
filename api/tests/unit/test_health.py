"""Tests for the health check endpoint."""

from fastapi.testclient import TestClient

from src.main import app

client = TestClient(app)


class TestShallowHealthCheck:
    def test_shallow_health_returns_healthy(self):
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "checks" not in data
        assert "features" in data
        assert isinstance(data["features"]["conversation_history"], bool)

    def test_deep_false_returns_healthy(self):
        response = client.get("/api/v1/health?deep=false")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"


class TestDeepHealthCheck:
    def test_deep_health_returns_checks_structure(self):
        response = client.get("/api/v1/health?deep=true")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert data["status"] in ("healthy", "degraded")
