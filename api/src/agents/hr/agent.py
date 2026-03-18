# ---------------------------------------------------------------------------
# HR Agent — handles human resources, policy, and governance queries.
#
# Notable differences from the IT agent:
#   - auth_level is MICROSOFT_ACCOUNT (not ORGANISATIONAL) — any Microsoft
#     user can access HR info, not just org-tenant users.
#   - strip_source_urls is True — HR documents come from SharePoint and
#     their URLs shouldn't be exposed to users.
# ---------------------------------------------------------------------------

from src.agents._base import AuthLevel, DomainAgent, RAGScope, get_organisation_name
from src.agents.hr.prompts import HR_SYSTEM_PROMPT_TEMPLATE


class HRAgent(DomainAgent):
    @property
    def name(self) -> str:
        return "hr_agent"

    @property
    def description(self) -> str:
        # Broad description — HR covers many policy areas, so list them
        # explicitly so the coordinator routes correctly.
        return (
            "Handles all human resources, organisational policy, and governance queries "
            "including leave entitlements, employment agreements, onboarding, performance "
            "reviews, learning & development, code of conduct, risk management, facilities "
            "management, procurement, workplace safety, employee benefits, volunteer "
            "management, privacy, public interest disclosures, entertainment and "
            "hospitality, and any other organisational policy or procedure."
        )

    @property
    def rag_scope(self) -> RAGScope:
        return RAGScope(
            domain="hr",
            document_types=["policy", "procedure", "agreement", "guideline", "form"],
        )

    @property
    def auth_level(self) -> AuthLevel:
        # MICROSOFT_ACCOUNT — any signed-in Microsoft user (any tenant).
        # Less restrictive than ORGANISATIONAL because HR policies are
        # accessible to contractors and partner-org users too.
        return AuthLevel.MICROSOFT_ACCOUNT

    @property
    def display_name(self) -> str:
        return "HR"

    @property
    def image(self) -> str:
        return "hr"

    @property
    def system_prompt(self) -> str:
        return HR_SYSTEM_PROMPT_TEMPLATE.replace(
            "{organisation_name}",
            get_organisation_name(),
        )

    @property
    def default_ui_hint(self) -> str:
        return "text"

    @property
    def strip_source_urls(self) -> bool:
        # True — HR documents are sourced from SharePoint. Their internal
        # URLs are meaningless to end users and may expose internal paths.
        return True
