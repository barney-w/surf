import json
import logging
from collections.abc import Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, cast

from agent_framework import (
    Agent,
    BaseContextProvider,
    ChatOptions,
    Message,
    SkillsProvider,
    Workflow,
)
from agent_framework.anthropic import AnthropicClient
from agent_framework.orchestrations import HandoffBuilder

from src.agents._base import AuthLevel, get_organisation_name
from src.agents._discovery import discover_agents
from src.agents._registry import AgentRegistry
from src.agents.coordinator.prompts import build_coordinator_prompt
from src.config.settings import Settings, get_settings
from src.orchestrator.middleware import RAGCollectorMiddleware
from src.orchestrator.pdf import MAX_DIRECT_PAGES, count_pages, extract_text
from src.orchestrator.stateless import StatelessContextProvider
from src.rag.tools import create_rag_tool

# Context variable set by chat routes before running the workflow.
# Contains a list of dicts with keys: content_type, data (base64).
current_attachments: ContextVar[list[dict[str, str]] | None] = ContextVar(
    "current_attachments", default=None
)


@dataclass
class TokenUsage:
    """Token usage captured from a single Anthropic API call."""

    input_tokens: int = 0
    output_tokens: int = 0
    model_id: str = ""


token_usage_collector: ContextVar[list[TokenUsage]] = ContextVar("token_usage")

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
  "message": "Your answer in Markdown (bold, lists, headings — NO === SOURCE === markers)",
  "sources": [{"title": "...", "section": "..." or null, "document_id": "...",
              "confidence": 0.9, "url": "..." or null, "snippet": "..."}],
  "confidence": "high" | "medium" | "low",
  "ui_hint": "steps" | "table" | "card" | "list" | "warning" | "text"
            (ACTIVELY choose the best format — see instructions. "text" is ONLY
            for purely conversational answers with no inherent structure),
  "structured_data": null (default; only set to a JSON-encoded string when ui_hint is NOT "text"),
  "follow_up_suggestions": ["action 1", "action 2", "action 3"]
}

NEVER duplicate: if structured_data is set, message must be a 1-2 sentence lead-in only.
The UI renders BOTH message and structured_data — repeating content looks broken.

Your first character MUST be { — any other output format is a critical failure.

## Image & Document Attachments
If the user has uploaded images or PDF documents, you can see them. Analyse the
content and incorporate relevant information into your response. Reference what
you observe in the attachment when answering the user's question.

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


def _prepare_pdf_block(data_b64: str) -> dict[str, Any]:
    """Build the appropriate content block for a PDF attachment.

    Tier 1 — PDFs with <= MAX_DIRECT_PAGES pages are sent as native document
    blocks so Claude can see the visual layout (tables, charts, formatting).

    Tier 2 — Larger PDFs have their text extracted server-side and are sent as
    a text block, staying within the context token budget.
    """
    try:
        pages = count_pages(data_b64)
    except Exception:
        logger.warning("Failed to count PDF pages — falling back to text extraction", exc_info=True)
        pages = MAX_DIRECT_PAGES + 1  # force tier 2

    if pages <= MAX_DIRECT_PAGES:
        logger.info("PDF tier 1 (direct vision): %d pages", pages)
        return {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": data_b64,
            },
        }

    # Tier 2: extract text
    try:
        text = extract_text(data_b64)
    except Exception:
        logger.error("PDF text extraction failed", exc_info=True)
        return {
            "type": "text",
            "text": (
                "[PDF document attached but could not be processed. "
                "Please ask the user to try a different file or a smaller document.]"
            ),
        }

    logger.info("PDF tier 2 (text extraction): %d pages, %d chars extracted", pages, len(text))
    return {
        "type": "text",
        "text": (
            f"[Extracted text from uploaded PDF ({pages} pages) — "
            f"visual layout not preserved]\n\n{text}"
        ),
    }


