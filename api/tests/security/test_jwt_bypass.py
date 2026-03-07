"""Security tests for JWT validation edge cases.

These tests call get_current_user() directly with auth_enabled=True and verify
that malformed, missing, or algorithmically-incorrect tokens are rejected with
HTTP 401.
"""

import datetime
from unittest.mock import MagicMock, patch

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from src.middleware.auth import get_current_user

TENANT_ID = "test-tenant-id"
CLIENT_ID = "test-client-id"


def _make_rsa_key() -> rsa.RSAPrivateKey:
    """Generate a throwaway RSA private key for signing test tokens."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _build_rs256_token(private_key: rsa.RSAPrivateKey, claims: dict | None = None) -> str:
    """Create a valid RS256-signed JWT."""
    now = datetime.datetime.now(datetime.UTC)
    payload = {
        "oid": "user-oid-123",
        "name": "Jane Doe",
        "preferred_username": "jane@example.com",
        "iss": f"https://login.microsoftonline.com/{TENANT_ID}/v2.0",
        "aud": CLIENT_ID,
        "iat": now - datetime.timedelta(minutes=5),
        "exp": now + datetime.timedelta(hours=1),
    }
    if claims:
        payload.update(claims)
    return jwt.encode(payload, private_key, algorithm="RS256")


def _mock_settings() -> MagicMock:
    settings = MagicMock()
    settings.auth_enabled = True
    settings.entra_tenant_id = TENANT_ID
    settings.entra_client_id = CLIENT_ID
    return settings


def _mock_request(authorization: str | None = None) -> MagicMock:
    """Build a mock request with the given Authorization header value."""
    request = MagicMock()
    request.headers.get.return_value = authorization if authorization is not None else ""
    return request


def _mock_jwks_client(private_key: rsa.RSAPrivateKey) -> MagicMock:
    """Build a JWKS client mock that resolves to the given key's public key."""
    mock_signing_key = MagicMock()
    mock_signing_key.key = private_key.public_key()
    mock_jwks = MagicMock()
    mock_jwks.get_signing_key_from_jwt.return_value = mock_signing_key
    return mock_jwks


class TestJwtBypass:
    """JWT validation must reject all bypass attempts with HTTP 401."""

    @pytest.mark.asyncio
    async def test_no_auth_header_returns_401(self):
        """A request with no Authorization header must be rejected."""
        request = _mock_request(authorization="")
        with (
            patch("src.middleware.auth.get_settings", return_value=_mock_settings()),
            pytest.raises(HTTPException) as exc_info,
        ):
            await get_current_user(request)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_empty_bearer_token_returns_401(self):
        """Authorization: Bearer  (empty token after prefix) must be rejected."""
        request = _mock_request(authorization="Bearer ")
        with (
            patch("src.middleware.auth.get_settings", return_value=_mock_settings()),
            pytest.raises(HTTPException) as exc_info,
        ):
            await get_current_user(request)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_malformed_jwt_returns_401(self):
        """A token that is not a valid JWT structure must be rejected."""
        request = _mock_request(authorization="Bearer not.a.jwt")
        private_key = _make_rsa_key()
        mock_jwks = _mock_jwks_client(private_key)
        # JWKS client will raise when asked to get a signing key from garbage input
        mock_jwks.get_signing_key_from_jwt.side_effect = jwt.exceptions.DecodeError(
            "Not a valid JWT"
        )

        with (
            patch("src.middleware.auth.get_settings", return_value=_mock_settings()),
            patch("src.middleware.auth._get_jwks_client", return_value=mock_jwks),
            pytest.raises(HTTPException) as exc_info,
        ):
            await get_current_user(request)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_wrong_algorithm_returns_401(self):
        """A token signed with HS256 (symmetric) instead of RS256 must be rejected.

        The auth middleware only accepts RS256. An HS256 token should fail
        because jwt.decode() enforces algorithms=["RS256"].
        """
        secret = "supersecret"
        now = datetime.datetime.now(datetime.UTC)
        hs256_payload = {
            "oid": "attacker",
            "name": "Bad Actor",
            "preferred_username": "bad@evil.com",
            "iss": f"https://login.microsoftonline.com/{TENANT_ID}/v2.0",
            "aud": CLIENT_ID,
            "iat": now - datetime.timedelta(minutes=1),
            "exp": now + datetime.timedelta(hours=1),
        }
        hs256_token = jwt.encode(hs256_payload, secret, algorithm="HS256")

        request = _mock_request(authorization=f"Bearer {hs256_token}")

        # The JWKS client mock returns a public key — jwt.decode will then reject
        # the HS256 token because the algorithm doesn't match RS256.
        private_key = _make_rsa_key()
        mock_signing_key = MagicMock()
        mock_signing_key.key = private_key.public_key()
        mock_jwks = MagicMock()
        mock_jwks.get_signing_key_from_jwt.return_value = mock_signing_key

        with (
            patch("src.middleware.auth.get_settings", return_value=_mock_settings()),
            patch("src.middleware.auth._get_jwks_client", return_value=mock_jwks),
            pytest.raises(HTTPException) as exc_info,
        ):
            await get_current_user(request)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_required_claims_returns_401(self):
        """A token missing the required 'oid' claim must be rejected.

        The auth middleware requires ["exp", "iss", "aud", "oid"] via
        options={"require": [...]}.
        """
        private_key = _make_rsa_key()
        now = datetime.datetime.now(datetime.UTC)
        # Build a token without the 'oid' claim
        payload_without_oid = {
            "name": "No OID User",
            "preferred_username": "nooid@example.com",
            "iss": f"https://login.microsoftonline.com/{TENANT_ID}/v2.0",
            "aud": CLIENT_ID,
            "iat": now - datetime.timedelta(minutes=1),
            "exp": now + datetime.timedelta(hours=1),
        }
        token = jwt.encode(payload_without_oid, private_key, algorithm="RS256")

        mock_jwks = _mock_jwks_client(private_key)
        request = _mock_request(authorization=f"Bearer {token}")

        with (
            patch("src.middleware.auth.get_settings", return_value=_mock_settings()),
            patch("src.middleware.auth._get_jwks_client", return_value=mock_jwks),
            pytest.raises(HTTPException) as exc_info,
        ):
            await get_current_user(request)

        assert exc_info.value.status_code == 401
