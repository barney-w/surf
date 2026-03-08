import pytest

from src.agents._base import DomainAgent, RAGScope
from src.agents._registry import AgentRegistry


@pytest.fixture(autouse=True)
def _clean_registry():
    """Clear the registry before and after each test."""
    AgentRegistry.clear()
    yield
    AgentRegistry.clear()


def _register_hr_agent():
    """Import HRAgent and ensure it is registered (re-register if needed after clear)."""
    from src.agents.hr.agent import HRAgent

    if AgentRegistry.get("hr_agent") is None:
        AgentRegistry.register(HRAgent)
    return HRAgent


class TestAgentAutoRegistration:
    def test_hr_agent_registers_on_import(self):
        hr_agent_cls = _register_hr_agent()
        assert AgentRegistry.get("hr_agent") is hr_agent_cls

    def test_registry_get_returns_none_for_unknown(self):
        assert AgentRegistry.get("nonexistent") is None

    def test_agent_descriptions_includes_hr(self):
        _register_hr_agent()
        descriptions = AgentRegistry.agent_descriptions()
        assert len(descriptions) == 1
        assert descriptions[0]["name"] == "hr_agent"
        assert "human resources" in descriptions[0]["description"].lower()

    def test_get_all_returns_registered_agents(self):
        hr_agent_cls = _register_hr_agent()
        all_agents = AgentRegistry.get_all()
        assert "hr_agent" in all_agents
        assert all_agents["hr_agent"] is hr_agent_cls


class TestDiscovery:
    def test_discover_agents_finds_hr(self):
        from src.agents._discovery import discover_agents

        discover_agents()
        assert AgentRegistry.get("hr_agent") is not None


class TestCoordinatorPrompt:
    def test_build_coordinator_prompt_includes_agent_info(self):
        from src.agents.coordinator.prompts import build_coordinator_prompt

        descriptions = [
            {"name": "hr_agent", "description": "Handles HR queries."},
        ]
        prompt = build_coordinator_prompt(descriptions)
        assert "hr_agent" in prompt
        assert "Handles HR queries." in prompt
        assert "Surf" in prompt


class TestDuplicateRegistration:
    def test_duplicate_agent_name_raises(self):
        _register_hr_agent()
        with pytest.raises(ValueError, match="Duplicate agent name: hr_agent"):
            AgentRegistry.register(
                type(
                    "FakeHRAgent",
                    (),
                    {
                        "name": property(lambda self: "hr_agent"),
                        "description": property(lambda self: "Duplicate"),
                        "system_prompt": property(lambda self: "Duplicate"),
                        "rag_scope": property(lambda self: RAGScope(domain="hr")),
                    },
                )
            )


class TestInitSubclassRegistration:
    def test_init_subclass_triggers_registration(self):
        """Verify __init_subclass__ auto-registers a new DomainAgent subclass."""

        class TestAgent(DomainAgent):
            @property
            def name(self) -> str:
                return "test_agent"

            @property
            def description(self) -> str:
                return "A test agent"

            @property
            def system_prompt(self) -> str:
                return "Test prompt"

            @property
            def rag_scope(self) -> RAGScope:
                return RAGScope(domain="test")

        assert AgentRegistry.get("test_agent") is TestAgent
