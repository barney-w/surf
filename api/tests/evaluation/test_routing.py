"""Agent routing accuracy tests — verify queries reach the correct agent."""

from __future__ import annotations

from typing import Any

import pytest

ROUTING_CASES: list[dict[str, str]] = [
    # ── HR-routed queries (leave, pay, agreements, onboarding, policy) ──
    {"query": "What is my annual leave entitlement?", "expected_agent": "hr_agent"},
    {"query": "How many sick leave days do I have?", "expected_agent": "hr_agent"},
    {"query": "Tell me about long service leave eligibility", "expected_agent": "hr_agent"},
    {"query": "What parental leave options are available?", "expected_agent": "hr_agent"},
    {"query": "How does salary packaging work?", "expected_agent": "hr_agent"},
    {"query": "What is the grievance resolution process?", "expected_agent": "hr_agent"},
    {
        "query": "What are the overtime rates under the enterprise agreement?",
        "expected_agent": "hr_agent",
    },
    {"query": "How do I apply for parental leave?", "expected_agent": "hr_agent"},
    {"query": "What is the onboarding process for new employees?", "expected_agent": "hr_agent"},
    {"query": "When is the next performance review cycle?", "expected_agent": "hr_agent"},
    {
        "query": "Can you explain the enterprise agreement clause on shift allowances?",
        "expected_agent": "hr_agent",
    },
    {"query": "What learning and development courses are available?", "expected_agent": "hr_agent"},
    # ── IT-routed queries (VPN, password, software, hardware) ────────────
    {"query": "How do I reset my password?", "expected_agent": "it_agent"},
    {"query": "My VPN is not working", "expected_agent": "it_agent"},
    {"query": "How do I install Office?", "expected_agent": "it_agent"},
    {"query": "My password expired", "expected_agent": "it_agent"},
    {"query": "I can't connect to the Wi-Fi network", "expected_agent": "it_agent"},
    {"query": "How do I set up email on my phone?", "expected_agent": "it_agent"},
    {"query": "Teams keeps crashing during meetings", "expected_agent": "it_agent"},
    {"query": "I need a new laptop for my role", "expected_agent": "it_agent"},
    {"query": "Can I get a second monitor for my desk?", "expected_agent": "it_agent"},
    {"query": "My Outlook calendar is not syncing", "expected_agent": "it_agent"},
    {"query": "I forgot my MFA token and can't log in", "expected_agent": "it_agent"},
    {"query": "How do I set up the office printer?", "expected_agent": "it_agent"},
    # ── General / coordinator-answered queries ───────────────────────────
    {"query": "What time does the main office open?", "expected_agent": "coordinator"},
    {"query": "Where is the staff parking?", "expected_agent": "coordinator"},
    {"query": "What are the organisation's core values?", "expected_agent": "coordinator"},
    {"query": "Where can I find the staff cafeteria?", "expected_agent": "coordinator"},
    # ── Greetings & small talk (should NOT be routed) ────────────────────
    {"query": "Hello", "expected_agent": "coordinator"},
    {"query": "Good morning!", "expected_agent": "coordinator"},
    {"query": "Thanks for your help", "expected_agent": "coordinator"},
    {"query": "Cheers, bye!", "expected_agent": "coordinator"},
    # ── Out-of-scope queries (coordinator handles gracefully) ────────────
    {"query": "What is the weather forecast for today?", "expected_agent": "coordinator"},
    {"query": "Who won the AFL grand final?", "expected_agent": "coordinator"},
    {"query": "Can you recommend a good restaurant nearby?", "expected_agent": "coordinator"},
    # ── Ambiguous queries (could be HR or IT — coordinator clarifies) ────
    {
        "query": "I need help with my account",
        "expected_agent": "coordinator",  # should ask for clarification
    },
    {
        "query": "I'm having trouble with my access",
        "expected_agent": "coordinator",  # ambiguous — clarify first
    },
    # ── Multi-domain queries (route to PRIMARY domain) ───────────────────
    {
        "query": "I need a laptop set up for a new starter joining next Monday",
        "expected_agent": "it_agent",  # IT is primary (equipment), HR secondary
    },
    {
        "query": "The new intern needs a building pass and email account",
        "expected_agent": "it_agent",  # IT is primary (account setup)
    },
]


def _routing_id(case: dict[str, str]) -> str:
    """Generate a short test ID from the query text."""
    return case["query"][:50]


@pytest.mark.evaluation
@pytest.mark.parametrize("case", ROUTING_CASES, ids=[_routing_id(c) for c in ROUTING_CASES])
async def test_agent_routing(case: dict[str, str], orchestrator_client: Any) -> None:
    """Test that queries are routed to the correct agent."""
    response = await orchestrator_client.ask(case["query"])

    assert response.agent == case["expected_agent"], (
        f"Query '{case['query']}' was routed to '{response.agent}' "
        f"but expected '{case['expected_agent']}'"
    )
