from pathlib import Path

from src.agents._base import DomainAgent, RAGScope, get_organisation_name
from src.agents.website.prompts import WEBSITE_SYSTEM_PROMPT_TEMPLATE


class WebsiteAgent(DomainAgent):
    @property
    def name(self) -> str:
        return "website_agent"

    @property
    def description(self) -> str:
        return (
            "Handles questions about public-facing information published on the "
            "organisation's website — services, programs, facilities, events, "
            "locations, opening hours, waste and recycling, community resources, "
            "and general enquiries about what the organisation offers."
        )

    @property
    def rag_scope(self) -> RAGScope:
        return RAGScope(
            domain="",
            document_types=[],
            metadata_filters={"content_source": "website"},
        )

    @property
    def skill_path(self) -> Path | None:
        """Explicit path since rag_scope.domain is empty."""
        skills_dir = Path(__file__).resolve().parent.parent.parent.parent / "skills" / "website"
        return skills_dir if skills_dir.is_dir() else None

    @property
    def display_name(self) -> str:
        return "Website"

    @property
    def image(self) -> str:
        return "website"

    @property
    def system_prompt(self) -> str:
        return WEBSITE_SYSTEM_PROMPT_TEMPLATE.replace(
            "{organisation_name}",
            get_organisation_name(),
        )

    @property
    def default_ui_hint(self) -> str:
        return "text"
