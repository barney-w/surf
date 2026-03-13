"""Post-response quality gate for RAG-powered agent responses.

Applies deterministic checks after the agent produces its response,
catching and remediating common failure modes:
- SEARCH_SKIPPED: Agent never called search_knowledge_base
- RESULTS_IGNORED: Agent claimed no knowledge despite search returning results
- SOURCES_MISSING: Search returned results but agent didn't populate sources
"""

import logging
import re

from src.agents._output import deduplicate_sources, extract_sources
from src.models.agent import AgentResponseModel

logger = logging.getLogger(__name__)

INFRA_ERROR_SENTINEL = "SEARCH_INFRASTRUCTURE_ERROR:"

# Phrases indicating the agent claims it couldn't find relevant documents.
_NO_KNOWLEDGE_PATTERNS = [
    r"couldn'?t find",
    r"could not find",
    r"no relevant",
    r"not available",
    r"wasn'?t able to find",
    r"don'?t have",
    r"unable to locate",
    r"no .+ found",
    r"no documents",
    r"no information",
    r"no specific",
]

_NO_KNOWLEDGE_RE = re.compile("|".join(_NO_KNOWLEDGE_PATTERNS), re.IGNORECASE)


class QualityGateResult:
    """Result of running the quality gate on an agent response."""

    __slots__ = ("check", "original", "remediated")

    def __init__(
        self,
        check: str,
        original: AgentResponseModel,
        remediated: AgentResponseModel,
    ) -> None:
        self.check = check
        self.original = original
        self.remediated = remediated


def _message_claims_no_knowledge(message: str) -> bool:
    """Return True if the message contains phrases claiming no documents were found."""
    return bool(_NO_KNOWLEDGE_RE.search(message))


def run_quality_gate(
    agent_response: AgentResponseModel,
    rag_outputs: list[str],
    routed_agent: str,
) -> QualityGateResult:
    """Run post-response quality checks with automatic remediation.

    Returns a QualityGateResult with the check name and (possibly remediated)
    response. The caller should use ``result.remediated`` as the final response.
    """
    if routed_agent == "coordinator":
        return QualityGateResult("passed", agent_response, agent_response)

    # Check 0: Infrastructure failure (highest priority — overrides everything)
    has_infra_error = any(INFRA_ERROR_SENTINEL in output for output in rag_outputs)
    if has_infra_error:
        logger.error(
            "quality_gate: search_infrastructure_error agent=%s",
            routed_agent,
            extra={
                "event": "quality_gate_result",
                "check": "search_infrastructure_error",
                "agent": routed_agent,
                "has_infra_error": True,
            },
        )
        remediated = agent_response.model_copy(update={
            "confidence": "low",
            "message": (
                "I'm sorry, I'm currently experiencing a technical issue connecting "
                "to my knowledge base and cannot search for information to answer your "
                "question reliably. Please try again in a few minutes. If the issue "
                "persists, contact the support team for assistance."
            ),
            "sources": [],
            "follow_up_suggestions": [
                "Try asking again",
                "Contact the support team",
                "Check back later",
            ],
        })
        return QualityGateResult("search_infrastructure_error", agent_response, remediated)

    # Check 1: Search was skipped entirely
    if not rag_outputs:
        logger.warning(
            "quality_gate: search_skipped agent=%s",
            routed_agent,
        )
        return QualityGateResult("search_skipped", agent_response, agent_response)

    # Determine whether the RAG tool actually returned substantive results
    # (as opposed to "No relevant documents found for this query.").
    has_substantive_results = any("=== SOURCE" in output for output in rag_outputs)

    # Check 2: Results exist but agent claims no knowledge
    if (
        has_substantive_results
        and agent_response.confidence == "low"
        and not agent_response.sources
        and _message_claims_no_knowledge(agent_response.message)
    ):
        logger.warning(
            "quality_gate: results_ignored agent=%s rag_count=%d",
            routed_agent,
            len(rag_outputs),
        )
        # Recover sources from RAG output and override confidence
        rag_text = "\n\n".join(rag_outputs)
        recovered = deduplicate_sources(extract_sources(rag_text))
        updates: dict = {"confidence": "medium"} if recovered else {}
        if recovered:
            updates["sources"] = recovered
        remediated = agent_response.model_copy(update=updates) if updates else agent_response
        return QualityGateResult("results_ignored", agent_response, remediated)

    # Check 3: Sources missing (search returned results but sources empty)
    if has_substantive_results and not agent_response.sources:
        logger.info(
            "quality_gate: sources_missing agent=%s rag_count=%d",
            routed_agent,
            len(rag_outputs),
        )
        return QualityGateResult("sources_missing", agent_response, agent_response)

    logger.info(
        "quality_gate: passed agent=%s sources=%d confidence=%s",
        routed_agent,
        len(agent_response.sources),
        agent_response.confidence,
    )
    return QualityGateResult("passed", agent_response, agent_response)
