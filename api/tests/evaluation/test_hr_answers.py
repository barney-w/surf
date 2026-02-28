"""HR evaluation test cases — validate answer quality, citations, and confidence."""

from __future__ import annotations

from typing import Any

import pytest

HR_EVAL_CASES: list[dict[str, Any]] = [
    # --- Annual Leave ---
    {
        "query": "How many days of annual leave do I get?",
        "expected_agent": "hr_agent",
        "must_contain": ["20 days", "annual leave"],
        "must_cite": ["Enterprise Agreement"],
        "expected_confidence": "high",
    },
    {
        "query": "Can I carry over unused annual leave to next year?",
        "expected_agent": "hr_agent",
        "must_contain": ["annual leave"],
        "must_cite_any": True,
        "expected_confidence": "high",
    },
    # --- Personal / Sick Leave ---
    {
        "query": "How many personal leave days am I entitled to?",
        "expected_agent": "hr_agent",
        "must_contain": ["10 days", "personal leave"],
        "must_cite": ["Enterprise Agreement"],
        "expected_confidence": "high",
    },
    {
        "query": "Do I need a medical certificate for sick leave?",
        "expected_agent": "hr_agent",
        "must_contain": ["medical certificate"],
        "must_cite_any": True,
        "expected_confidence": "high",
    },
    # --- Long Service Leave ---
    {
        "query": "When am I eligible for long service leave?",
        "expected_agent": "hr_agent",
        "must_contain": ["10 years", "long service leave"],
        "must_cite": ["Enterprise Agreement"],
        "expected_confidence": "high",
    },
    {
        "query": "How many weeks of long service leave do I get after 10 years?",
        "expected_agent": "hr_agent",
        "must_contain": ["13 weeks"],
        "must_cite_any": True,
        "expected_confidence": "high",
    },
    # --- Parental Leave ---
    {
        "query": "What's the process for applying for parental leave?",
        "expected_agent": "hr_agent",
        "must_contain": ["parental leave", "application"],
        "must_cite_any": True,
        "expected_confidence": "high",
    },
    {
        "query": "How much parental leave can I take?",
        "expected_agent": "hr_agent",
        "must_contain": ["12 months", "parental leave"],
        "must_cite": ["Enterprise Agreement"],
        "expected_confidence": "high",
    },
    # --- Enterprise Agreement Interpretation ---
    {
        "query": "What does the Enterprise Agreement say about pay rates?",
        "expected_agent": "hr_agent",
        "must_contain": ["Enterprise Agreement"],
        "must_cite": ["Enterprise Agreement"],
        "expected_confidence": "high",
    },
    {
        "query": "How often is the enterprise agreement reviewed?",
        "expected_agent": "hr_agent",
        "must_contain": ["3 years"],
        "must_cite_any": True,
        "expected_confidence": "high",
    },
    # --- Onboarding ---
    {
        "query": "What does the onboarding process involve for new starters?",
        "expected_agent": "hr_agent",
        "must_contain": ["onboarding"],
        "must_cite_any": True,
        "expected_confidence": "high",
    },
    # --- Performance Reviews ---
    {
        "query": "How often are performance reviews conducted?",
        "expected_agent": "hr_agent",
        "must_contain": ["annually", "performance"],
        "must_cite": ["Performance Development Policy"],
        "expected_confidence": "high",
    },
    # --- Learning & Development ---
    {
        "query": "What learning and development support is available?",
        "expected_agent": "hr_agent",
        "must_contain": ["learning", "development"],
        "must_cite_any": True,
        "expected_confidence": "high",
    },
    # --- Flexible Work ---
    {
        "query": "Can I request to work from home?",
        "expected_agent": "hr_agent",
        "must_contain": ["flexible work"],
        "must_cite_any": True,
        "expected_confidence": "high",
    },
    # --- Grievance Procedures ---
    {
        "query": "How do I lodge a formal grievance?",
        "expected_agent": "hr_agent",
        "must_contain": ["grievance"],
        "must_cite": ["Grievance Resolution Policy"],
        "expected_confidence": "high",
    },
    # --- Salary Packaging ---
    {
        "query": "What salary packaging options are available?",
        "expected_agent": "hr_agent",
        "must_contain": ["salary packaging"],
        "must_cite_any": True,
        "expected_confidence": "medium",
    },
    # --- Overtime / TOIL ---
    {
        "query": "What is the overtime pay rate?",
        "expected_agent": "hr_agent",
        "must_contain": ["150%", "overtime"],
        "must_cite": ["Enterprise Agreement"],
        "expected_confidence": "high",
    },
    {
        "query": "Can I take time off in lieu instead of overtime pay?",
        "expected_agent": "hr_agent",
        "must_contain": ["TOIL"],
        "must_cite": ["Enterprise Agreement"],
        "expected_confidence": "high",
    },
    # --- WHS ---
    {
        "query": "What are my work health and safety obligations?",
        "expected_agent": "hr_agent",
        "must_contain": ["WHS", "safe"],
        "must_cite": ["WHS Policy"],
        "expected_confidence": "high",
    },
    # --- Probation ---
    {
        "query": "How long is the probationary period?",
        "expected_agent": "hr_agent",
        "must_contain": ["6-month", "probation"],
        "must_cite_any": True,
        "expected_confidence": "high",
    },
    # --- Resignation / Separation ---
    {
        "query": "What is the process for resigning from the organisation?",
        "expected_agent": "hr_agent",
        "must_contain": ["notice", "resignation"],
        "must_cite_any": True,
        "expected_confidence": "high",
    },
    {
        "query": "What happens with my leave balance when I separate from the organisation?",
        "expected_agent": "hr_agent",
        "must_contain": ["leave", "separation"],
        "must_cite_any": True,
        "expected_confidence": "high",
    },
    # --- Leave Balance ---
    {
        "query": "How do I check my current leave balance?",
        "expected_agent": "hr_agent",
        "must_contain": ["leave balance"],
        "must_cite_any": True,
        "expected_confidence": "high",
    },
]


def _case_id(case: dict[str, Any]) -> str:
    """Generate a short test ID from the query text."""
    return case["query"][:50]


@pytest.mark.evaluation
@pytest.mark.parametrize("case", HR_EVAL_CASES, ids=[_case_id(c) for c in HR_EVAL_CASES])
async def test_hr_answer_quality(case: dict[str, Any], orchestrator_client: Any) -> None:
    """Test that HR agent answers contain expected facts and citations."""
    response = await orchestrator_client.ask(case["query"])

    # Check routing
    assert response.agent == case["expected_agent"], (
        f"Expected agent '{case['expected_agent']}' but got '{response.agent}' "
        f"for query: {case['query']}"
    )

    # Check answer contains required keywords (case-insensitive)
    if "must_contain" in case:
        answer_lower = response.answer.lower()
        for keyword in case["must_contain"]:
            assert keyword.lower() in answer_lower, (
                f"Answer missing required keyword '{keyword}' for query: {case['query']}"
            )

    # Check specific citations
    if "must_cite" in case:
        for source in case["must_cite"]:
            assert source in response.citations, (
                f"Missing citation '{source}' in {response.citations} "
                f"for query: {case['query']}"
            )

    # Check that at least one citation exists
    if case.get("must_cite_any"):
        assert len(response.citations) > 0, (
            f"Expected at least one citation for query: {case['query']}"
        )

    # Check confidence level
    if "expected_confidence" in case:
        assert response.confidence == case["expected_confidence"], (
            f"Expected confidence '{case['expected_confidence']}' but got "
            f"'{response.confidence}' for query: {case['query']}"
        )
