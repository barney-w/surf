import logging
from dataclasses import dataclass
from functools import lru_cache

import jwt
from fastapi import HTTPException, Request

from src.config.settings import get_settings

logger = logging.getLogger(__name__)


@dataclass
class UserContext:
    user_id: str  # OID from token
    name: str
    email: str
    department: str | None = None
    job_title: str | None = None


@lru_cache(maxsize=1)
def _get_jwks_client(jwks_uri: str) -> jwt.PyJWKClient:
    """Create a cached JWKS client for Entra ID token validation."""
    return jwt.PyJWKClient(jwks_uri, cache_keys=True, lifespan=3600)


def _get_jwks_uri(tenant_id: str) -> str:
    """Build the JWKS URI for an Entra ID tenant."""
    return f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"


async def get_current_user(request: Request) -> UserContext:
    """Extract and validate the current user from the request.

    When auth is disabled (dev mode), returns a static dev user.
    When auth is enabled, validates the JWT token from Entra ID
    and extracts user claims.
    """
    settings = get_settings()

    if not settings.auth_enabled:
        return UserContext(
            user_id="dev-user",
            name="Dev User",
            email="dev@example.com",
            department="Development",
            job_title="Developer",
        )

    # Extract Bearer token from Authorization header
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        logger.warning("Missing or invalid Authorization header")
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    # Validate JWT
    try:
        jwks_uri = _get_jwks_uri(settings.entra_tenant_id)
        jwks_client = _get_jwks_client(jwks_uri)
        signing_key = jwks_client.get_signing_key_from_jwt(token)

        issuer = f"https://login.microsoftonline.com/{settings.entra_tenant_id}/v2.0"

        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.entra_client_id,
            issuer=issuer,
            options={"require": ["exp", "iss", "aud", "oid"]},
        )
    except jwt.ExpiredSignatureError as e:
        logger.warning("Token has expired")
        raise HTTPException(status_code=401, detail="Token has expired") from e
    except jwt.InvalidTokenError as e:
        logger.warning("Invalid token: %s", e)
        raise HTTPException(status_code=401, detail="Invalid token") from e

    # Extract claims
    return UserContext(
        user_id=payload.get("oid", ""),
        name=payload.get("name", ""),
        email=payload.get("preferred_username", ""),
        department=payload.get("department"),
        job_title=payload.get("jobTitle"),
    )
