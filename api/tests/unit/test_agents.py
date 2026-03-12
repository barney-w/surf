from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.agents._base import DomainAgent, RAGScope
from src.agents._registry import AgentRegistry


@pytest.fixture(autouse=True)
def _clean_registry() -> Generator[None, None, None]:  # pyright: ignore[reportUnusedFunction]
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


def _register_website_agent():
    """Import WebsiteAgent and ensure it is registered (re-register if needed after clear)."""
    from src.agents.website.agent import WebsiteAgent

    if AgentRegistry.get("website_agent") is None:
        AgentRegistry.register(WebsiteAgent)
    return WebsiteAgent


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


class TestWebsiteAgentProperties:
    """Verify WebsiteAgent has the expected configuration values."""

    def test_name(self):
        website_agent_cls = _register_website_agent()
        agent = website_agent_cls()
        assert agent.name == "website_agent"

    def test_description_contains_key_terms(self):
        website_agent_cls = _register_website_agent()
        agent = website_agent_cls()
        desc_lower = agent.description.lower()
        assert "public" in desc_lower
        assert "website" in desc_lower

    def test_rag_scope_domain_is_empty(self):
        website_agent_cls = _register_website_agent()
        agent = website_agent_cls()
        assert agent.rag_scope.domain == ""

    def test_rag_scope_metadata_filters(self):
        website_agent_cls = _register_website_agent()
        agent = website_agent_cls()
        assert agent.rag_scope.metadata_filters == {"content_source": "website"}

    def test_rag_scope_document_types_empty(self):
        website_agent_cls = _register_website_agent()
        agent = website_agent_cls()
        assert agent.rag_scope.document_types == []

    def test_skill_path_resolves_to_website_dir(self):
        website_agent_cls = _register_website_agent()
        agent = website_agent_cls()
        skill_path = agent.skill_path
        assert skill_path is not None
        assert skill_path.name == "website"
        assert skill_path.is_dir()
        assert (skill_path / "SKILL.md").exists()


class TestWebsiteAgentAutoRegistration:
    """Verify WebsiteAgent is discoverable via discover_agents."""

    def test_discover_agents_finds_website(self):
        from src.agents._discovery import discover_agents

        discover_agents()
        assert AgentRegistry.get("website_agent") is not None

    def test_registry_includes_website_in_descriptions(self):
        _register_website_agent()
        descriptions = AgentRegistry.agent_descriptions()
        names = [d["name"] for d in descriptions]
        assert "website_agent" in names


class TestCoordinatorPromptIncludesWebsite:
    """Verify the coordinator prompt renders website agent info."""

    def test_website_agent_appears_in_coordinator_prompt(self):
        from src.agents.coordinator.prompts import build_coordinator_prompt

        descriptions = [
            {"name": "website_agent", "description": "Handles public website queries."},
        ]
        prompt = build_coordinator_prompt(descriptions)
        assert "website_agent" in prompt
        assert "Handles public website queries." in prompt


class TestWebsitePromptContent:
    """Verify website prompt includes shared instructions and domain content."""

    def test_website_prompt_includes_shared_instructions(self):
        from src.agents.website.prompts import WEBSITE_SYSTEM_PROMPT

        assert "=== SOURCE N ===" in WEBSITE_SYSTEM_PROMPT
        assert "search_knowledge_base" in WEBSITE_SYSTEM_PROMPT

    def test_website_prompt_includes_domain_content(self):
        from src.agents.website.prompts import WEBSITE_SYSTEM_PROMPT

        assert "public website information specialist" in WEBSITE_SYSTEM_PROMPT


class TestWebsiteSkillMd:
    """Verify the website SKILL.md file exists and has frontmatter."""

    def test_website_skill_md_has_frontmatter(self):
        skill_path = (
            Path(__file__).resolve().parent.parent.parent / "skills" / "website" / "SKILL.md"
        )
        content = skill_path.read_text()
        assert content.startswith("---")


