"""Tests for guest token issuance and validation."""

import datetime
from unittest.mock import MagicMock, patch

import jwt
import pytest
from fastapi import HTTPException

from src.middleware.auth import GUEST_ISSUER, UserContext, get_current_user

GUEST_SECRET = "test-guest-secret-at-least-32-chars-long"


def _mock_settings(auth_enabled: bool = True, guest_secret: str = GUEST_SECRET) -> MagicMock:
    settings = MagicMock()
    settings.auth_enabled = auth_enabled
    settings.entra_tenant_id = "test-tenant"
    settings.entra_client_id = "test-client"
    settings.guest_token_secret = guest_secret
    settings.guest_token_ttl_minutes = 30
    return settings


def _make_guest_token(
    secret: str = GUEST_SECRET,
    guest_id: str = "guest-abc123",
    expired: bool = False,
    issuer: str = GUEST_ISSUER,
) -> str:
    now = datetime.datetime.now(datetime.UTC)
    payload = {
        "sub": guest_id,
        "iss": issuer,
        "iat": now,
        "exp": (
            now - datetime.timedelta(minutes=1) if expired else now + datetime.timedelta(minutes=30)
        ),
        "type": "guest",
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def _mock_request(token: str | None = None) -> MagicMock:
    request = MagicMock()
    if token is not None:
        request.headers.get.return_value = f"Bearer {token}"
    else:
        request.headers.get.return_value = ""
    return request


class TestGuestTokenValidation:
    """Test the auth middleware's handling of guest tokens."""

    @pytest.mark.asyncio
    async def test_valid_guest_token_returns_guest_user(self):
        token = _make_guest_token()
        request = _mock_request(token=token)

        with patch("src.middleware.auth.get_settings", return_value=_mock_settings()):
            user = await get_current_user(request)

        assert isinstance(user, UserContext)
        assert user.user_id == "guest-abc123"
        assert user.name == "Guest"
        assert user.email == ""
        assert user.is_guest is True

    @pytest.mark.asyncio
    async def test_expired_guest_token_returns_401(self):
        token = _make_guest_token(expired=True)
        request = _mock_request(token=token)

        with (
            patch("src.middleware.auth.get_settings", return_value=_mock_settings()),
            pytest.raises(HTTPException) as exc_info,
        ):
            await get_current_user(request)

        assert exc_info.value.status_code == 401
        assert "expired" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_guest_token_wrong_secret_returns_401(self):
        token = _make_guest_token(secret="wrong-secret-that-is-long-enough")
        request = _mock_request(token=token)

        with (
            patch("src.middleware.auth.get_settings", return_value=_mock_settings()),
            pytest.raises(HTTPException) as exc_info,
        ):
            await get_current_user(request)

        assert exc_info.value.status_code == 401
        assert "invalid" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_guest_token_disabled_when_no_secret(self):
        """When guest_token_secret is empty, guest tokens are rejected via Entra path."""
        token = _make_guest_token()
        request = _mock_request(token=token)

        settings = _mock_settings(guest_secret="")
        with (
            patch("src.middleware.auth.get_settings", return_value=settings),
            patch("src.middleware.auth._get_jwks_client") as mock_jwks,
            pytest.raises(HTTPException) as exc_info,
        ):
            # With no guest secret, it falls through to Entra validation which fails
            mock_jwks.return_value.get_signing_key_from_jwt.side_effect = (
                jwt.InvalidTokenError("Not an RSA token")
            )
            await get_current_user(request)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_guest_user_id_is_preserved(self):
        """The guest_id from the token sub claim should become the user_id."""
        token = _make_guest_token(guest_id="guest-xyz789")
        request = _mock_request(token=token)

        with patch("src.middleware.auth.get_settings", return_value=_mock_settings()):
            user = await get_current_user(request)

        assert user.user_id == "guest-xyz789"

    @pytest.mark.asyncio
    async def test_guest_token_with_wrong_issuer_falls_to_entra(self):
        """A token with a non-guest issuer should not be treated as guest."""
        token = _make_guest_token(issuer="https://some-other-issuer.com")
        request = _mock_request(token=token)

        settings = _mock_settings()
        with (
            patch("src.middleware.auth.get_settings", return_value=settings),
            patch("src.middleware.auth._get_jwks_client") as mock_jwks,
            pytest.raises(HTTPException),
        ):
            mock_jwks.return_value.get_signing_key_from_jwt.side_effect = (
                jwt.InvalidTokenError("Not an RSA token")
            )
            await get_current_user(request)


class TestGuestTokenEndpoint:
    """Test the POST /api/v1/auth/guest endpoint."""

    @pytest.mark.asyncio
    async def test_guest_endpoint_returns_token(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from src.routes.guest import router

        app = FastAPI()
        app.include_router(router)

        # slowapi needs state.limiter
        from src.middleware.rate_limit import limiter

        app.state.limiter = limiter

        from slowapi import _rate_limit_exceeded_handler
        from slowapi.errors import RateLimitExceeded

        app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

        with patch("src.routes.guest.get_settings", return_value=_mock_settings()):
            client = TestClient(app)
            resp = client.post("/api/v1/auth/guest")

        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data
        assert "expires_in" in data
        assert "guest_id" in data
        assert data["expires_in"] == 30 * 60
        assert data["guest_id"].startswith("guest-")

        # Verify the token is valid
        decoded = jwt.decode(data["token"], GUEST_SECRET, algorithms=["HS256"], issuer=GUEST_ISSUER)
        assert decoded["sub"] == data["guest_id"]
        assert decoded["iss"] == GUEST_ISSUER
        assert decoded["type"] == "guest"

    @pytest.mark.asyncio
    async def test_guest_endpoint_disabled_without_secret(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from src.routes.guest import router

        app = FastAPI()
        app.include_router(router)

        from src.middleware.rate_limit import limiter

        app.state.limiter = limiter

        from slowapi import _rate_limit_exceeded_handler
        from slowapi.errors import RateLimitExceeded

        app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

        settings = _mock_settings(guest_secret="")
        with patch("src.routes.guest.get_settings", return_value=settings):
            client = TestClient(app)
            resp = client.post("/api/v1/auth/guest")

        assert resp.status_code == 403
        assert "not enabled" in resp.json()["detail"].lower()


class TestGuestTokenRoundTrip:
    """End-to-end: issue a guest token then use it to authenticate."""

    @pytest.mark.asyncio
    async def test_issued_token_passes_auth_middleware(self):
        """A token from the guest endpoint should be accepted by get_current_user."""
        now = datetime.datetime.now(datetime.UTC)
        guest_id = "guest-roundtrip"
        payload = {
            "sub": guest_id,
            "iss": GUEST_ISSUER,
            "iat": now,
            "exp": now + datetime.timedelta(minutes=30),
            "type": "guest",
        }
        token = jwt.encode(payload, GUEST_SECRET, algorithm="HS256")
        request = _mock_request(token=token)

        with patch("src.middleware.auth.get_settings", return_value=_mock_settings()):
            user = await get_current_user(request)

        assert user.user_id == guest_id
        assert user.is_guest is True
        assert user.name == "Guest"
