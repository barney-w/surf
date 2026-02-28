import json
from collections.abc import Sequence

from agent_framework import Agent, BaseContextProvider, Message, Workflow
from agent_framework.anthropic import AnthropicClient
from agent_framework.orchestrations import HandoffBuilder

from src.agents._discovery import discover_agents
from src.agents._registry import AgentRegistry
from src.agents.coordinator.prompts import build_coordinator_prompt
from src.config.settings import Settings
from src.models.agent import AgentResponseModel
from src.orchestrator.stateless import StatelessContextProvider
from src.rag.tools import create_rag_tool

# Suffix appended to the synthesize-phase agent name so it's distinct from the
# search-phase agent that the coordinator routes to.
SYNTHESIZE_SUFFIX = "_response"

# Instructions for the search-phase agent.  The instructions direct the agent
# to call the RAG tool and then echo results + transfer.  No tool_choice
# constraint is needed — the instructions are sufficient.
_SEARCH_AGENT_INSTRUCTIONS = """\
You are a knowledge-retrieval assistant. Follow these two steps exactly:

Step 1 — Search: Call `search_knowledge_base` with the user's query.
If the user's message is a follow-up or contains pronouns (it, that, this, they),
use conversation history to understand what they're referring to. Reformulate your
search query to include the specific topic from prior messages rather than searching
for the vague pronoun.

Step 2 — Transfer with context:
- Write your text response as the FULL, VERBATIM content returned by the search tool.
  Copy every character exactly — the response agent needs the complete search results.
- Then call the transfer/handoff tool to pass control to the response agent.

Do NOT call search a second time. Do NOT answer the question yourself.\
"""


def _domain_agent_responded(messages: list[Message]) -> bool:
    """Terminate the workflow once a synthesize agent has produced a structured response.

    Synthesize agents always emit AgentResponseModel JSON (with 'message' and
    'confidence' keys).  Stopping here prevents the framework from looping back
    to the coordinator after the synthesize agent completes with no outgoing
    handoffs.
    """
    for msg in reversed(messages):
        if msg.role != "assistant":
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


def create_model_client(settings: Settings) -> AnthropicClient:
    """Create the Anthropic chat client used by all agents."""
    return AnthropicClient(
        api_key=settings.anthropic_api_key or None,
        model_id=settings.anthropic_model_id,
    )


def build_orchestrator(
    client: AnthropicClient,
    context_providers: Sequence[BaseContextProvider] | None = None,
) -> Workflow:
    """Build the complete HandoffBuilder workflow.

    Each domain agent is split into two framework agents:
      - Search agent  ({name})          — calls RAG, echoes results, then transfers
      - Synthesize agent ({name}_response) — response_format=AgentResponseModel, no tools

    Flow: coordinator → search agent → synthesize agent
    """
    discover_agents()
    registry = AgentRegistry.get_all()

    search_agents: dict[str, Agent] = {}
    synthesize_agents: dict[str, Agent] = {}

    for name, agent_cls in registry.items():
        agent_def = agent_cls()
        scoped_rag = create_rag_tool(scope=agent_def.rag_scope)
        synthesize_name = f"{name}{SYNTHESIZE_SUFFIX}"

        # Phase 1: search agent — calls RAG tool, echoes results, then transfers
        search_agent = client.as_agent(
            name=agent_def.name,
            description=agent_def.description,
            instructions=_SEARCH_AGENT_INSTRUCTIONS,
            tools=[scoped_rag],
            default_options={},
            context_providers=list(context_providers)
            if context_providers
            else [StatelessContextProvider(source_id=f"stateless_{agent_def.name}")],
        )
        search_agents[name] = search_agent

        # Phase 2: synthesize agent — generates the structured JSON response
        synthesize_agent = client.as_agent(
            name=synthesize_name,
            description=f"Generates the final structured response for {agent_def.name}",
            instructions=agent_def.system_prompt,
            tools=[],
            default_options={"response_format": AgentResponseModel, "max_tokens": 4096},
            context_providers=list(context_providers)
            if context_providers
            else [StatelessContextProvider(source_id=f"stateless_{synthesize_name}")],
        )
        synthesize_agents[synthesize_name] = synthesize_agent

    # Coordinator has NO tools — its sole job is to route to domain agents.
    # Giving it a RAG tool tempts the LLM into searching and answering domain
    # questions itself instead of handing off to the specialist agent.
    coordinator_prompt = build_coordinator_prompt(AgentRegistry.agent_descriptions())
    coordinator = client.as_agent(
        name="coordinator",
        description="Routes user queries to the correct specialist agent",
        instructions=coordinator_prompt,
        tools=[],
        default_options={},
        context_providers=context_providers
        or [StatelessContextProvider(source_id="stateless_coordinator")],
    )

    all_participants = [
        coordinator,
        *list(search_agents.values()),
        *list(synthesize_agents.values()),
    ]
    builder = HandoffBuilder(name="surf", participants=all_participants)
    builder.with_start_agent(coordinator)

    # Coordinator hands off to search agents (one per domain).
    # Search agents are named the same as the domain agents the coordinator knows about,
    # so the coordinator's routing logic is unchanged.
    builder.add_handoff(coordinator, list(search_agents.values()))

    # Each search agent hands off to its corresponding synthesize agent.
    for name, search_agent in search_agents.items():
        builder.add_handoff(search_agent, [synthesize_agents[f"{name}{SYNTHESIZE_SUFFIX}"]])

    # Stop as soon as a synthesize agent emits its structured response.
    builder.with_termination_condition(_domain_agent_responded)

    return builder.build()