class TestDuplicateRegistration:
    def test_duplicate_agent_name_raises(self):
        _register_hr_agent()
        with pytest.raises(ValueError, match="Duplicate agent name: hr_agent"):
            AgentRegistry.register(
                type(  # pyright: ignore[reportArgumentType]
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


class TestSkillPath:
    """Verify DomainAgent.skill_path resolution."""

    def test_hr_agent_skill_path_exists(self):
        _register_hr_agent()
        from src.agents.hr.agent import HRAgent

        agent = HRAgent()
        skill_path = agent.skill_path
        assert skill_path is not None
        assert skill_path.is_dir()
        assert (skill_path / "SKILL.md").exists()

    def test_it_agent_skill_path_exists(self):
        from src.agents.it.agent import ITAgent

        agent = ITAgent()
        skill_path = agent.skill_path
        assert skill_path is not None
        assert skill_path.is_dir()
        assert (skill_path / "SKILL.md").exists()

    def test_skill_path_returns_none_for_missing_domain(self):
        """An agent whose domain has no skills directory should get None."""

        class NoSkillAgent(DomainAgent):
            @property
            def name(self) -> str:
                return "noskill_agent"

            @property
            def description(self) -> str:
                return "No skills"

            @property
            def system_prompt(self) -> str:
                return "No skills"

            @property
            def rag_scope(self) -> RAGScope:
                return RAGScope(domain="nonexistent_domain_xyz")

        agent = NoSkillAgent()
        assert agent.skill_path is None


class TestSharedInstructions:
    """Verify shared instructions are included in domain agent prompts."""

    def test_hr_prompt_includes_shared_instructions(self):
        from src.agents.hr.prompts import HR_SYSTEM_PROMPT

        assert "=== SOURCE N ===" in HR_SYSTEM_PROMPT
        assert "search_knowledge_base" in HR_SYSTEM_PROMPT
        assert "HR and organisational policy specialist" in HR_SYSTEM_PROMPT

    def test_it_prompt_includes_shared_instructions(self):
        from src.agents.it.prompts import IT_SYSTEM_PROMPT

        assert "=== SOURCE N ===" in IT_SYSTEM_PROMPT
        assert "search_knowledge_base" in IT_SYSTEM_PROMPT
        assert "IT support specialist" in IT_SYSTEM_PROMPT

    def test_shared_instructions_contain_key_sections(self):
        from src.agents.shared_instructions import DOMAIN_AGENT_INSTRUCTIONS

        assert "Processing Search Results" in DOMAIN_AGENT_INSTRUCTIONS
        assert "MANDATORY: Search Before Answering" in DOMAIN_AGENT_INSTRUCTIONS
        assert "Always Respond" in DOMAIN_AGENT_INSTRUCTIONS
        assert "Structured Output Fields" in DOMAIN_AGENT_INSTRUCTIONS
        assert "follow_up_suggestions" in DOMAIN_AGENT_INSTRUCTIONS


class TestSkillMdFiles:
    """Verify SKILL.md files have correct frontmatter and content."""

    def test_hr_skill_md_has_frontmatter(self):
        skill_path = Path(__file__).resolve().parent.parent.parent / "skills" / "hr" / "SKILL.md"
        content = skill_path.read_text()
        assert content.startswith("---")
        assert "name: hr-domain-expertise" in content
        assert "HR & Organisational Policy Expertise" in content

    def test_it_skill_md_has_frontmatter(self):
        skill_path = Path(__file__).resolve().parent.parent.parent / "skills" / "it" / "SKILL.md"
        content = skill_path.read_text()
        assert content.startswith("---")
        assert "name: it-domain-expertise" in content
        assert "IT Support Expertise" in content


class TestCreateModelClient:
    """Verify create_model_client picks the right backend."""

    def test_direct_anthropic_when_no_foundry_url(self):
        from src.config.settings import Settings
        from src.orchestrator.builder import create_model_client

        settings = Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            anthropic_api_key="sk-ant-test",
            anthropic_model_id="claude-sonnet-4-6",
        )
        client = create_model_client(settings)
        base_url = str(client.anthropic_client.base_url)
        assert "anthropic.com" in base_url

    def test_foundry_when_base_url_set(self):
        from src.config.settings import Settings
        from src.orchestrator.builder import create_model_client

        settings = Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            anthropic_foundry_base_url="https://test-resource.services.ai.azure.com/anthropic/",
            anthropic_foundry_api_key="foundry-test-key",
            anthropic_model_id="claude-sonnet-4-6",
        )
        client = create_model_client(settings)
        base_url = str(client.anthropic_client.base_url)
        assert "services.ai.azure.com" in base_url

    def test_foundry_takes_precedence_over_direct_key(self):
        from src.config.settings import Settings
        from src.orchestrator.builder import create_model_client

        settings = Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            anthropic_api_key="sk-ant-should-not-use",
            anthropic_foundry_base_url="https://test-resource.services.ai.azure.com/anthropic/",
            anthropic_foundry_api_key="foundry-test-key",
            anthropic_model_id="claude-sonnet-4-6",
        )
        client = create_model_client(settings)
        base_url = str(client.anthropic_client.base_url)
        assert "services.ai.azure.com" in base_url


