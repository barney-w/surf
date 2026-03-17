from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.agents._base import DomainAgent


class AgentRegistry:
    _agents: dict[str, type["DomainAgent"]] = {}

    @classmethod
    def register(cls, agent_class: type["DomainAgent"]) -> None:
        instance = agent_class()
        if instance.name in cls._agents:
            raise ValueError(f"Duplicate agent name: {instance.name}")
        cls._agents[instance.name] = agent_class

    @classmethod
    def get_all(cls) -> dict[str, type["DomainAgent"]]:
        return dict(cls._agents)

    @classmethod
    def get(cls, name: str) -> type["DomainAgent"] | None:
        return cls._agents.get(name)

    @classmethod
    def agent_descriptions(cls) -> list[dict[str, str]]:
        return [
            {"name": inst.name, "description": inst.description}
            for agent_cls in cls._agents.values()
            for inst in [agent_cls()]
        ]

    @classmethod
    def agent_metadata(cls) -> list[dict[str, str]]:
        """Return metadata for all registered agents (used by the frontend)."""
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
