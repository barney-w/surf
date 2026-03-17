"""Evaluation rubric: judge prompt template and scoring weights."""

# Weights for the aggregate score (must sum to 1.0)
WEIGHTS = {
    "routing_correct": 0.20,
    "response_relevance": 0.30,
    "source_citation_quality": 0.15,
    "confidence_appropriateness": 0.10,
    "response_structure": 0.10,
    "no_hallucination": 0.15,
}

JUDGE_SYSTEM_PROMPT = """\
You are an expert evaluator for an AI assistant that answers workplace questions.
You will be given a user query and the assistant's response, then asked to score
the response on several dimensions.

Score each dimension honestly. A perfect score is rare — reserve 5/5 for
genuinely excellent responses. Be specific in your reasoning.
"""

JUDGE_USER_TEMPLATE = """\
## User Query
{query}

## Query Context
- Category: {category}
- Expected agent: {expected_agent}
- Actual agent: {actual_agent}
- Notes: {notes}

## Assistant Response
{response_message}

## Sources Provided
{sources_text}

## Response Metadata
- Confidence: {confidence}
- UI hint: {ui_hint}
- Follow-up suggestions count: {follow_up_count}
- RAG available: {rag_available}

---

Score the response on these dimensions. For each, provide a brief reason (1-2 sentences)
then the score.

1. **response_relevance** (1-5): Does the message directly and helpfully answer the question?
   - 5 = comprehensive, accurate, directly addresses the query
   - 3 = partially relevant, missing key details
   - 1 = irrelevant or unhelpful

2. **source_citation_quality** (1-5): Are relevant sources cited with titles and snippets?
   If RAG is unavailable, score based on whether the response appropriately acknowledges
   limited source material.
   - 5 = multiple relevant sources with clear titles and useful snippets
   - 3 = some sources but lacking detail or relevance
   - 1 = no sources when they should be present

3. **confidence_appropriateness** (1-5): Does the confidence level match the evidence?
   - 5 = confidence perfectly calibrated to available evidence
   - 3 = slightly over/under-confident
   - 1 = wildly miscalibrated (high confidence with no sources, or low with strong evidence)

4. **response_structure** (1-5): Is the ui_hint appropriate and are there exactly 3
   follow-up suggestions?
   - 5 = perfect ui_hint choice and exactly 3 relevant follow-ups
   - 3 = acceptable structure with minor issues
   - 1 = wrong ui_hint or missing/excessive follow-ups

5. **no_hallucination** (bool as 1 or 0): Is the response free from fabricated information?
   Look for invented policy details, fake document references, or claims not supported
   by the provided sources. Score 1 if clean, 0 if hallucination detected.

Respond in this exact JSON format (no markdown fencing):
{{
  "response_relevance": {{"score": <int 1-5>, "reason": "<str>"}},
  "source_citation_quality": {{"score": <int 1-5>, "reason": "<str>"}},
  "confidence_appropriateness": {{"score": <int 1-5>, "reason": "<str>"}},
  "response_structure": {{"score": <int 1-5>, "reason": "<str>"}},
  "no_hallucination": {{"score": <int 0-1>, "reason": "<str>"}}
}}
"""


def compute_weighted_score(
    *,
    routing_correct: bool,
    response_relevance: int,
    source_citation_quality: int,
    confidence_appropriateness: int,
    response_structure: int,
    no_hallucination: bool,
) -> float:
    """Compute a 0-100 weighted score from individual dimension scores."""
    normalised = {
        "routing_correct": 1.0 if routing_correct else 0.0,
        "response_relevance": response_relevance / 5.0,
        "source_citation_quality": source_citation_quality / 5.0,
        "confidence_appropriateness": confidence_appropriateness / 5.0,
        "response_structure": response_structure / 5.0,
        "no_hallucination": 1.0 if no_hallucination else 0.0,
    }
    return round(sum(normalised[k] * WEIGHTS[k] for k in WEIGHTS) * 100, 1)
