"""User profile endpoints — /api/v1/me and /api/v1/me/photo."""

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from src.middleware.auth import get_current_user
from src.middleware.rate_limit import limiter

if TYPE_CHECKING:
    from src.services.graph import GraphService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["user"])


def _extract_bearer_token(request: Request) -> str | None:
    """Extract the raw Bearer token from the Authorization header."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth.removeprefix("Bearer ").strip() or None
    return None


@router.get("/me")
@limiter.limit("30/minute")  # pyright: ignore[reportUnknownMemberType,reportUntypedFunctionDecorator]
async def get_me(request: Request) -> JSONResponse:
    """Return the authenticated user's profile (via Graph API OBO or JWT claims)."""
    user = await get_current_user(request)
    graph_service: GraphService | None = getattr(request.app.state, "graph_service", None)

    # Base profile from JWT claims (always available when auth is enabled)
    profile: dict[str, object] = {
        "displayName": user.name,
        "givenName": user.name.split()[0] if user.name else None,
        "department": user.department,
        "jobTitle": user.job_title,
        "mail": user.email,
        "photoUrl": "/api/v1/me/photo",
        "groups": [],
    }

    # Enrich via Graph API OBO if available
    bearer_token = _extract_bearer_token(request)
    if graph_service and graph_service.available and bearer_token:
        graph_token = await graph_service.get_graph_token(bearer_token)
        if graph_token:
            graph_profile = await graph_service.get_user_profile(graph_token)
            if graph_profile:
                profile.update(
                    {
                        "displayName": graph_profile.display_name or user.name,
                        "givenName": graph_profile.given_name
                        or (user.name.split()[0] if user.name else None),
                        "department": graph_profile.department or user.department,
                        "jobTitle": graph_profile.job_title or user.job_title,
                        "officeLocation": graph_profile.office_location,
                        "mail": graph_profile.mail or user.email,
                    }
                )
        # Groups use client credentials (application permission) — independent of OBO.
        groups = await graph_service.get_user_groups(user.user_id)
        if groups:
            profile["groups"] = groups

    return JSONResponse(content=profile)


@router.get("/me/photo")
@limiter.limit("10/minute")  # pyright: ignore[reportUnknownMemberType,reportUntypedFunctionDecorator]
async def get_me_photo(request: Request) -> Response:
    """Return the authenticated user's profile photo (via Graph API OBO)."""
    await get_current_user(request)
    graph_service: GraphService | None = getattr(request.app.state, "graph_service", None)

    bearer_token = _extract_bearer_token(request)
    if not graph_service or not graph_service.available or not bearer_token:
        raise HTTPException(status_code=404, detail="Photo not available")

    graph_token = await graph_service.get_graph_token(bearer_token)
    if not graph_token:
        raise HTTPException(status_code=404, detail="Photo not available")

    photo_bytes = await graph_service.get_user_photo(graph_token)
    if not photo_bytes:
        raise HTTPException(status_code=404, detail="No photo set")

    return Response(
        content=photo_bytes,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=3600"},
    )
