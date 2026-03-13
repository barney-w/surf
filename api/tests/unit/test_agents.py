from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.agents._base import AuthLevel, DomainAgent, RAGScope
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


def _register_it_agent():
    """Import ITAgent and ensure it is registered (re-register if needed after clear)."""
    from src.agents.it.agent import ITAgent

    if AgentRegistry.get("it_agent") is None:
        AgentRegistry.register(ITAgent)
    return ITAgent


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
        from src.agents.website.prompts import WEBSITE_SYSTEM_PROMPT_TEMPLATE

        assert "=== SOURCE N ===" in WEBSITE_SYSTEM_PROMPT_TEMPLATE
        assert "search_knowledge_base" in WEBSITE_SYSTEM_PROMPT_TEMPLATE

    def test_website_prompt_includes_domain_content(self):
        from src.agents.website.prompts import WEBSITE_SYSTEM_PROMPT_TEMPLATE

        assert "public-facing website information specialist" in WEBSITE_SYSTEM_PROMPT_TEMPLATE


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
        from src.agents.hr.prompts import HR_SYSTEM_PROMPT_TEMPLATE

        assert "=== SOURCE N ===" in HR_SYSTEM_PROMPT_TEMPLATE
        assert "search_knowledge_base" in HR_SYSTEM_PROMPT_TEMPLATE
        assert "HR and organisational policy specialist" in HR_SYSTEM_PROMPT_TEMPLATE

    def test_it_prompt_includes_shared_instructions(self):
        from src.agents.it.prompts import IT_SYSTEM_PROMPT_TEMPLATE

        assert "=== SOURCE N ===" in IT_SYSTEM_PROMPT_TEMPLATE
        assert "search_knowledge_base" in IT_SYSTEM_PROMPT_TEMPLATE
        assert "IT support specialist" in IT_SYSTEM_PROMPT_TEMPLATE

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

    def test_attachments_skip_tool_result_messages(self):
        """Attachments must be injected into the original user message, not into
        a tool_result user message — otherwise the API returns a 400."""
        import base64

        from agent_framework import Message

        from src.orchestrator.builder import (
            _SafeHandoffAnthropicClient,  # pyright: ignore[reportPrivateUsage]
            current_attachments,
        )

        client = _SafeHandoffAnthropicClient(api_key="test-key", model_id="test-model")
        messages = [Message(role="user", text="Analyse this document")]

        # Simulate the prepared output having a tool_use + tool_result pair
        # by patching the parent's method to return the full conversation.
        tool_use_id = "toolu_test123"
        fake_prepared = [
            {"role": "user", "content": [{"type": "text", "text": "Analyse this document"}]},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": tool_use_id,
                        "name": "search_knowledge_base",
                        "input": {"query": "document analysis"},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": "Found 3 results.",
                    }
                ],
            },
        ]

        # Set an image attachment in the context var (avoids PDF parsing deps).
        dummy_img = base64.b64encode(b"fake-image-data").decode()
        token = current_attachments.set([{"content_type": "image/png", "data": dummy_img}])
        try:
            with patch.object(
                type(client).__bases__[0],
                "_prepare_messages_for_anthropic",
                return_value=fake_prepared,
            ):
                result = client._prepare_messages_for_anthropic(messages)  # pyright: ignore[reportPrivateUsage]
        finally:
            current_attachments.reset(token)

        # The tool_result message must NOT contain an image block.
        tool_result_msg = result[2]
        assert tool_result_msg["role"] == "user"
        content_types = [b["type"] for b in tool_result_msg["content"]]
        assert "image" not in content_types, "Attachment was injected into tool_result message"
        assert "tool_result" in content_types

        # The original user message SHOULD contain the image block.
        original_msg = result[0]
        content_types = [b["type"] for b in original_msg["content"]]
        assert "image" in content_types, "Attachment was not injected into original user message"


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


class TestAuthLevelEnum:
    """Verify AuthLevel enum values."""

    def test_public_value(self):
        assert AuthLevel.PUBLIC.value == "public"

    def test_microsoft_account_value(self):
        assert AuthLevel.MICROSOFT_ACCOUNT.value == "microsoft"

    def test_organisational_value(self):
        assert AuthLevel.ORGANISATIONAL.value == "organisational"

    def test_is_str_subclass(self):
        assert isinstance(AuthLevel.PUBLIC, str)


