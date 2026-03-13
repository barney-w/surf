from src.agents._base import AuthLevel, DomainAgent, RAGScope, get_organisation_name
from src.agents.it.prompts import IT_SYSTEM_PROMPT_TEMPLATE


class ITAgent(DomainAgent):
    @property
    def name(self) -> str:
        return "it_agent"

    @property
    def description(self) -> str:
        return (
            "Handles IT support queries including VPN and network connectivity, "
            "password resets and account access, software installation and licensing, "
            "hardware requests and issues, email and Teams troubleshooting, "
            "and IT security policies and procedures."
        )

    @property
    def rag_scope(self) -> RAGScope:
        return RAGScope(
            domain="it",
            document_types=["policy", "procedure", "guideline", "knowledge-base"],
        )

    @property
    def auth_level(self) -> AuthLevel:
        return AuthLevel.ORGANISATIONAL

    @property
    def display_name(self) -> str:
        return "IT Support"

    @property
    def image(self) -> str:
        return "it"

    @property
    def system_prompt(self) -> str:
        return IT_SYSTEM_PROMPT_TEMPLATE.replace(
            "{organisation_name}", get_organisation_name(),
        )

    @property
    def default_ui_hint(self) -> str:
        return "text"
