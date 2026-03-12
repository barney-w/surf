from pathlib import Path

from src.agents._base import DomainAgent, RAGScope
from src.agents.website.prompts import WEBSITE_SYSTEM_PROMPT


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
    def system_prompt(self) -> str:
        return WEBSITE_SYSTEM_PROMPT

    @property
    def default_ui_hint(self) -> str:
        return "text"
