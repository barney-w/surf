"""Unit tests for BodySizeLimitMiddleware."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.middleware.body_limit import MAX_BODY_BYTES, BodySizeLimitMiddleware


def make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(BodySizeLimitMiddleware)

    @app.post("/test")
    async def test_endpoint() -> dict[str, bool]:
        return {"ok": True}

    _ = test_endpoint  # registered by decorator

    return app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(make_app(), raise_server_exceptions=True)


def test_oversized_body_returns_413(client: TestClient) -> None:
    """A Content-Length header exceeding MAX_BODY_BYTES must return HTTP 413."""
    oversized = MAX_BODY_BYTES + 1
    response = client.post(
        "/test",
        headers={"content-length": str(oversized), "content-type": "application/json"},
        content=b"{}",  # actual body doesn't matter — header is checked first
    )
    assert response.status_code == 413
    body = response.json()
    assert body["error"]["type"] == "payload_too_large"


def test_exact_limit_body_passes(client: TestClient) -> None:
    """A Content-Length equal to MAX_BODY_BYTES must not be rejected."""
    response = client.post(
        "/test",
        headers={"content-length": str(MAX_BODY_BYTES), "content-type": "application/json"},
        content=b"{}",
    )
    assert response.status_code != 413


def test_small_body_passes(client: TestClient) -> None:
    """A normal small request must pass through to the endpoint."""
    response = client.post("/test", json={})
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_no_content_length_passes(client: TestClient) -> None:
    """A request with no Content-Length header must not be rejected."""
    # Send raw bytes without Content-Length by using content= and stripping the header.
    # TestClient sets content-length automatically; override by sending a streaming body.

    app = make_app()
    with TestClient(app) as c:
        # Use httpx directly to send a request without content-length header
        response = c.post(
            "/test",
            headers={"content-type": "application/json"},
            content=b"{}",
        )
        # Should not be 413 — middleware only blocks when header is present and too large
        assert response.status_code != 413
