"""Unified post-processing pipeline for agent responses."""

import logging

from src.agents._output import deduplicate_sources, extract_sources, strip_source_urls
from src.agents._registry import AgentRegistry
from src.middleware.langfuse_utils import get_langfuse
from src.middleware.telemetry import quality_gate_triggers
from src.models.agent import AgentResponseModel
from src.rag.quality_gate import QualityGateResult, run_quality_gate

logger = logging.getLogger(__name__)


async def process_agent_response(
    agent_response: AgentResponseModel,
    rag_outputs: list[str],
    routed_agent: str,
) -> tuple[AgentResponseModel, QualityGateResult]:
    """Run all post-processing steps on an agent response.

    Steps:
    1. Quality gate (search-skipped, results-ignored checks)
    2. Source recovery from RAG outputs
    3. Per-agent post-processing (URL stripping)

    Returns the processed response and the quality gate result.
    """
    # 1. Quality gate
    gate_result = run_quality_gate(agent_response, rag_outputs, routed_agent)
    agent_response = gate_result.remediated
    if gate_result.check != "passed":
        logger.warning(
            "quality_gate_triggered check=%s agent=%s rag_count=%d source_count=%d confidence=%s",
            gate_result.check,
            routed_agent,
            len(rag_outputs),
            len(agent_response.sources),
            agent_response.confidence,
        )

    quality_gate_triggers.add(1, {"check": gate_result.check, "agent": routed_agent})

    # Langfuse trace scoring
    langfuse = get_langfuse()
    if langfuse:
        try:
            langfuse.score_current_trace(
                name="quality_gate",
                value=gate_result.check,
                data_type="CATEGORICAL",
            )
            langfuse.score_current_trace(
                name="confidence",
                value=agent_response.confidence,
                data_type="CATEGORICAL",
            )
            langfuse.score_current_trace(
                name="source_count",
                value=float(len(agent_response.sources)),
                data_type="NUMERIC",
            )
        except Exception:
            pass  # Langfuse failures must never affect response pipeline

    # 2. Source recovery
    if not agent_response.sources and rag_outputs:
        rag_text = "\n\n".join(rag_outputs)
        recovered = deduplicate_sources(extract_sources(rag_text))
        if recovered:
            agent_response = agent_response.model_copy(update={"sources": recovered})
            logger.info("injected %d recovered sources into response", len(recovered))

    # 3. Per-agent post-processing
    agent_cls = AgentRegistry.get(routed_agent)
    if agent_cls is not None:
        agent_def = agent_cls()
        if agent_def.strip_source_urls:
            agent_response = strip_source_urls(agent_response)

    return agent_response, gate_result
