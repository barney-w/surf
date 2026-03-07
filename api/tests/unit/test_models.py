"""Tests for Pydantic models — validation edge cases and serialization round-trips."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from src.models import (
    AgentResponseModel,
    ChatRequest,
    ChatResponse,
    ConversationDocument,
    ConversationMetadata,
    MessageRecord,
    RoutingMetadata,
    Source,
)

# ---------------------------------------------------------------------------
# Source.confidence bounds
# ---------------------------------------------------------------------------

class TestSourceConfidence:
    def test_rejects_negative_confidence(self):
        with pytest.raises(ValidationError):
            Source(title="t", document_id="d1", confidence=-0.1)

    def test_rejects_confidence_above_one(self):
        with pytest.raises(ValidationError):
            Source(title="t", document_id="d1", confidence=1.01)

    def test_accepts_boundary_values(self):
        s_low = Source(title="t", document_id="d1", confidence=0.0)
        s_high = Source(title="t", document_id="d1", confidence=1.0)
        assert s_low.confidence == 0.0
        assert s_high.confidence == 1.0


# ---------------------------------------------------------------------------
# ChatRequest.message validation
# ---------------------------------------------------------------------------

class TestChatRequestMessage:
    def test_rejects_empty_message(self):
        with pytest.raises(ValidationError):
            ChatRequest(message="")

    def test_rejects_message_over_max_length(self):
        with pytest.raises(ValidationError):
            ChatRequest(message="x" * 10001)

    def test_accepts_valid_message(self):
        req = ChatRequest(message="Hello")
        assert req.message == "Hello"


# ---------------------------------------------------------------------------
# AgentResponseModel.ui_hint validation
# ---------------------------------------------------------------------------

class TestAgentResponseModelUiHint:
    def test_rejects_invalid_ui_hint(self):
        with pytest.raises(ValidationError):
            AgentResponseModel(
                message="hi",
                confidence="high",
                ui_hint="invalid_hint",
            )

    def test_accepts_all_valid_ui_hints(self):
        for hint in ("text", "table", "card", "list", "steps", "warning"):
            resp = AgentResponseModel(
                message="hi",
                confidence="high",
                ui_hint=hint,
            )
            assert resp.ui_hint == hint

    def test_defaults_to_text(self):
        resp = AgentResponseModel(message="hi", confidence="high")
        assert resp.ui_hint == "text"


# ---------------------------------------------------------------------------
# ChatResponse JSON round-trip
# ---------------------------------------------------------------------------

class TestChatResponseRoundTrip:
    def test_json_round_trip(self):
        now = datetime.now(UTC)
        original = ChatResponse(
            conversation_id="conv-1",
            message_id="msg-1",
            agent="planning",
            response=AgentResponseModel(
                message="answer",
                sources=[
                    Source(
                        title="Doc A",
                        document_id="doc-1",
                        confidence=0.95,
                        snippet="relevant text",
                    )
                ],
                confidence="high",
                ui_hint="card",
                structured_data={"key": "value"},
                follow_up_suggestions=["Ask about X"],
            ),
            routing=RoutingMetadata(
                routed_by="orchestrator",
                primary_agent="planning",
                secondary_suggestion="environment",
            ),
            created_at=now,
        )
        json_str = original.model_dump_json()
        restored = ChatResponse.model_validate_json(json_str)
        assert restored == original


# ---------------------------------------------------------------------------
# ConversationDocument full serialization
# ---------------------------------------------------------------------------

class TestConversationDocumentSerialization:
    def test_full_conversation_round_trip(self):
        now = datetime.now(UTC)
        doc = ConversationDocument(
            id="conv-42",
            user_id="user-7",
            created_at=now,
            updated_at=now,
            messages=[
                MessageRecord(
                    id="msg-1",
                    role="user",
                    content="What is the zoning?",
                    timestamp=now,
                ),
                MessageRecord(
                    id="msg-2",
                    role="assistant",
                    agent="planning",
                    response=AgentResponseModel(
                        message="The zoning is residential.",
                        confidence="high",
                        sources=[
                            Source(
                                title="Zoning Map",
                                document_id="zone-1",
                                confidence=0.9,
                            )
                        ],
                    ),
                    timestamp=now,
                ),
            ],
            metadata=ConversationMetadata(
                last_active_agent="planning",
                message_count=2,
            ),
        )
        json_str = doc.model_dump_json()
        restored = ConversationDocument.model_validate_json(json_str)
        assert restored == doc
        assert len(restored.messages) == 2
        assert restored.metadata.message_count == 2
