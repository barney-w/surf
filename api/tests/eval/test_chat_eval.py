"""Parametrised eval tests — one test per query in dataset.json.

Each test sends a query to the live API, checks for HTTP success,
then uses an LLM judge to score the response quality. Tests do NOT
hard-fail on low scores; only HTTP errors cause failures.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from .judge import judge_response
from .rubric import compute_weighted_score

if TYPE_CHECKING:
    import anthropic
    import httpx

    from .conftest import ResultsCollector

logger = logging.getLogger(__name__)

# Load dataset for parametrisation at module level
_DATASET_PATH = Path(__file__).parent / "dataset.json"
with open(_DATASET_PATH) as _f:
    _DATASET: list[dict[str, Any]] = json.load(_f)

_IDS = [q["id"] for q in _DATASET]


@pytest.mark.parametrize("query_data", _DATASET, ids=_IDS)
def test_chat_eval(
    query_data: dict[str, Any],
    api_client: httpx.Client,
    anthropic_client: anthropic.Anthropic,
    rag_available: bool,
    results_collector: ResultsCollector,
) -> None:
    """Send a query to the API and score the response."""
    query_id = query_data["id"]
    query = query_data["query"]
    expected_agent = query_data["expected_agent"]
    category = query_data["category"]
    notes = query_data["notes"]

    resp = api_client.post(
        "/api/v1/chat",
        json={"message": query},
    )

    assert resp.status_code == 200, (
        f"[{query_id}] API returned {resp.status_code}: {resp.text[:300]}"
    )

    data = resp.json()
    actual_agent = data.get("agent", "unknown")
    response_obj = data.get("response", {})
    response_message = response_obj.get("message", "")
    sources = response_obj.get("sources", [])
    confidence = response_obj.get("confidence", "unknown")
    ui_hint = response_obj.get("ui_hint", "unknown")
    follow_ups = response_obj.get("follow_up_suggestions", [])

    routing_correct = actual_agent == expected_agent

    has_leaked_markers = any(
        marker in response_message for marker in ["[source:", "[doc:", "\u3010", "\u3011"]
    )

    judge_scores = judge_response(
        client=anthropic_client,
        query=query,
        category=category,
        expected_agent=expected_agent,
        actual_agent=actual_agent,
        notes=notes,
        response_message=response_message,
        sources=sources,
        confidence=confidence,
        ui_hint=ui_hint,
        follow_up_count=len(follow_ups),
        rag_available=rag_available,
    )

    response_relevance = judge_scores["response_relevance"]["score"]
    source_citation_quality = judge_scores["source_citation_quality"]["score"]
    confidence_appropriateness = judge_scores["confidence_appropriateness"]["score"]
    response_structure = judge_scores["response_structure"]["score"]
    judge_no_hallucination = judge_scores["no_hallucination"]["score"]

    no_hallucination = bool(judge_no_hallucination) and not has_leaked_markers

    weighted = compute_weighted_score(
        routing_correct=routing_correct,
        response_relevance=response_relevance,
        source_citation_quality=source_citation_quality,
        confidence_appropriateness=confidence_appropriateness,
        response_structure=response_structure,
        no_hallucination=no_hallucination,
    )

    result: dict[str, Any] = {
        "id": query_id,
        "query": query,
        "category": category,
        "expected_agent": expected_agent,
        "actual_agent": actual_agent,
        "routing_correct": routing_correct,
        "response_relevance": response_relevance,
        "source_citation_quality": source_citation_quality,
        "confidence_appropriateness": confidence_appropriateness,
        "response_structure": response_structure,
        "no_hallucination": no_hallucination,
        "has_leaked_markers": has_leaked_markers,
        "weighted_score": weighted,
        "response_message": response_message[:500],
        "source_count": len(sources),
        "confidence": confidence,
        "ui_hint": ui_hint,
        "follow_up_count": len(follow_ups),
        "judge_reasoning": {k: v.get("reason", "") for k, v in judge_scores.items()},
    }
    results_collector.add(result)

    status = "PASS" if routing_correct else "MISS"
    logger.info(
        "[%s] %s routing=%s score=%.1f relevance=%d/5",
        query_id,
        status,
        actual_agent,
        weighted,
        response_relevance,
    )
