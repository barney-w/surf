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
def _get_jwks_client() -> jwt.PyJWKClient:
    """Create a cached JWKS client for Entra ID token validation (multi-tenant)."""
    jwks_uri = "https://login.microsoftonline.com/common/discovery/v2.0/keys"
    return jwt.PyJWKClient(jwks_uri, cache_keys=True, lifespan=300)


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
        jwks_client = _get_jwks_client()
        signing_key = jwks_client.get_signing_key_from_jwt(token)

        # Multi-tenant: issuer varies per tenant so we validate the issuer
        # format after decoding rather than passing a fixed list.
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=[settings.entra_client_id, f"api://{settings.entra_client_id}"],
            options={
                "require": ["exp", "iss", "aud", "oid"],
                "verify_iss": False,
            },
        )

        # Verify issuer matches Entra ID pattern (v1 or v2 format)
        issuer = payload.get("iss", "")
        if not (
            issuer.startswith("https://login.microsoftonline.com/")
            or issuer.startswith("https://sts.windows.net/")
        ):
            logger.warning("Token issuer '%s' is not a recognised Entra ID issuer", issuer)
            raise jwt.InvalidIssuerError("Invalid issuer")
    except jwt.ExpiredSignatureError as e:
        logger.warning("Token has expired")
        raise HTTPException(status_code=401, detail="Token has expired") from e
    except jwt.PyJWKClientConnectionError as e:
        logger.error("Failed to fetch JWKS keys: %s", e)
        raise HTTPException(status_code=401, detail="Authentication service unavailable") from e
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
