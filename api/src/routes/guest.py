"""Guest token endpoint — issues short-lived anonymous access tokens."""

import datetime
import logging
import uuid

import jwt
from fastapi import APIRouter, HTTPException, Request

from src.config.settings import get_settings
from src.middleware.auth import GUEST_ISSUER
from src.middleware.rate_limit import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/guest")
@limiter.limit("5/minute")  # pyright: ignore[reportUnknownMemberType,reportUntypedFunctionDecorator]
async def create_guest_token(request: Request) -> dict[str, object]:
    """Issue a short-lived anonymous JWT for guest access.

    The token is signed with a server-side HMAC secret and carries a
    random subject ID. Guest tokens grant limited access — conversation
    history is not persisted and rate limits are stricter.
    """
    settings = get_settings()

    if not settings.guest_token_secret:
        raise HTTPException(
            status_code=403,
            detail="Guest access is not enabled",
        )

    now = datetime.datetime.now(datetime.UTC)
    guest_id = f"guest-{uuid.uuid4().hex[:12]}"

    payload = {
        "sub": guest_id,
        "iss": GUEST_ISSUER,
        "iat": now,
        "exp": now + datetime.timedelta(minutes=settings.guest_token_ttl_minutes),
        "type": "guest",
    }

    token = jwt.encode(payload, settings.guest_token_secret, algorithm="HS256")

    logger.info("Issued guest token for %s (TTL=%dm)", guest_id, settings.guest_token_ttl_minutes)

    return {
        "token": token,
        "expires_in": settings.guest_token_ttl_minutes * 60,
        "guest_id": guest_id,
    }
