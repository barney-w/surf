# ---------------------------------------------------------------------------
# Agent Registry — a global store of all registered domain agents.
#
# Agents register themselves automatically via DomainAgent.__init_subclass__.
# The orchestrator reads from this registry at startup to build the agent
# graph. You should never need to call register() manually.
# ---------------------------------------------------------------------------

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.agents._base import DomainAgent


class AgentRegistry:
    # Class-level dict mapping agent name -> agent class.
    # Populated automatically as DomainAgent subclasses are imported.
    _agents: dict[str, type["DomainAgent"]] = {}

    @classmethod
    def register(cls, agent_class: type["DomainAgent"]) -> None:
        """Add an agent class to the registry. Called by __init_subclass__."""
        instance = agent_class()
        # Each agent must have a unique name — duplicates are a bug.
        if instance.name in cls._agents:
            raise ValueError(f"Duplicate agent name: {instance.name}")
        cls._agents[instance.name] = agent_class

    @classmethod
    def get_all(cls) -> dict[str, type["DomainAgent"]]:
        """Return all registered agents as a {name: class} dict."""
        return dict(cls._agents)

    @classmethod
    def get(cls, name: str) -> type["DomainAgent"] | None:
        """Look up a single agent by name. Returns None if not found."""
        return cls._agents.get(name)

    @classmethod
    def agent_descriptions(cls) -> list[dict[str, str]]:
        """Return name + description for each agent.

        Used by the coordinator prompt to decide which agent handles a query.
        """
        return [
            {"name": inst.name, "description": inst.description}
            for agent_cls in cls._agents.values()
            for inst in [agent_cls()]
        ]

    @classmethod
    def agent_metadata(cls) -> list[dict[str, str]]:
        """Return metadata for all registered agents (used by the frontend).

        The /agents endpoint returns this so the UI can show agent cards
        with display names, descriptions, icons, and auth requirements.
        """
        return [
            {
                "id": inst.name,
                "name": inst.display_name,
                "description": inst.description,
                "auth_level": inst.auth_level.value,
                "image": inst.image,
            }
            for agent_cls in cls._agents.values()
            for inst in [agent_cls()]
        ]

    @classmethod
    def clear(cls) -> None:
        """For testing only."""
        cls._agents.clear()