class TestSafeHandoffAnthropicClient:
    """Verify the client subclass fixes conversations ending with assistant messages."""

    def test_appends_user_message_when_conversation_ends_with_assistant(self):
        from agent_framework import Message

        from src.orchestrator.builder import (
            _SafeHandoffAnthropicClient,  # pyright: ignore[reportPrivateUsage]
        )

        client = _SafeHandoffAnthropicClient(api_key="test-key", model_id="test-model")
        messages = [
            Message(role="user", text="What is the leave policy?"),
            Message(role="assistant", text="Let me route this to the HR specialist."),
        ]
        prepared = client._prepare_messages_for_anthropic(messages)  # pyright: ignore[reportPrivateUsage]
        assert prepared[-1]["role"] == "user"
        assert len(prepared) == 3

    def test_no_change_when_conversation_ends_with_user(self):
        from agent_framework import Message

        from src.orchestrator.builder import (
            _SafeHandoffAnthropicClient,  # pyright: ignore[reportPrivateUsage]
        )

        client = _SafeHandoffAnthropicClient(api_key="test-key", model_id="test-model")
        messages = [
            Message(role="user", text="What is the leave policy?"),
        ]
        prepared = client._prepare_messages_for_anthropic(messages)  # pyright: ignore[reportPrivateUsage]
        assert prepared[-1]["role"] == "user"
        assert len(prepared) == 1

    def test_handles_system_then_assistant(self):
        from agent_framework import Message

        from src.orchestrator.builder import (
            _SafeHandoffAnthropicClient,  # pyright: ignore[reportPrivateUsage]
        )

        client = _SafeHandoffAnthropicClient(api_key="test-key", model_id="test-model")
        messages = [
            Message(role="system", text="You are a helpful assistant."),
            Message(role="user", text="Hello"),
            Message(role="assistant", text="I'll check that for you."),
        ]
        prepared = client._prepare_messages_for_anthropic(messages)  # pyright: ignore[reportPrivateUsage]
        # System message is stripped (handled as separate param by Anthropic)
        assert prepared[0]["role"] == "user"
        assert prepared[-1]["role"] == "user"


class TestPerAgentModel:
    """Verify per-agent model resolution in build_agent_graph."""

    def _make_settings(self, **overrides):
        from src.config.settings import Settings

        defaults = {
            "anthropic_api_key": "sk-ant-test",
            "anthropic_model_id": "claude-sonnet-4-6",
            "anthropic_domain_model_id": "",
        }
        defaults.update(overrides)
        return Settings(_env_file=None, **defaults)  # pyright: ignore[reportCallIssue]

    @patch("src.orchestrator.builder.create_rag_tool")
    def test_domain_model_creates_separate_client(self, mock_rag):
        """When domain model differs from global, a separate client is created."""
        from src.orchestrator.builder import (
            _SafeHandoffAnthropicClient,  # pyright: ignore[reportPrivateUsage]
            build_agent_graph,
            create_model_client,
        )

        _register_hr_agent()
        mock_rag.return_value = MagicMock()

        settings = self._make_settings(anthropic_domain_model_id="claude-haiku-4-5")
        coordinator_client = create_model_client(settings)

        with patch("src.orchestrator.builder.create_model_client_for_model") as mock_create:
            mock_create.return_value = _SafeHandoffAnthropicClient(
                api_key="sk-ant-test", model_id="claude-haiku-4-5"
            )
            build_agent_graph(coordinator_client, settings)

            # Should be called once for the domain client
            mock_create.assert_called_with(settings, "claude-haiku-4-5")

    @patch("src.orchestrator.builder.create_rag_tool")
    def test_same_model_reuses_client(self, mock_rag):
        """When domain model equals global, no second client is created."""
        from src.orchestrator.builder import (
            build_agent_graph,
            create_model_client,
        )

        _register_hr_agent()
        mock_rag.return_value = MagicMock()

        settings = self._make_settings()  # domain model empty => same as global
        coordinator_client = create_model_client(settings)

        with patch("src.orchestrator.builder.create_model_client_for_model") as mock_create:
            build_agent_graph(coordinator_client, settings)
            mock_create.assert_not_called()

    @patch("src.orchestrator.builder.create_rag_tool")
    def test_agent_model_override(self, mock_rag):
        """An agent with model_id set gets its own client."""
        from src.orchestrator.builder import (
            _SafeHandoffAnthropicClient,  # pyright: ignore[reportPrivateUsage]
            build_agent_graph,
            create_model_client,
        )

        # Create a test agent with a specific model override
        class SpecialistAgent(DomainAgent):
            @property
            def name(self) -> str:
                return "specialist_agent"

            @property
            def description(self) -> str:
                return "A specialist agent"

            @property
            def system_prompt(self) -> str:
                return "You are a specialist."

            @property
            def rag_scope(self) -> RAGScope:
                return RAGScope(domain="specialist")

            @property
            def model_id(self) -> str | None:
                return "claude-opus-4-6"

        mock_rag.return_value = MagicMock()

        settings = self._make_settings()
        coordinator_client = create_model_client(settings)

        with patch("src.orchestrator.builder.create_model_client_for_model") as mock_create:
            mock_create.return_value = _SafeHandoffAnthropicClient(
                api_key="sk-ant-test", model_id="claude-opus-4-6"
            )
            build_agent_graph(coordinator_client, settings)

            # Should be called with the agent-specific model
            mock_create.assert_any_call(settings, "claude-opus-4-6")
