"""Tests for coordinator prompt — verifies routing fixes are present."""

from src.agents.coordinator.prompts import build_coordinator_prompt

SAMPLE_AGENTS = [
    {"name": "it_agent", "description": "IT support"},
    {"name": "hr_agent", "description": "HR support"},
]


def _prompt() -> str:
    return build_coordinator_prompt(SAMPLE_AGENTS, organisation_name="TestOrg")


class TestClarificationLimit:
    """Fix 1: Coordinator should ask at most one clarifying question."""

    def test_one_clarifying_question_rule(self):
        prompt = _prompt()
        assert "at most **ONE** clarifying question" in prompt

    def test_prior_context_narrows_domain(self):
        prompt = _prompt()
        assert "any context in prior" in prompt
        assert "treat the domain as resolved and hand off immediately" in prompt


class TestExplicitRoutingRequests:
    """Fix 2: Explicit routing requests should be honoured immediately."""

    def test_explicit_routing_rule(self):
        prompt = _prompt()
        assert "explicitly asks to speak with" in prompt
        assert "off immediately to the matching specialist" in prompt


class TestNumberedOptionResponses:
    """Fix 3: Numbered replies should be resolved from prior options."""

    def test_numbered_option_section_exists(self):
        prompt = _prompt()
        assert "### Numbered Option Responses" in prompt

    def test_never_treat_as_new_conversation(self):
        prompt = _prompt()
        assert "NEVER treat a numbered reply as a new conversation" in prompt


class TestConfidentialitySoftened:
    """Fix 4: Confidentiality should not block acting on explicit requests."""

    def test_silently_perform_handoff(self):
        prompt = _prompt()
        assert "silently" in prompt
        assert "perform the handoff" in prompt

    def test_not_refuse_to_act(self):
        prompt = _prompt()
        assert "not that you should refuse to act" in prompt


class TestFewShotExamples:
    """Fix 5: New few-shot examples for fixed behaviours."""

    def test_explicit_routing_example(self):
        prompt = _prompt()
        assert "Can you connect me to IT support?" in prompt

    def test_prior_context_example(self):
        prompt = _prompt()
        assert "after discussing Windows upgrade" in prompt

    def test_numbered_option_example(self):
        prompt = _prompt()
        assert 'coordinator asked "1. Device login or 2. Work account?"' in prompt
