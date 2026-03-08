"""Contract tests verifying API response schemas match expected shapes."""

from fastapi.testclient import TestClient

from src.main import app

client = TestClient(app)


class TestHealthContract:
    def test_shallow_health_response_shape(self):
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert data["status"] == "healthy"

    def test_deep_health_response_shape(self):
        response = client.get("/api/v1/health?deep=true")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert data["status"] in ("healthy", "degraded")
        assert "checks" in data


class TestErrorContract:
    """All errors should follow the structured error format."""

    def test_422_error_on_missing_body(self):
        response = client.post("/api/v1/chat")
        assert response.status_code == 422
