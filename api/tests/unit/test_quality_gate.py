"""Tests for the post-response RAG quality gate."""

from src.models.agent import AgentResponseModel, Source
from src.rag.quality_gate import _message_claims_no_knowledge, run_quality_gate


def _make_response(
    message: str = "Here is the answer.",
    sources: list[Source] | None = None,
    confidence: str = "high",
) -> AgentResponseModel:
    """Helper to build an AgentResponseModel with sensible defaults."""
    return AgentResponseModel(
        message=message,
        sources=sources or [],
        confidence=confidence,
        ui_hint="text",
        follow_up_suggestions=[],
    )


def _make_source(
    title: str = "Test Doc",
    document_id: str = "doc-1",
    confidence: float = 0.85,
) -> Source:
    return Source(title=title, document_id=document_id, confidence=confidence)


def _make_rag_block(
    index: int = 1,
    title: str = "Test Document",
    document_id: str = "doc-1",
    relevance: float = 0.85,
    snippet: str = "Some relevant content here.",
) -> str:
    """Build a === SOURCE N === block matching the format used by tools.py."""
    return (
        f"=== SOURCE {index} ===\n"
        f'title: "{title}"\n'
        f'document_id: "{document_id}"\n'
        f"relevance: {relevance}\n"
        f'snippet: "{snippet}"\n'
        f"CONTENT:\nSome content.\n"
        f"=== END SOURCE {index} ==="
    )


class TestMessageClaimsNoKnowledge:
    """Tests for _message_claims_no_knowledge helper."""

    def test_detects_couldnt_find(self) -> None:
        assert _message_claims_no_knowledge("I couldn't find any relevant documents") is True

    def test_detects_not_available(self) -> None:
        assert (
            _message_claims_no_knowledge("That information is not available in the knowledge base")
            is True
        )

    def test_detects_wasnt_able(self) -> None:
        assert _message_claims_no_knowledge("I wasn't able to find a Code of Conduct") is True

    def test_normal_response_not_flagged(self) -> None:
        assert _message_claims_no_knowledge("The Code of Conduct states that...") is False

    def test_case_insensitive(self) -> None:
        assert _message_claims_no_knowledge("NO RELEVANT documents") is True


class TestRunQualityGate:
    """Tests for the main quality gate function."""

    def test_coordinator_always_passes(self) -> None:
        response = _make_response()
        result = run_quality_gate(response, rag_outputs=[], routed_agent="coordinator")
        assert result.check == "passed"
        assert result.remediated is response

    def test_search_skipped_detected(self) -> None:
        response = _make_response()
        result = run_quality_gate(response, rag_outputs=[], routed_agent="hr_agent")
        assert result.check == "search_skipped"
        assert result.remediated is response

    def test_results_ignored_detected_and_remediated(self) -> None:
        rag_block = _make_rag_block(
            index=1,
            title="Leave Policy",
            document_id="leave-policy-v2",
            relevance=0.9,
            snippet="Annual leave entitlements are outlined below.",
        )
        response = _make_response(
            message="I couldn't find any relevant documents about leave.",
            sources=[],
            confidence="low",
        )
        result = run_quality_gate(response, rag_outputs=[rag_block], routed_agent="hr_agent")

        assert result.check == "results_ignored"
        # Original should be unchanged
        assert result.original.confidence == "low"
        assert result.original.sources == []
        # Remediated should have recovered sources and upgraded confidence
        assert result.remediated.confidence == "medium"
        assert len(result.remediated.sources) == 1
        assert result.remediated.sources[0].document_id == "leave-policy-v2"

    def test_sources_missing_detected(self) -> None:
        rag_block = _make_rag_block()
        response = _make_response(
            message="Here is some helpful information about our policies.",
            sources=[],
            confidence="high",
        )
        result = run_quality_gate(response, rag_outputs=[rag_block], routed_agent="hr_agent")
        assert result.check == "sources_missing"

    def test_healthy_response_passes(self) -> None:
        rag_block = _make_rag_block()
        source = _make_source()
        response = _make_response(
            message="The policy states the following details.",
            sources=[source],
            confidence="high",
        )
        result = run_quality_gate(response, rag_outputs=[rag_block], routed_agent="hr_agent")
        assert result.check == "passed"
        assert result.remediated is response

    def test_results_ignored_not_triggered_with_sources(self) -> None:
        """Even with low confidence, if sources are present it should not be results_ignored."""
        rag_block = _make_rag_block()
        source = _make_source()
        response = _make_response(
            message="I couldn't find much, but here is something.",
            sources=[source],
            confidence="low",
        )
        result = run_quality_gate(response, rag_outputs=[rag_block], routed_agent="hr_agent")
        # Should pass because sources are present
        assert result.check != "results_ignored"
        assert result.check == "passed"

    def test_no_results_message_not_treated_as_substantive(self) -> None:
        """When rag_outputs only contains 'No relevant documents found', it's not substantive."""
        response = _make_response(
            message="I wasn't able to find specific documentation about red bins.",
            sources=[],
            confidence="low",
        )
        # The RAG tool returned "No relevant documents found" — not a SOURCE block
        result = run_quality_gate(
            response,
            rag_outputs=["No relevant documents found for this query."],
            routed_agent="website_agent",
        )
        # Should NOT be results_ignored (no substantive results to recover)
        assert result.check != "results_ignored"
        # Should NOT be sources_missing (no substantive results)
        assert result.check != "sources_missing"
        # The gate can't help — it passes through unchanged
        assert result.check == "passed"

    def test_low_confidence_with_sources_passes(self) -> None:
        """Low confidence with populated sources should pass the gate."""
        rag_block = _make_rag_block()
        source = _make_source()
        response = _make_response(
            message="Based on what I found, here is some information.",
            sources=[source],
            confidence="low",
        )
        result = run_quality_gate(response, rag_outputs=[rag_block], routed_agent="hr_agent")
        assert result.check == "passed"
        assert result.remediated is response


