# ---------------------------------------------------------------------------
# Domain Agent base class.
#
# Every specialist agent (HR, IT, Website, etc.) subclasses DomainAgent.
# These are *configuration* classes — they don't run queries themselves.
# The orchestrator reads them and builds real framework Agent instances.
#
# To create a new agent, subclass DomainAgent in a new package under
# api/src/agents/<domain>/agent.py.  Implement the four required properties
# (name, description, system_prompt, rag_scope) and you're done — the
# auto-registration hook handles the rest.
#
# Full walkthrough: docs/runbooks/add-new-agent.md
# ---------------------------------------------------------------------------

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any


@lru_cache
def get_organisation_name() -> str:
    """Return the configured organisation name, falling back to a generic label."""
    from src.config.settings import get_settings

    return get_settings().organisation_name or "the organisation"


class AuthLevel(StrEnum):
    """Minimum authentication level required to access an agent.

    The coordinator checks this before handing off to an agent.
    PUBLIC agents are available to everyone, MICROSOFT_ACCOUNT requires
    a signed-in Microsoft user, and ORGANISATIONAL requires a user from
    the organisation's own tenant.
    """

    PUBLIC = "public"               # No login needed — anyone can use this agent
    MICROSOFT_ACCOUNT = "microsoft"  # Requires a Microsoft account (any tenant)
    ORGANISATIONAL = "organisational"  # Requires an account from the org's tenant


@dataclass
class RAGScope:
    """Defines how this agent's search queries are filtered in Azure AI Search.

    When the agent calls `search_knowledge_base`, these fields are used to
    build an OData filter expression so the agent only sees relevant documents.

    ``metadata_filters`` supports any filterable index field as a key,
    including ``content_source`` (e.g. ``{"content_source": "website"}``).
    """

    # Primary domain tag — matches the "domain" field in the search index.
    # Set to "" if filtering by metadata_filters instead (see WebsiteAgent).
    domain: str

    # Allowed document types — matches "document_type" in the search index.
    # e.g. ["policy", "procedure", "guideline"]. Empty list = no type filter.
    document_types: list[str] = field(default_factory=lambda: [])

    # Extra index field filters applied as OData $filter clauses.
    # e.g. {"content_source": "website"} filters to web-scraped content only.
    metadata_filters: dict[str, str] = field(default_factory=lambda: {})


class DomainAgent(ABC):
    """
    Base class for all domain agents. Subclasses self-register
    with the AgentRegistry on class creation.

    NOTE: This is NOT a subclass of agent_framework.Agent.
    This is a configuration class. The orchestrator builder reads
    these and creates agent_framework.Agent instances via client.as_agent().
    """

    # --- Required properties (you MUST implement these) -------------------

    @property
    @abstractmethod
    def name(self) -> str:
        # Unique identifier for this agent, e.g. "hr_agent".
        # Used in routing, logging, and the registry key.
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        # Natural-language description of what this agent handles.
        # The coordinator reads this to decide which agent gets each query,
        # so be specific about the topics this agent covers.
        ...

    @property
    @abstractmethod
    def system_prompt(self) -> str:
        # Full system prompt sent to the LLM when this agent runs.
        # Should include the shared DOMAIN_AGENT_INSTRUCTIONS plus
        # domain-specific role, guidelines, tone, and disclaimers.
        ...

    @property
    @abstractmethod
    def rag_scope(self) -> RAGScope:
        # Defines how search results are filtered for this agent.
        # See the RAGScope dataclass above for field details.
        ...

    # --- Optional properties (override to customise) ----------------------

    @property
    def skill_path(self) -> Path | None:
        """Path to this agent's skill directory (contains SKILL.md).

        Defaults to ``api/skills/<domain>/`` based on the RAG scope domain.
        Override to customise or return ``None`` to disable skills.
        """
        # The default convention: api/skills/<rag_scope.domain>/
        # Override this if your domain name doesn't match rag_scope.domain
        # (e.g. WebsiteAgent has domain="" so it overrides this).
        skills_dir = (
            Path(__file__).resolve().parent.parent.parent / "skills" / self.rag_scope.domain
        )
        return skills_dir if skills_dir.is_dir() else None

    @property
    def tools(self) -> list[Callable[..., Any]]:
        """Domain-specific tools beyond shared RAG. Override to add tools."""
        # Every agent gets the RAG search_knowledge_base tool automatically.
        # Only override this if your agent needs extra tools (e.g. a calculator).
        return []

    @property
    def model_id(self) -> str | None:
        """Model override for this agent. None = use settings default."""
        # Set to a specific model ID string if this agent needs a different
        # LLM than the default (e.g. a cheaper model for simple lookups).
        return None

    @property
    def auth_level(self) -> AuthLevel:
        """Minimum auth level required to access this agent."""
        # Override to restrict access. See AuthLevel above for options.
        return AuthLevel.PUBLIC

    @property
    def display_name(self) -> str:
        """Human-friendly name for the frontend."""
        # Shown in the UI as the agent's label. Defaults to title-cased name.
        return self.name.replace("_", " ").title()

    @property
    def image(self) -> str:
        """Icon identifier for the frontend."""
        # Maps to an icon in the web client. Must match a known icon key.
        return "default"

    @property
    def default_ui_hint(self) -> str:
        # Tells the frontend how to render responses by default.
        # Options: "text", "table", "card", "list", "steps", "warning"
        return "text"

    @property
    def strip_source_urls(self) -> bool:
        """Whether to strip URLs from sources in responses."""
        # Set to True for agents whose sources are internal documents
        # (e.g. SharePoint) where URLs shouldn't be exposed to the user.
        return False

    # --- Auto-registration hook -------------------------------------------

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Called automatically when a class subclasses DomainAgent.

        This is the magic that makes auto-registration work — you don't need
        to manually register your agent anywhere. Just subclass DomainAgent
        and the registry picks it up.
        """
        super().__init_subclass__(**kwargs)
        # Skip registration for intermediate abstract classes
        if not getattr(cls, "__abstract__", False) and cls is not DomainAgent:
            from src.agents._registry import AgentRegistry

            AgentRegistry.register(cls)
