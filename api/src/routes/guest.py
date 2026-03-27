"""Guest token endpoint — issues short-lived anonymous access tokens."""

import datetime
import logging
import re
import uuid

import jwt
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from src.config.settings import get_settings
from src.middleware.auth import GUEST_ISSUER
from src.middleware.rate_limit import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# Strict pattern for guest IDs — prevents injection of arbitrary subject claims.
_GUEST_ID_RE = re.compile(r"^guest-[0-9a-f]{12}$")


class GuestTokenRequest(BaseModel):
    """Optional body for POST /auth/guest.

    When ``guest_id`` is supplied the server re-issues a token for the
    same identity, preserving conversation history across page reloads
    and token refreshes.
    """

    guest_id: str | None = None


@router.post("/guest")
@limiter.limit("5/minute")  # pyright: ignore[reportUnknownMemberType,reportUntypedFunctionDecorator]
async def create_guest_token(
    request: Request,
    body: GuestTokenRequest | None = None,
) -> dict[str, object]:
    """Issue a short-lived anonymous JWT for guest access.

    The token is signed with a server-side HMAC secret and carries a
    random — or previously-issued — subject ID.  When the client sends
    back a ``guest_id`` it received earlier, the same identity is reused
    so that conversation history is preserved across token renewals and
    page reloads.
    """
    settings = get_settings()

    if not settings.guest_token_secret:
        raise HTTPException(
            status_code=403,
            detail="Guest access is not enabled",
        )

    # Reuse a previously-issued guest ID if it passes validation,
    # otherwise mint a fresh one.
    guest_id: str | None = body.guest_id if body else None
    if guest_id and _GUEST_ID_RE.match(guest_id):
        logger.info("Renewing guest token for existing identity %s", guest_id)
    else:
        guest_id = f"guest-{uuid.uuid4().hex[:12]}"

    now = datetime.datetime.now(datetime.UTC)

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
