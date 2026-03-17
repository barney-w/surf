from src.agents._base import DomainAgent, RAGScope
from src.agents.it.prompts import IT_SYSTEM_PROMPT


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
    def system_prompt(self) -> str:
        return IT_SYSTEM_PROMPT

    @property
    def default_ui_hint(self) -> str:
        return "text"