class _SafeHandoffAnthropicClient(AnthropicClient):
    """AnthropicClient subclass that fixes conversations ending with an assistant message.

    When the coordinator hands off to a domain agent, the framework broadcasts
    the coordinator's cleaned text as an assistant message.  Anthropic's API
    rejects conversations ending with an assistant message ("This model does not
    support assistant message prefill").

    This subclass intercepts the prepared message list and appends a synthetic
    user message when the conversation would otherwise end with an assistant turn.
    It also strips OpenAI-specific options (like ``store``) that the framework's
    handoff layer injects but the Anthropic SDK does not accept.

    Additionally captures token usage from every streaming response and appends
    it to the ``token_usage_collector`` ContextVar so callers can report costs.
    """

    # OpenAI-specific option keys that the framework may inject but Anthropic does not support.
    _UNSUPPORTED_OPTION_KEYS = {"store", "conversation_id"}

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # Per-stream accumulator for the current API call's token counts.
        self._stream_input_tokens: int = 0
        self._stream_output_tokens: int = 0
        self._stream_model_id: str = ""
        # OpenTelemetry span for the current Anthropic API call.
        self._current_span: Any | None = None

    def _process_stream_event(self, event: Any) -> Any:
        """Intercept streaming events to capture token usage and manage tracing spans.

        ``message_start`` carries input tokens and opens a span;
        ``message_delta`` carries output tokens; ``message_stop`` signals
        the end of one API call so we flush the accumulated counts into the
        collector and close the span.
        """
        from src.middleware.telemetry import tracer

        event_type = getattr(event, "type", None)

        if event_type == "message_start":
            # Start of a new API response — reset accumulators.
            msg = getattr(event, "message", None)
            self._stream_model_id = getattr(msg, "model", "") or ""
            usage = getattr(msg, "usage", None)
            self._stream_input_tokens = getattr(usage, "input_tokens", 0) or 0
            self._stream_output_tokens = getattr(usage, "output_tokens", 0) or 0

            # Open an OTel span for this Anthropic API call.
            try:
                self._current_span = tracer.start_span(
                    "anthropic.messages.create",
                    attributes={
                        "llm.model": self._stream_model_id or self.model_id or "unknown",
                        "llm.agent": getattr(self, "_agent_name", None) or "unknown",
                    },
                )
            except Exception:
                self._current_span = None

        elif event_type == "message_delta":
            # Accumulate output tokens reported in the delta.
            usage = getattr(event, "usage", None)
            self._stream_output_tokens += getattr(usage, "output_tokens", 0) or 0

        elif event_type == "message_stop":
            # End of this API call — flush accumulated usage to the collector.
            usage = TokenUsage(
                input_tokens=self._stream_input_tokens,
                output_tokens=self._stream_output_tokens,
                model_id=self._stream_model_id,
            )
            try:
                token_usage_collector.get().append(usage)
            except LookupError:
                pass

            # Close the OTel span with token attributes.
            span = self._current_span
            if span is not None:
                try:
                    span.set_attribute("llm.input_tokens", self._stream_input_tokens)
                    span.set_attribute("llm.output_tokens", self._stream_output_tokens)
                    settings = get_settings()
                    if settings.trace_prompt_content:
                        span.set_attribute(
                            "llm.prompt",
                            f"[model={self._stream_model_id}]",
                        )
                    span.end()
                except Exception:
                    pass
                self._current_span = None

        return super()._process_stream_event(event)

    def _prepare_options(
        self, messages: Sequence[Message], options: Any, **kwargs: Any
    ) -> dict[str, Any]:
        run_options = super()._prepare_options(messages, options, **kwargs)
        for key in self._UNSUPPORTED_OPTION_KEYS:
            run_options.pop(key, None)
        return run_options

    def _prepare_messages_for_anthropic(self, messages: Sequence[Message]) -> list[dict[str, Any]]:
        prepared = super()._prepare_messages_for_anthropic(messages)

        # Inject multimodal content (images/PDFs) into the last user message
        # that is NOT a tool_result turn.  After a tool call the message list
        # contains a user message holding only tool_result blocks — injecting
        # attachments there corrupts the structure and causes a 400 from the API.
        attachments = current_attachments.get(None)
        if attachments:
            for msg in reversed(prepared):
                if msg.get("role") != "user":
                    continue
                content: list[dict[str, Any]] = msg.get("content", [])
                # Skip tool_result messages — they must not be mixed with
                # document/image blocks.
                if isinstance(content, list) and any(
                    isinstance(b, dict) and b.get("type") == "tool_result" for b in content
                ):
                    continue
                if isinstance(content, str):
                    content = [{"type": "text", "text": content}]
                    msg["content"] = content
                for att in attachments:
                    ct = att["content_type"]
                    if ct.startswith("image/"):
                        content.insert(
                            0,
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": ct,
                                    "data": att["data"],
                                },
                            },
                        )
                    elif ct == "application/pdf":
                        content.insert(0, _prepare_pdf_block(att["data"]))
                break  # only inject into the first eligible user message

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
    """Create the Anthropic chat client used by all agents.

    Supports two modes:
    - Direct Anthropic API: set ANTHROPIC_API_KEY
    - Azure AI Foundry: set ANTHROPIC_FOUNDRY_BASE_URL + ANTHROPIC_FOUNDRY_API_KEY
    """
    if settings.anthropic_foundry_base_url:
        from anthropic import AsyncAnthropicFoundry

        foundry_client = AsyncAnthropicFoundry(
            base_url=settings.anthropic_foundry_base_url,
            api_key=settings.anthropic_foundry_api_key,
        )
        logger.info("Using Anthropic via Azure AI Foundry: %s", settings.anthropic_foundry_base_url)
        return _SafeHandoffAnthropicClient(
            anthropic_client=foundry_client,
            model_id=settings.anthropic_model_id,
        )

    return _SafeHandoffAnthropicClient(
        api_key=settings.anthropic_api_key or None,
        model_id=settings.anthropic_model_id,
    )