class TestDomainAgentDefaults:
    """Verify default auth_level, display_name, and image on DomainAgent."""

    def test_default_auth_level_is_public(self):
        """A plain DomainAgent subclass defaults to PUBLIC."""

        class PlainAgent(DomainAgent):
            @property
            def name(self) -> str:
                return "plain_agent"

            @property
            def description(self) -> str:
                return "A plain agent"

            @property
            def system_prompt(self) -> str:
                return "Plain prompt"

            @property
            def rag_scope(self) -> RAGScope:
                return RAGScope(domain="plain")

        agent = PlainAgent()
        assert agent.auth_level is AuthLevel.PUBLIC

    def test_default_display_name_generated_from_name(self):
        class PlainAgent(DomainAgent):
            @property
            def name(self) -> str:
                return "my_cool_agent"

            @property
            def description(self) -> str:
                return "A plain agent"

            @property
            def system_prompt(self) -> str:
                return "Plain prompt"

            @property
            def rag_scope(self) -> RAGScope:
                return RAGScope(domain="plain2")

        agent = PlainAgent()
        assert agent.display_name == "My Cool Agent"

    def test_default_image_is_default(self):
        class PlainAgent(DomainAgent):
            @property
            def name(self) -> str:
                return "img_agent"

            @property
            def description(self) -> str:
                return "A plain agent"

            @property
            def system_prompt(self) -> str:
                return "Plain prompt"

            @property
            def rag_scope(self) -> RAGScope:
                return RAGScope(domain="plain3")

        agent = PlainAgent()
        assert agent.image == "default"


class TestHRAgentAuthProperties:
    """Verify HRAgent overrides for auth_level, display_name, image."""

    def test_auth_level_is_microsoft_account(self):
        _register_hr_agent()
        from src.agents.hr.agent import HRAgent

        agent = HRAgent()
        assert agent.auth_level is AuthLevel.MICROSOFT_ACCOUNT

    def test_display_name_is_hr(self):
        _register_hr_agent()
        from src.agents.hr.agent import HRAgent

        agent = HRAgent()
        assert agent.display_name == "HR"

    def test_image_is_hr(self):
        _register_hr_agent()
        from src.agents.hr.agent import HRAgent

        agent = HRAgent()
        assert agent.image == "hr"


class TestWebsiteAgentAuthProperties:
    """Verify WebsiteAgent overrides for display_name and image."""

    def test_display_name_is_website(self):
        _register_website_agent()
        from src.agents.website.agent import WebsiteAgent

        agent = WebsiteAgent()
        assert agent.display_name == "Website"

    def test_image_is_website(self):
        _register_website_agent()
        from src.agents.website.agent import WebsiteAgent

        agent = WebsiteAgent()
        assert agent.image == "website"

    def test_auth_level_defaults_to_public(self):
        _register_website_agent()
        from src.agents.website.agent import WebsiteAgent

        agent = WebsiteAgent()
        assert agent.auth_level is AuthLevel.PUBLIC


class TestITAgentAuthProperties:
    """Verify ITAgent overrides for auth_level, display_name, image."""

    def test_auth_level_is_organisational(self):
        _register_it_agent()
        from src.agents.it.agent import ITAgent

        agent = ITAgent()
        assert agent.auth_level is AuthLevel.ORGANISATIONAL

    def test_display_name_is_it_support(self):
        _register_it_agent()
        from src.agents.it.agent import ITAgent

        agent = ITAgent()
        assert agent.display_name == "IT Support"

    def test_image_is_it(self):
        _register_it_agent()
        from src.agents.it.agent import ITAgent

        agent = ITAgent()
        assert agent.image == "it"


class TestAgentMetadata:
    """Verify AgentRegistry.agent_metadata() returns correct structure."""

    def test_metadata_returns_list_of_dicts(self):
        _register_hr_agent()
        metadata = AgentRegistry.agent_metadata()
        assert isinstance(metadata, list)
        assert len(metadata) == 1

    def test_metadata_has_required_keys(self):
        _register_hr_agent()
        metadata = AgentRegistry.agent_metadata()
        entry = metadata[0]
        assert set(entry.keys()) == {"id", "name", "description", "auth_level", "image"}

    def test_metadata_values_for_hr(self):
        _register_hr_agent()
        metadata = AgentRegistry.agent_metadata()
        entry = metadata[0]
        assert entry["id"] == "hr_agent"
        assert entry["name"] == "HR"
        assert entry["auth_level"] == "microsoft"
        assert entry["image"] == "hr"

    def test_metadata_multiple_agents(self):
        _register_hr_agent()
        _register_website_agent()
        _register_it_agent()
        metadata = AgentRegistry.agent_metadata()
        assert len(metadata) == 3
        ids = {m["id"] for m in metadata}
        assert ids == {"hr_agent", "website_agent", "it_agent"}


