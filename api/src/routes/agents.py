import logging

from fastapi import APIRouter, Request

from src.agents._base import AuthLevel
from src.agents._registry import AgentRegistry
from src.middleware.auth import UserContext, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["agents"])

CONSUMER_TENANT_ID = "9188040d-6c67-4c5b-b112-36a304b66dad"


def _resolve_caller_auth_level(user: UserContext) -> AuthLevel:
    """Determine the caller's effective auth level from their UserContext."""
    if user.is_guest:
        return AuthLevel.PUBLIC
    if user.tid and user.tid != CONSUMER_TENANT_ID:
        return AuthLevel.ORGANISATIONAL
    # Authenticated but personal account or no tid
    return AuthLevel.MICROSOFT_ACCOUNT


def _can_access(agent_level: AuthLevel, caller_level: AuthLevel) -> bool:
    """Check if a caller's auth level is sufficient for an agent."""
    hierarchy = {
        AuthLevel.PUBLIC: 0,
        AuthLevel.MICROSOFT_ACCOUNT: 1,
        AuthLevel.ORGANISATIONAL: 2,
    }
    return hierarchy[caller_level] >= hierarchy[agent_level]


@router.get("/agents")
async def list_agents(request: Request) -> list[dict]:
    """Return all agents with metadata and accessibility flags."""
    user = await get_current_user(request)
    caller_level = _resolve_caller_auth_level(user)

    metadata = AgentRegistry.agent_metadata()

    # Add synthetic coordinator entry
    coordinator = {
        "id": "coordinator",
        "name": "Coordinator",
        "description": "Automatically routes your question to the best specialist agent.",
        "auth_level": "public",
        "image": "coordinator",
        "accessible": True,
        "enabled": True,
    }

    agents = [coordinator]
    for agent in metadata:
        agent_auth = AuthLevel(agent["auth_level"])
        agents.append(
            {
                **agent,
                "accessible": _can_access(agent_auth, caller_level),
                "enabled": True,
            }
        )

    return agents
