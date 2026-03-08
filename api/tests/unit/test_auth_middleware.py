"""Tests for the auth middleware."""

import datetime
from unittest.mock import MagicMock, patch

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from src.middleware.auth import UserContext, get_current_user

TENANT_ID = "test-tenant-id"
CLIENT_ID = "test-client-id"


def _make_rsa_key() -> rsa.RSAPrivateKey:
    """Generate a throwaway RSA private key for signing test tokens."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _build_token(
    private_key: rsa.RSAPrivateKey,
    claims: dict[str, object] | None = None,
    expired: bool = False,
) -> str:
    """Create a signed JWT with the given claims."""
    now = datetime.datetime.now(datetime.UTC)
    payload: dict[str, object] = {
        "oid": "user-oid-123",
        "name": "Jane Doe",
        "preferred_username": "jane@example.com",
        "department": "IT",
        "jobTitle": "Engineer",
        "iss": f"https://login.microsoftonline.com/{TENANT_ID}/v2.0",
        "aud": CLIENT_ID,
        "iat": now - datetime.timedelta(minutes=5),
        "exp": (
            now - datetime.timedelta(minutes=1) if expired else now + datetime.timedelta(hours=1)
        ),
    }
    if claims:
        payload.update(claims)
    return jwt.encode(payload, private_key, algorithm="RS256")


def _mock_settings(auth_enabled: bool = True) -> MagicMock:
    settings = MagicMock()
    settings.auth_enabled = auth_enabled
    settings.entra_tenant_id = TENANT_ID
    settings.entra_client_id = CLIENT_ID
    return settings


def _mock_request(token: str | None = None) -> MagicMock:
    request = MagicMock()
    if token is not None:
        request.headers.get.return_value = f"Bearer {token}"
    else:
        request.headers.get.return_value = ""
    return request


class TestAuthDisabled:
    """When auth_enabled=False the middleware should return a dev user."""

    @pytest.mark.asyncio
    async def test_returns_dev_user_without_token(self):
        request = _mock_request()
        with patch(
            "src.middleware.auth.get_settings",
            return_value=_mock_settings(auth_enabled=False),
        ):
            user = await get_current_user(request)

        assert isinstance(user, UserContext)
        assert user.user_id == "dev-user"
        assert user.name == "Dev User"
        assert user.email == "dev@example.com"


class TestAuthEnabled:
    """When auth_enabled=True the middleware must validate a JWT."""

    @pytest.mark.asyncio
    async def test_missing_token_returns_401(self):
        request = _mock_request(token=None)
        with (
            patch(
                "src.middleware.auth.get_settings",
                return_value=_mock_settings(auth_enabled=True),
            ),
            pytest.raises(HTTPException) as exc_info,
        ):
            await get_current_user(request)

        assert exc_info.value.status_code == 401
        assert "Missing or invalid Authorization header" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_valid_token_extracts_user_context(self):
        private_key = _make_rsa_key()
        token = _build_token(private_key)

        # Build a mock signing key whose `.key` is the RSA public key
        mock_signing_key = MagicMock()
        mock_signing_key.key = private_key.public_key()

        mock_jwks_client = MagicMock()
        mock_jwks_client.get_signing_key_from_jwt.return_value = mock_signing_key

        request = _mock_request(token=token)
        with (
            patch(
                "src.middleware.auth.get_settings",
                return_value=_mock_settings(auth_enabled=True),
            ),
            patch("src.middleware.auth._get_jwks_client", return_value=mock_jwks_client),
        ):
            user = await get_current_user(request)

        assert user.user_id == "user-oid-123"
        assert user.name == "Jane Doe"
        assert user.email == "jane@example.com"
        assert user.department == "IT"
        assert user.job_title == "Engineer"

    @pytest.mark.asyncio
    async def test_expired_token_returns_401(self):
        private_key = _make_rsa_key()
        token = _build_token(private_key, expired=True)

        mock_signing_key = MagicMock()
        mock_signing_key.key = private_key.public_key()

        mock_jwks_client = MagicMock()
        mock_jwks_client.get_signing_key_from_jwt.return_value = mock_signing_key

        request = _mock_request(token=token)
        with (
            patch(
                "src.middleware.auth.get_settings",
                return_value=_mock_settings(auth_enabled=True),
            ),
            patch("src.middleware.auth._get_jwks_client", return_value=mock_jwks_client),
            pytest.raises(HTTPException) as exc_info,
        ):
            await get_current_user(request)

        assert exc_info.value.status_code == 401
        assert "expired" in exc_info.value.detail.lower()