# ---------------------------------------------------------------------------
# Tests for the /api/v1/agents endpoint and its helper functions
# ---------------------------------------------------------------------------

import importlib  # noqa: E402
import sys  # noqa: E402
import types  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# Stub telemetry to prevent opentelemetry import chain
if "src.middleware.telemetry" not in sys.modules:
    _telemetry_stub = types.ModuleType("src.middleware.telemetry")
    _telemetry_stub.setup_telemetry = lambda *a, **kw: None  # type: ignore[attr-defined]
    sys.modules["src.middleware.telemetry"] = _telemetry_stub

from src.middleware.auth import UserContext  # noqa: E402

# Load src.routes.agents without triggering src.routes.__init__ (which imports chat)
_agents_mod_path = Path(__file__).resolve().parents[2] / "src" / "routes" / "agents.py"
_spec = importlib.util.spec_from_file_location("src.routes.agents", _agents_mod_path)
assert _spec and _spec.loader
_agents_mod = importlib.util.module_from_spec(_spec)
sys.modules["src.routes.agents"] = _agents_mod
_spec.loader.exec_module(_agents_mod)

_resolve_caller_auth_level = _agents_mod._resolve_caller_auth_level  # type: ignore[attr-defined]
_can_access = _agents_mod._can_access  # type: ignore[attr-defined]
_agents_router = _agents_mod.router  # type: ignore[attr-defined]


class TestResolveCallerAuthLevel:
    """Verify _resolve_caller_auth_level maps UserContext to the correct AuthLevel."""

    def test_guest_user_returns_public(self):
        user = UserContext(user_id="guest-1", name="Guest", email="", is_guest=True)
        assert _resolve_caller_auth_level(user) == AuthLevel.PUBLIC

    def test_personal_account_returns_microsoft_account(self):
        # Consumer tenant ID = personal Microsoft account
        user = UserContext(
            user_id="user-1",
            name="Personal User",
            email="user@outlook.com",
            tid="9188040d-6c67-4c5b-b112-36a304b66dad",
        )
        assert _resolve_caller_auth_level(user) == AuthLevel.MICROSOFT_ACCOUNT

    def test_no_tid_returns_microsoft_account(self):
        user = UserContext(user_id="user-2", name="No Tid", email="user@example.com")
        assert _resolve_caller_auth_level(user) == AuthLevel.MICROSOFT_ACCOUNT

    def test_organisational_account_returns_organisational(self):
        user = UserContext(
            user_id="user-3",
            name="Org User",
            email="user@contoso.com",
            tid="aaaabbbb-cccc-dddd-eeee-ffffgggghhhh",
        )
        assert _resolve_caller_auth_level(user) == AuthLevel.ORGANISATIONAL


class TestCanAccess:
    """Verify _can_access hierarchy logic."""

    def test_public_can_access_public(self):
        assert _can_access(AuthLevel.PUBLIC, AuthLevel.PUBLIC) is True

    def test_public_cannot_access_microsoft(self):
        assert _can_access(AuthLevel.MICROSOFT_ACCOUNT, AuthLevel.PUBLIC) is False

    def test_public_cannot_access_organisational(self):
        assert _can_access(AuthLevel.ORGANISATIONAL, AuthLevel.PUBLIC) is False

    def test_microsoft_can_access_public(self):
        assert _can_access(AuthLevel.PUBLIC, AuthLevel.MICROSOFT_ACCOUNT) is True

    def test_microsoft_can_access_microsoft(self):
        assert _can_access(AuthLevel.MICROSOFT_ACCOUNT, AuthLevel.MICROSOFT_ACCOUNT) is True

    def test_microsoft_cannot_access_organisational(self):
        assert _can_access(AuthLevel.ORGANISATIONAL, AuthLevel.MICROSOFT_ACCOUNT) is False

    def test_organisational_can_access_all(self):
        assert _can_access(AuthLevel.PUBLIC, AuthLevel.ORGANISATIONAL) is True
        assert _can_access(AuthLevel.MICROSOFT_ACCOUNT, AuthLevel.ORGANISATIONAL) is True
        assert _can_access(AuthLevel.ORGANISATIONAL, AuthLevel.ORGANISATIONAL) is True


