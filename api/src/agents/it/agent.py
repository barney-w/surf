# ---------------------------------------------------------------------------
# IT Support Agent — handles IT helpdesk and technical support queries.
#
# This is a good reference example if you're creating a new agent.
# Copy this file, change the class name and properties, and you're done.
# Full guide: docs/runbooks/add-new-agent.md
# ---------------------------------------------------------------------------

from src.agents._base import AuthLevel, DomainAgent, RAGScope, get_organisation_name
from src.agents.it.prompts import IT_SYSTEM_PROMPT_TEMPLATE


class ITAgent(DomainAgent):
    @property
    def name(self) -> str:
        # Unique identifier — used in routing, logging, and the registry.
        # Convention: lowercase with underscores, ending in "_agent".
        return "it_agent"

    @property
    def description(self) -> str:
        # The coordinator reads this to decide if a user's query should
        # be routed to this agent. Be specific about the topics covered —
        # vague descriptions lead to poor routing.
        return (
            "Handles IT support queries including VPN and network connectivity, "
            "password resets and account access, software installation and licensing, "
            "hardware requests and issues, email and Teams troubleshooting, "
            "and IT security policies and procedures."
        )

    @property
    def rag_scope(self) -> RAGScope:
        # Filters search results so this agent only sees IT documents.
        # "domain" matches the domain field in the Azure AI Search index.
        # "document_types" restricts to these document type tags.
        return RAGScope(
            domain="it",
            document_types=["policy", "procedure", "guideline", "knowledge-base"],
        )

    @property
    def auth_level(self) -> AuthLevel:
        # ORGANISATIONAL = only users from the org's own Entra tenant.
        # Use PUBLIC for unauthenticated access, MICROSOFT_ACCOUNT for
        # any Microsoft account (regardless of tenant).
        return AuthLevel.ORGANISATIONAL

    @property
    def display_name(self) -> str:
        # Shown in the frontend UI as the agent's label.
        return "IT Support"

    @property
    def image(self) -> str:
        # Icon key — the web client maps this to an icon component.
        return "it"

    @property
    def system_prompt(self) -> str:
        # Built from the template in prompts.py with the org name injected.
        # The template includes shared DOMAIN_AGENT_INSTRUCTIONS plus
        # IT-specific role, guidelines, tone, and disclaimers.
        return IT_SYSTEM_PROMPT_TEMPLATE.replace(
            "{organisation_name}",
            get_organisation_name(),
        )

    @property
    def default_ui_hint(self) -> str:
        # How the frontend renders responses by default.
        # Options: "text", "table", "card", "list", "steps", "warning"
        return "text"
