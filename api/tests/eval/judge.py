"""LLM-as-judge scoring via Claude API."""

from __future__ import annotations

import json
import logging
from typing import Any

from .rubric import JUDGE_SYSTEM_PROMPT, JUDGE_USER_TEMPLATE

logger = logging.getLogger(__name__)

JUDGE_MODEL = "claude-sonnet-4-6"


def _format_sources(sources: list[dict[str, Any]]) -> str:
    if not sources:
        return "(no sources)"
    parts = []
    for i, s in enumerate(sources, 1):
        title = s.get("title", "Untitled")
        doc_id = s.get("document_id", "?")
        snippet = s.get("snippet", "")
        confidence = s.get("confidence", "?")
        parts.append(f"{i}. [{title}] (id={doc_id}, confidence={confidence})\n   {snippet}")
    return "\n".join(parts)


def judge_response(
    *,
    client: object,
    query: str,
    category: str,
    expected_agent: str,
    actual_agent: str,
    notes: str,
    response_message: str,
    sources: list[dict[str, Any]],
    confidence: str,
    ui_hint: str,
    follow_up_count: int,
    rag_available: bool,
) -> dict[str, Any]:
    """Call Claude to score a single eval response. Returns parsed judge scores."""
    import anthropic

    assert isinstance(client, anthropic.Anthropic)

    user_prompt = JUDGE_USER_TEMPLATE.format(
        query=query,
        category=category,
        expected_agent=expected_agent,
        actual_agent=actual_agent,
        notes=notes,
        response_message=response_message,
        sources_text=_format_sources(sources),
        confidence=confidence,
        ui_hint=ui_hint,
        follow_up_count=follow_up_count,
        rag_available=rag_available,
    )

    message = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=1024,
        system=JUDGE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw = message.content[0].text  # type: ignore[union-attr]

    # Strip markdown fences if the model wraps them
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    try:
        scores: dict[str, Any] = json.loads(text)
    except json.JSONDecodeError:
        logger.error("Judge returned invalid JSON: %s", raw[:500])
        scores = {
            "response_relevance": {"score": 3, "reason": "Judge parse failure"},
            "source_citation_quality": {"score": 3, "reason": "Judge parse failure"},
            "confidence_appropriateness": {"score": 3, "reason": "Judge parse failure"},
            "response_structure": {"score": 3, "reason": "Judge parse failure"},
            "no_hallucination": {
                "score": 1,
                "reason": "Judge parse failure — assumed clean",
            },
        }

    return scores
