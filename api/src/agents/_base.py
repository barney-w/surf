from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class RAGScope:
    """Defines how this agent's RAG queries are filtered.

    ``metadata_filters`` supports any filterable index field as a key,
    including ``content_source`` (e.g. ``{"content_source": "website"}``).
    """

    domain: str
    document_types: list[str] = field(default_factory=lambda: [])
    metadata_filters: dict[str, str] = field(default_factory=lambda: {})


class DomainAgent(ABC):
    """
    Base class for all domain agents. Subclasses self-register
    with the AgentRegistry on class creation.

    NOTE: This is NOT a subclass of agent_framework.Agent.
    This is a configuration class. The orchestrator builder reads
    these and creates agent_framework.Agent instances via client.as_agent().
    """

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    @abstractmethod
    def system_prompt(self) -> str: ...

    @property
    @abstractmethod
    def rag_scope(self) -> RAGScope: ...

    @property
    def skill_path(self) -> Path | None:
        """Path to this agent's skill directory (contains SKILL.md).

        Defaults to ``api/skills/<domain>/`` based on the RAG scope domain.
        Override to customise or return ``None`` to disable skills.
        """
        skills_dir = (
            Path(__file__).resolve().parent.parent.parent / "skills" / self.rag_scope.domain
        )
        return skills_dir if skills_dir.is_dir() else None

    @property
    def tools(self) -> list[Callable[..., Any]]:
        """Domain-specific tools beyond shared RAG. Override to add tools."""
        return []

    @property
    def model_id(self) -> str | None:
        """Model override for this agent. None = use settings default."""
        return None

    @property
    def default_ui_hint(self) -> str:
        return "text"

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if not getattr(cls, "__abstract__", False) and cls is not DomainAgent:
            from src.agents._registry import AgentRegistry

            AgentRegistry.register(cls)
