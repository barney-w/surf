from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RAGScope:
    """Defines how this agent's RAG queries are filtered."""

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
    def tools(self) -> list[Callable[..., Any]]:
        """Domain-specific tools beyond shared RAG. Override to add tools."""
        return []

    @property
    def default_ui_hint(self) -> str:
        return "text"

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if not getattr(cls, "__abstract__", False) and cls is not DomainAgent:
            from src.agents._registry import AgentRegistry

            AgentRegistry.register(cls)