def _make_agents_app() -> FastAPI:
    """Create a minimal FastAPI app with the agents router."""
    app = FastAPI()
    app.include_router(_agents_router)
    return app


class TestAgentsEndpoint:
    """Verify GET /api/v1/agents endpoint behaviour."""

    def test_returns_coordinator_plus_registered_agents(self):
        _register_hr_agent()
        _register_website_agent()

        app = _make_agents_app()

        # Mock get_current_user to return an organisational user
        org_user = UserContext(
            user_id="org-1",
            name="Org User",
            email="user@contoso.com",
            tid="some-org-tenant-id",
        )
        with patch.object(_agents_mod, "get_current_user", return_value=org_user):
            client = TestClient(app)
            response = client.get("/api/v1/agents")

        assert response.status_code == 200
        data = response.json()

        # Coordinator + hr_agent + website_agent
        assert len(data) == 3

        # Coordinator is always first
        assert data[0]["id"] == "coordinator"
        assert data[0]["accessible"] is True
        assert data[0]["enabled"] is True

        # All registered agents present
        ids = {a["id"] for a in data}
        assert "hr_agent" in ids
        assert "website_agent" in ids

    def test_accessible_flag_varies_by_auth_level(self):
        _register_hr_agent()  # auth_level = microsoft
        _register_website_agent()  # auth_level = public
        _register_it_agent()  # auth_level = organisational

        app = _make_agents_app()

        # Guest user (PUBLIC level)
        guest_user = UserContext(
            user_id="guest-1", name="Guest", email="", is_guest=True
        )
        with patch.object(_agents_mod, "get_current_user", return_value=guest_user):
            client = TestClient(app)
            response = client.get("/api/v1/agents")

        data = response.json()
        by_id = {a["id"]: a for a in data}

        # Guest can access public agents only
        assert by_id["coordinator"]["accessible"] is True
        assert by_id["website_agent"]["accessible"] is True
        assert by_id["hr_agent"]["accessible"] is False  # microsoft level
        assert by_id["it_agent"]["accessible"] is False  # organisational level

    def test_microsoft_account_accessibility(self):
        _register_hr_agent()  # auth_level = microsoft
        _register_website_agent()  # auth_level = public
        _register_it_agent()  # auth_level = organisational

        app = _make_agents_app()

        # Personal Microsoft account user
        ms_user = UserContext(
            user_id="ms-1",
            name="MS User",
            email="user@outlook.com",
            tid="9188040d-6c67-4c5b-b112-36a304b66dad",
        )
        with patch.object(_agents_mod, "get_current_user", return_value=ms_user):
            client = TestClient(app)
            response = client.get("/api/v1/agents")

        data = response.json()
        by_id = {a["id"]: a for a in data}

        assert by_id["website_agent"]["accessible"] is True
        assert by_id["hr_agent"]["accessible"] is True
        assert by_id["it_agent"]["accessible"] is False  # organisational only

    def test_organisational_user_can_access_all(self):
        _register_hr_agent()
        _register_website_agent()
        _register_it_agent()

        app = _make_agents_app()

        org_user = UserContext(
            user_id="org-1",
            name="Org User",
            email="user@contoso.com",
            tid="some-org-tenant-id",
        )
        with patch.object(_agents_mod, "get_current_user", return_value=org_user):
            client = TestClient(app)
            response = client.get("/api/v1/agents")

        data = response.json()
        assert all(a["accessible"] is True for a in data)

    def test_all_agents_have_enabled_flag(self):
        _register_hr_agent()

        app = _make_agents_app()

        user = UserContext(user_id="u1", name="User", email="u@e.com")
        with patch.object(_agents_mod, "get_current_user", return_value=user):
            client = TestClient(app)
            response = client.get("/api/v1/agents")

        data = response.json()
        assert all(a["enabled"] is True for a in data)