class TestQualityGateIntegration:
    """Integration-level tests verifying the gate's interaction with the response pipeline."""

    def test_results_ignored_remediation_includes_sources(self) -> None:
        """RESULTS_IGNORED remediation should recover sources from RAG output blocks."""
        rag_blocks = [
            _make_rag_block(
                index=1,
                title="Leave Policy",
                document_id="leave-policy-v2",
                relevance=0.9,
                snippet="Annual leave entitlements are outlined below.",
            ),
            _make_rag_block(
                index=2,
                title="Flexible Work Arrangements",
                document_id="flex-work-v1",
                relevance=0.82,
                snippet="Employees may request flexible hours.",
            ),
        ]
        response = _make_response(
            message="I wasn't able to find any relevant information about leave.",
            sources=[],
            confidence="low",
        )
        result = run_quality_gate(response, rag_outputs=rag_blocks, routed_agent="hr_agent")

        assert result.check == "results_ignored"
        assert result.remediated.confidence == "medium"
        assert len(result.remediated.sources) == 2
        source_titles = [s.title for s in result.remediated.sources]
        assert "Leave Policy" in source_titles
        assert "Flexible Work Arrangements" in source_titles

    def test_results_ignored_preserves_original_message(self) -> None:
        """Remediation should not alter the message field."""
        original_message = "I wasn't able to find any relevant documents about leave."
        rag_blocks = [
            _make_rag_block(
                index=1,
                title="Leave Policy",
                document_id="leave-policy-v2",
                relevance=0.9,
                snippet="Annual leave entitlements.",
            ),
        ]
        response = _make_response(
            message=original_message,
            sources=[],
            confidence="low",
        )
        result = run_quality_gate(response, rag_outputs=rag_blocks, routed_agent="hr_agent")

        assert result.check == "results_ignored"
        assert result.remediated.message == original_message

    def test_search_skipped_returns_original_unchanged(self) -> None:
        """When search is skipped, the remediated response should be the same object."""
        response = _make_response(
            message="Here is some general information.",
            sources=[],
            confidence="medium",
        )
        result = run_quality_gate(response, rag_outputs=[], routed_agent="hr_agent")

        assert result.check == "search_skipped"
        assert result.remediated is response

    def test_gate_does_not_double_remediate_sources_recovery(self) -> None:
        """Sources recovered via RESULTS_IGNORED should not be duplicated by subsequent recovery."""
        rag_blocks = [
            _make_rag_block(
                index=1,
                title="Code of Conduct",
                document_id="code-conduct-v3",
                relevance=0.88,
                snippet="Employees must adhere to professional standards.",
            ),
            _make_rag_block(
                index=2,
                title="IT Security Policy",
                document_id="it-sec-v1",
                relevance=0.75,
                snippet="All devices must use encryption.",
            ),
        ]
        response = _make_response(
            message="I couldn't find any information about the code of conduct.",
            sources=[],
            confidence="low",
        )
        result = run_quality_gate(response, rag_outputs=rag_blocks, routed_agent="hr_agent")

        assert result.check == "results_ignored"
        remediated = result.remediated
        # Sources should be recovered exactly once — no duplicates
        assert len(remediated.sources) == 2
        doc_ids = [s.document_id for s in remediated.sources]
        assert len(set(doc_ids)) == len(doc_ids), "Source document_ids should be unique"

        # Simulate what the caller would do: if sources are now populated,
        # a second extract_sources + deduplicate should be a no-op
        from src.agents._output import deduplicate_sources, extract_sources

        rag_text = "\n\n".join(rag_blocks)
        deduplicate_sources(extract_sources(rag_text))
        # Since remediated.sources is already populated, the caller's
        # `if not agent_response.sources` guard prevents double-recovery
        assert len(remediated.sources) > 0  # guard condition is False
        # Merging should not add duplicates
        all_ids = [s.document_id for s in remediated.sources]
        assert all_ids == list(dict.fromkeys(all_ids))

    def test_passed_response_unchanged(self) -> None:
        """A healthy response with sources and high confidence passes through unchanged."""
        rag_block = _make_rag_block(
            index=1,
            title="HR Policy",
            document_id="hr-policy-v1",
            relevance=0.92,
            snippet="Our HR policies ensure fair treatment.",
        )
        source = _make_source(
            title="HR Policy",
            document_id="hr-policy-v1",
            confidence=0.92,
        )
        response = _make_response(
            message="According to the HR Policy, fair treatment is ensured.",
            sources=[source],
            confidence="high",
        )
        result = run_quality_gate(response, rag_outputs=[rag_block], routed_agent="hr_agent")

        assert result.check == "passed"
        assert result.remediated is response
        assert result.remediated.confidence == "high"
        assert result.remediated.sources == [source]
        assert result.remediated.message == response.message
