import json
import logging
from collections.abc import Sequence
from typing import Any, cast

from agent_framework import Agent, BaseContextProvider, ChatOptions, Message, Workflow
from agent_framework.anthropic import AnthropicClient
from agent_framework.orchestrations import HandoffBuilder

from src.agents._discovery import discover_agents
from src.agents._registry import AgentRegistry
from src.agents.coordinator.prompts import build_coordinator_prompt
from src.config.settings import Settings
from src.orchestrator.stateless import StatelessContextProvider
from src.rag.tools import create_rag_tool

logger = logging.getLogger(__name__)

# JSON output instructions prepended to every domain agent's prompt.
# Without response_format enforcement (Anthropic doesn't support it in handoff
# conversations), the model needs explicit instructions to output valid JSON.
_JSON_OUTPUT_PREAMBLE = """\
## CRITICAL: Output Format — ABSOLUTE REQUIREMENT
Your ENTIRE response must be a single JSON object. Start with { and end with }.
Do NOT write any text, commentary, or explanation before or after the JSON.
Do NOT use markdown fences. Do NOT narrate your thinking.
If you need to search first, call the search tool, then respond with ONLY JSON.

The JSON object must match this schema:
{
  "message": "Your answer (plain text, NO === SOURCE === markers)",
  "sources": [{"title": "...", "section": "..." or null, "document_id": "...",
              "confidence": 0.9, "url": "..." or null, "snippet": "..."}],
  "confidence": "high" | "medium" | "low",
  "ui_hint": "text" (default; only use "table"/"list"/"steps"/"card"/"warning"
            when the answer is SIGNIFICANTLY clearer in that format),
  "structured_data": null (default; only set to a JSON-encoded string when ui_hint is NOT "text"),
  "follow_up_suggestions": ["action 1", "action 2", "action 3"]
}

NEVER duplicate: if structured_data is set, message must be a 1-2 sentence lead-in only.
The UI renders BOTH message and structured_data — repeating content looks broken.

Your first character MUST be { — any other output format is a critical failure.

"""


def _domain_agent_responded(messages: list[Message]) -> bool:
    """Terminate the workflow once a domain agent has produced a structured response.

    Domain agents emit AgentResponseModel JSON (with 'message' and 'confidence'
    keys).  Stopping here prevents the framework from looping back to the
    coordinator after the domain agent completes with no outgoing handoffs.
    """
    for msg in reversed(messages):
        if msg.role != "assistant":
            continue
        # Skip coordinator messages — only domain agent JSON should terminate.
        author = getattr(msg, "author_name", None) or ""
        if author == "coordinator":
            continue
        text = msg.text or ""
        if '"confidence"' in text and '"message"' in text:
            try:
                data = json.loads(text.strip())
                if "message" in data and "confidence" in data:
                    return True
            except (json.JSONDecodeError, ValueError):
                pass
    return False


class _SafeHandoffAnthropicClient(AnthropicClient):
    """AnthropicClient subclass that fixes conversations ending with an assistant message.

    When the coordinator hands off to a domain agent, the framework broadcasts
    the coordinator's cleaned text as an assistant message.  Anthropic's API
    rejects conversations ending with an assistant message ("This model does not
    support assistant message prefill").

    This subclass intercepts the prepared message list and appends a synthetic
    user message when the conversation would otherwise end with an assistant turn.
    """

    def _prepare_messages_for_anthropic(self, messages: Sequence[Message]) -> list[dict[str, Any]]:
        prepared = super()._prepare_messages_for_anthropic(messages)
        if prepared and prepared[-1].get("role") == "assistant":
            logger.debug(
                "Appending synthetic user message to fix assistant-terminated conversation",
            )
            prepared.append(
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "Please respond to the above."}],
                }
            )
        return prepared


def create_model_client(settings: Settings) -> _SafeHandoffAnthropicClient:
    """Create the Anthropic chat client used by all agents."""
    return _SafeHandoffAnthropicClient(
        api_key=settings.anthropic_api_key or None,
        model_id=settings.anthropic_model_id,
    )


def build_orchestrator(
    client: AnthropicClient,
    context_providers: Sequence[BaseContextProvider] | None = None,
) -> Workflow:
    """Build the complete HandoffBuilder workflow.

    Each domain agent has its own scoped RAG tool and JSON output instructions.
    The coordinator routes to domain agents, which search and respond in one step.

    Flow: coordinator → domain_agent (calls RAG tool, generates JSON response)
    """
    discover_agents()
    registry = AgentRegistry.get_all()

    domain_agents: dict[str, Agent] = {}

    for name, agent_cls in registry.items():
        agent_def = agent_cls()
        scoped_rag = create_rag_tool(scope=agent_def.rag_scope)

        # Combine JSON output preamble with the domain-specific prompt.
        combined_prompt = _JSON_OUTPUT_PREAMBLE + agent_def.system_prompt

        agent = client.as_agent(
            name=agent_def.name,
            description=agent_def.description,
            instructions=combined_prompt,
            tools=[scoped_rag],
            default_options={"max_tokens": 4096},
            context_providers=list(context_providers)
            if context_providers
            else [StatelessContextProvider(source_id=f"stateless_{agent_def.name}")],
        )
        domain_agents[name] = cast("Agent[ChatOptions[None]]", agent)

    # Coordinator has NO tools — its sole job is to route to domain agents.
    coordinator_prompt = build_coordinator_prompt(AgentRegistry.agent_descriptions())
    coordinator = cast(
        "Agent[ChatOptions[None]]",
        client.as_agent(
            name="coordinator",
            description="Routes user queries to the correct specialist agent",
            instructions=coordinator_prompt,
            tools=[],
            default_options={},
            context_providers=context_providers
            or [StatelessContextProvider(source_id="stateless_coordinator")],
        ),
    )

    all_participants = [coordinator, *list(domain_agents.values())]
    builder = HandoffBuilder(name="surf", participants=all_participants)
    builder.with_start_agent(coordinator)

    # Coordinator hands off to domain agents.
    builder.add_handoff(coordinator, list(domain_agents.values()))

    # Stop as soon as a domain agent emits its structured JSON response.
    builder.with_termination_condition(_domain_agent_responded)

    return builder.build()
