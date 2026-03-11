"""Microsoft Graph API service using the On-Behalf-Of (OBO) flow."""

import logging
from dataclasses import dataclass

import httpx
import msal  # pyright: ignore[reportMissingTypeStubs]

from src.config.settings import get_settings

logger = logging.getLogger(__name__)

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_GRAPH_SCOPES = ["User.Read", "GroupMember.Read.All"]


@dataclass
class UserProfile:
    display_name: str
    given_name: str | None
    department: str | None
    job_title: str | None
    office_location: str | None
    mail: str | None


class GraphService:
    """Exchanges a user's access token via OBO and calls MS Graph."""

    def __init__(self) -> None:
        settings = get_settings()
        self._app: msal.ConfidentialClientApplication | None = None
        if settings.entra_client_id and settings.entra_client_secret and settings.entra_tenant_id:
            self._app = msal.ConfidentialClientApplication(
                client_id=settings.entra_client_id,
                client_credential=settings.entra_client_secret,
                authority=f"https://login.microsoftonline.com/{settings.entra_tenant_id}",
            )
            logger.info("GraphService initialised with OBO capability")
        else:
            logger.warning("GraphService: missing Entra credentials — OBO disabled")

    @property
    def available(self) -> bool:
        return self._app is not None

    async def get_graph_token(self, user_assertion: str) -> str | None:
        """Exchange a user access token for a Graph API token via OBO."""
        if not self._app:
            return None

        result: dict[str, object] = self._app.acquire_token_on_behalf_of(  # type: ignore[assignment]
            user_assertion=user_assertion,
            scopes=_GRAPH_SCOPES,
        )

        if "access_token" in result:
            return str(result["access_token"])

        error_desc = result.get("error_description", "")
        error_code = result.get("error", "unknown")
        logger.warning("OBO token acquisition failed: %s — %s", error_code, error_desc)
        return None

    async def get_user_profile(self, graph_token: str) -> UserProfile | None:
        """Fetch the signed-in user's profile from Graph."""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{_GRAPH_BASE}/me",
                    params={
                        "$select": "displayName,givenName,department,jobTitle,officeLocation,mail"
                    },
                    headers={"Authorization": f"Bearer {graph_token}"},
                    timeout=10,
                )
                resp.raise_for_status()
                data = resp.json()
                return UserProfile(
                    display_name=data.get("displayName", ""),
                    given_name=data.get("givenName"),
                    department=data.get("department"),
                    job_title=data.get("jobTitle"),
                    office_location=data.get("officeLocation"),
                    mail=data.get("mail"),
                )
        except Exception:
            logger.warning("Failed to fetch user profile from Graph", exc_info=True)
            return None

    async def get_user_photo(self, graph_token: str) -> bytes | None:
        """Fetch the signed-in user's photo from Graph. Returns JPEG bytes or None."""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{_GRAPH_BASE}/me/photo/$value",
                    headers={
                        "Authorization": f"Bearer {graph_token}",
                        "Accept": "image/jpeg",
                    },
                    timeout=10,
                )
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                return resp.content
        except Exception:
            logger.warning("Failed to fetch user photo from Graph", exc_info=True)
            return None

    async def get_user_groups(self, graph_token: str) -> list[str]:
        """Fetch the signed-in user's group display names."""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{_GRAPH_BASE}/me/memberOf",
                    params={"$select": "displayName,id", "$top": "100"},
                    headers={"Authorization": f"Bearer {graph_token}"},
                    timeout=10,
                )
                resp.raise_for_status()
                data = resp.json()
                return [g["displayName"] for g in data.get("value", []) if g.get("displayName")]
        except Exception:
            logger.warning("Failed to fetch user groups from Graph", exc_info=True)
            return []
