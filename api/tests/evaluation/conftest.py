"""Shared fixtures for evaluation tests."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest


@dataclass
class AgentResponse:
    """Simulated response from an agent via the orchestrator."""

    agent: str
    answer: str
    citations: list[str] = field(default_factory=list)
    confidence: str = "high"


class MockOrchestratorClient:
    """Mock orchestrator client that simulates agent routing and responses.

    In a real integration-test scenario this would call the live orchestrator.
    For evaluation tests we return deterministic responses so that test-case
    assertions on structure and routing can be validated without a running
    backend.
    """

    # ---------------------------------------------------------------------------
    # Pre-canned responses keyed by substring match against the query.
    # Each entry maps a keyword to (agent, answer_snippet, citations, confidence).
    # ---------------------------------------------------------------------------
    _RESPONSES: dict[str, tuple[str, str, list[str], str]] = {
        "annual leave": (
            "hr_agent",
            "Employees are entitled to 20 days (4 weeks) of annual leave per year "
            "under the Enterprise Agreement. Annual leave accrues progressively and "
            "can be taken with manager approval.",
            ["Enterprise Agreement", "Leave Policy"],
            "high",
        ),
        "personal leave": (
            "hr_agent",
            "You are entitled to 10 days of personal/carer's leave per year. "
            "Personal leave covers sick leave and carer's leave. A medical "
            "certificate may be required for absences exceeding 2 consecutive days.",
            ["Enterprise Agreement", "Leave Policy"],
            "high",
        ),
        "sick leave": (
            "hr_agent",
            "Sick leave falls under personal leave entitlements. You receive "
            "10 days of personal leave per year which covers illness. A medical "
            "certificate is required for absences longer than 2 days.",
            ["Enterprise Agreement", "Leave Policy"],
            "high",
        ),
        "long service leave": (
            "hr_agent",
            "After 10 years of continuous service you are entitled to 13 weeks "
            "(65 business days) of long service leave. Pro-rata access may be "
            "available after 7 years in some circumstances.",
            ["Enterprise Agreement", "Long Service Leave Act"],
            "high",
        ),
        "parental leave": (
            "hr_agent",
            "Eligible employees may apply for up to 12 months of parental leave. "
            "The application process involves submitting a parental leave "
            "application form to your manager at least 10 weeks before the "
            "expected date.",
            ["Enterprise Agreement", "Parental Leave Policy"],
            "high",
        ),
        "agreement": (
            "hr_agent",
            "The enterprise agreement outlines terms and "
            "conditions of employment including pay, leave, and working hours. "
            "The current agreement is reviewed every 3 years.",
            ["Enterprise Agreement"],
            "high",
        ),
        "enterprise agreement": (
            "hr_agent",
            "The Enterprise Agreement covers wages, leave entitlements, "
            "working conditions, and dispute resolution for all employees.",
            ["Enterprise Agreement"],
            "high",
        ),
        "onboarding": (
            "hr_agent",
            "New employees complete a structured onboarding program during their "
            "first two weeks. This includes orientation, WHS induction, IT setup, "
            "and meeting your team.",
            ["Onboarding Guide", "HR Procedures Manual"],
            "high",
        ),
        "performance review": (
            "hr_agent",
            "Performance reviews are conducted annually using the organisation's "
            "performance development framework. Mid-year check-ins are also "
            "encouraged. Reviews cover goals, competencies, and development plans.",
            ["Performance Development Policy"],
            "high",
        ),
        "learning": (
            "hr_agent",
            "The organisation supports learning and development through study "
            "assistance, conference attendance, and internal training programs. "
            "Eligible employees may receive up to 5 days of study leave per year.",
            ["Learning & Development Policy", "Enterprise Agreement"],
            "high",
        ),
        "flexible work": (
            "hr_agent",
            "Flexible work arrangements are available including part-time work, "
            "job sharing, compressed hours, and working from home. Requests are "
            "assessed on operational requirements.",
            ["Flexible Work Policy", "Enterprise Agreement"],
            "high",
        ),
        "grievance": (
            "hr_agent",
            "The grievance procedure involves raising concerns with your "
            "supervisor first, then escalating to HR if unresolved. Formal "
            "grievances are handled within 21 days.",
            ["Grievance Resolution Policy", "Enterprise Agreement"],
            "high",
        ),
        "salary packaging": (
            "hr_agent",
            "Employees can access salary packaging for superannuation, "
            "novated leases, and other approved items. Contact the salary "
            "packaging provider to arrange.",
            ["Salary Packaging Policy"],
            "medium",
        ),
        "overtime": (
            "hr_agent",
            "Overtime must be pre-approved by your manager. It is paid at "
            "150% for the first 2 hours and 200% thereafter. Alternatively, "
            "you may elect to take Time Off In Lieu (TOIL).",
            ["Enterprise Agreement"],
            "high",
        ),
        "toil": (
            "hr_agent",
            "Time Off In Lieu (TOIL) can be taken instead of overtime payment. "
            "TOIL accrues at the equivalent overtime rate and must be taken "
            "within 3 months.",
            ["Enterprise Agreement"],
            "high",
        ),
        "whs": (
            "hr_agent",
            "The organisation's WHS obligations include providing a safe workplace, "
            "conducting risk assessments, and ensuring staff complete WHS "
            "induction training. Report hazards to your supervisor immediately.",
            ["WHS Policy", "WHS Act"],
            "high",
        ),
        "work health": (
            "hr_agent",
            "Work health and safety is everyone's responsibility. Report "
            "incidents via the online portal within 24 hours.",
            ["WHS Policy"],
            "high",
        ),
        "probation": (
            "hr_agent",
            "New employees serve a 6-month probationary period. Performance "
            "is reviewed at 3 months and 6 months. Extensions may apply in "
            "certain circumstances.",
            ["Employment Conditions Policy", "Enterprise Agreement"],
            "high",
        ),
        "resignation": (
            "hr_agent",
            "To resign, submit a written notice to your manager. The standard "
            "notice period is 4 weeks. Exit interviews are conducted by HR.",
            ["Employment Conditions Policy"],
            "high",
        ),
        "separation": (
            "hr_agent",
            "The separation process includes returning organisation property, "
            "completing an exit interview, and final pay processing. Final "
            "pay includes any outstanding leave entitlements.",
            ["Employment Conditions Policy", "Leave Policy"],
            "high",
        ),
        "reset my password": (
            "coordinator",
            "To reset your password, visit the IT self-service portal at "
            "https://selfservice.example.com or contact the IT helpdesk.",
            [],
            "high",
        ),
        "gym": (
            "coordinator",
            "The staff gym is open from 6:00 AM to 8:00 PM on weekdays.",
            [],
            "medium",
        ),
        "parking": (
            "coordinator",
            "Staff parking is available in the basement. Swipe your access card at the boom gate.",
            [],
            "medium",
        ),
        "weather": (
            "coordinator",
            "I can help with work-related questions. For weather information "
            "please check the Bureau of Meteorology website.",
            [],
            "low",
        ),
        "leave balance": (
            "hr_agent",
            "You can check your leave balance in the HR self-service portal "
            "under My Leave. Annual leave accrues at 20 days per year.",
            ["Enterprise Agreement", "Leave Policy"],
            "high",
        ),
    }

    async def ask(self, query: str) -> AgentResponse:
        """Route the query and return a simulated agent response."""
        query_lower = query.lower()
        for keyword, (agent, answer, citations, confidence) in self._RESPONSES.items():
            if keyword in query_lower:
                return AgentResponse(
                    agent=agent,
                    answer=answer,
                    citations=citations,
                    confidence=confidence,
                )
        # Default fallback
        return AgentResponse(
            agent="coordinator",
            answer="I'm not sure how to help with that. Please contact the relevant team.",
            citations=[],
            confidence="low",
        )


@pytest.fixture
def orchestrator_client() -> MockOrchestratorClient:
    """Provide a mock orchestrator client for evaluation tests."""
    return MockOrchestratorClient()
