from src.agents._base import DomainAgent, RAGScope
from src.agents.hr.prompts import HR_SYSTEM_PROMPT


class HRAgent(DomainAgent):
    @property
    def name(self) -> str:
        return "hr_agent"

    @property
    def description(self) -> str:
        return (
            "Handles all human resources queries including leave entitlements "
            "(annual, personal, long service, parental), employment agreement "
            "interpretation, onboarding procedures, performance review timelines, "
            "learning & development options, and HR policy questions."
        )

    @property
    def rag_scope(self) -> RAGScope:
        return RAGScope(
            domain="hr",
            document_types=["policy", "procedure", "agreement", "guideline", "form"],
        )

    @property
    def system_prompt(self) -> str:
        return HR_SYSTEM_PROMPT

    @property
    def default_ui_hint(self) -> str:
        return "text"