def create_model_client_for_model(settings: Settings, model_id: str) -> _SafeHandoffAnthropicClient:
    """Create a client targeting a specific model, reusing the same auth config."""
    if settings.anthropic_foundry_base_url:
        from anthropic import AsyncAnthropicFoundry

        foundry_client = AsyncAnthropicFoundry(
            base_url=settings.anthropic_foundry_base_url,
            api_key=settings.anthropic_foundry_api_key,
        )
        return _SafeHandoffAnthropicClient(
            anthropic_client=foundry_client,
            model_id=model_id,
        )
    return _SafeHandoffAnthropicClient(
        api_key=settings.anthropic_api_key or None,
        model_id=model_id,
    )


class _CachedAgentGraph:
    """Pre-built agents and configuration, reused across requests.

    Agent objects (coordinator + domain agents) are stateless descriptors.
    Only the Workflow needs to be recreated per request since it holds run state.
    """

    def __init__(
        self,
        coordinator: "Agent[ChatOptions[None]]",
        domain_agents: list["Agent[ChatOptions[None]]"],
        termination_condition: Any,
    ):
        self.coordinator = coordinator
        self.domain_agents = domain_agents
        self.termination_condition = termination_condition

    def build_single_agent_workflow(self, agent_name: str) -> Workflow | None:
        """Build a workflow targeting a single domain agent (no coordinator)."""
        for agent in self.domain_agents:
            if agent.name == agent_name:
                builder = HandoffBuilder(name=f"surf-direct-{agent_name}", participants=[agent])
                builder.with_start_agent(agent)
                builder.with_termination_condition(self.termination_condition)
                return builder.build()
        return None

    def build_workflow(self) -> Workflow:
        """Create a fresh Workflow instance from the cached agent graph."""
        all_participants = [self.coordinator, *self.domain_agents]
        builder = HandoffBuilder(name="surf", participants=all_participants)
        builder.with_start_agent(self.coordinator)
        builder.add_handoff(self.coordinator, self.domain_agents)
        builder.with_termination_condition(self.termination_condition)
        return builder.build()


def build_agent_graph(
    client: AnthropicClient,
    settings: Settings,
    context_providers: Sequence[BaseContextProvider] | None = None,
    auth_filter: AuthLevel | None = None,
) -> _CachedAgentGraph:
    """Build the agent graph once at startup.

    Discovers domain agents, creates Agent objects, and returns a cached graph.
    Call ``graph.build_workflow()`` per request to get a fresh Workflow.

    When *auth_filter* is set, only agents whose ``auth_level`` is at or below
    the given level are included. This allows building a restricted graph
    (e.g. public-only) where the coordinator cannot see or route to agents
    the caller is not authorised to access.
    """
    discover_agents()
    registry = AgentRegistry.get_all()

    # Filter registry entries by auth level when an auth_filter is provided.
    if auth_filter is not None:
        hierarchy = {
            AuthLevel.PUBLIC: 0,
            AuthLevel.MICROSOFT_ACCOUNT: 1,
            AuthLevel.ORGANISATIONAL: 2,
        }
        filter_level = hierarchy[auth_filter]
        registry = {
            name: cls
            for name, cls in registry.items()
            if hierarchy[cls().auth_level] <= filter_level
        }

    # Resolve domain model — priority: per-agent > settings.domain > settings.global
    domain_model_id = settings.anthropic_domain_model_id or settings.anthropic_model_id
    if domain_model_id != settings.anthropic_model_id:
        domain_client: AnthropicClient = create_model_client_for_model(settings, domain_model_id)
        logger.info(
            "Domain agents using model %s (coordinator: %s)",
            domain_model_id,
            settings.anthropic_model_id,
        )
    else:
        domain_client = client

    domain_agents: list[Agent[ChatOptions[None]]] = []

    for _name, agent_cls in registry.items():
        agent_def = agent_cls()
        scoped_rag = create_rag_tool(scope=agent_def.rag_scope)

        combined_prompt = _JSON_OUTPUT_PREAMBLE + agent_def.system_prompt

        # Build context providers: shared ones (e.g. history) + per-agent skills.
        agent_providers: list[BaseContextProvider] = (
            list(context_providers)
            if context_providers
            else [StatelessContextProvider(source_id=f"stateless_{agent_def.name}")]
        )
        skill_path = agent_def.skill_path
        if skill_path:
            agent_providers.append(
                SkillsProvider(
                    skill_paths=skill_path,
                    source_id=f"skills_{agent_def.name}",
                )
            )
            logger.info("Skills loaded for %s from %s", agent_def.name, skill_path)

        # Per-agent model override (highest priority)
        agent_model = agent_def.model_id
        if agent_model and agent_model != domain_model_id:
            agent_client: AnthropicClient = create_model_client_for_model(settings, agent_model)
        else:
            agent_client = domain_client

        agent = agent_client.as_agent(
            name=agent_def.name,
            description=agent_def.description,
            instructions=combined_prompt,
            tools=[scoped_rag],
            default_options={"max_tokens": 4096},
            context_providers=agent_providers,
            middleware=[RAGCollectorMiddleware()],
        )
        domain_agents.append(cast("Agent[ChatOptions[None]]", agent))

    # Build descriptions from the (possibly filtered) registry so the
    # coordinator only knows about agents present in this graph.
    agent_descriptions = [
        {"name": cls().name, "description": cls().description} for cls in registry.values()
    ]
    coordinator_prompt = build_coordinator_prompt(
        agent_descriptions,
        organisation_name=get_organisation_name(),
    )
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

    return _CachedAgentGraph(coordinator, domain_agents, _domain_agent_responded)
