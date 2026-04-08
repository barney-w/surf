"""Tests for Pydantic models — validation edge cases and serialization round-trips."""

import base64
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
from src.models.agent import EnrichedAgentResponse, enrich_agent_response
from src.models.chat import MAX_ATTACHMENT_SIZE, MAX_ATTACHMENTS, Attachment

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


# ---------------------------------------------------------------------------
# Attachment validation
# ---------------------------------------------------------------------------


class TestAttachmentContentType:
    def test_rejects_unsupported_type(self):
        data = base64.b64encode(b"fake").decode()
        with pytest.raises(ValidationError, match="Unsupported file type"):
            Attachment(filename="f.txt", content_type="text/plain", data=data)

    def test_accepts_supported_types(self):
        data = base64.b64encode(b"fake").decode()
        for ct in ("image/png", "image/jpeg", "application/pdf"):
            att = Attachment(filename="f", content_type=ct, data=data)
            assert att.content_type == ct


class TestAttachmentDataSize:
    def test_rejects_invalid_base64(self):
        with pytest.raises(ValidationError, match="Invalid base64"):
            Attachment(filename="f.pdf", content_type="application/pdf", data="!!!")

    def test_rejects_oversized_file(self):
        big = base64.b64encode(b"x" * (MAX_ATTACHMENT_SIZE + 1)).decode()
        with pytest.raises(ValidationError, match="limit"):
            Attachment(filename="f.pdf", content_type="application/pdf", data=big)

    def test_accepts_file_within_limit(self):
        data = base64.b64encode(b"small").decode()
        att = Attachment(filename="f.pdf", content_type="application/pdf", data=data)
        assert att.data == data


class TestAttachmentCount:
    def test_rejects_too_many_attachments(self):
        data = base64.b64encode(b"x").decode()
        attachments = [
            {"filename": f"f{i}.png", "content_type": "image/png", "data": data}
            for i in range(MAX_ATTACHMENTS + 1)
        ]
        with pytest.raises(ValidationError, match="Too many"):
            ChatRequest(message="hi", attachments=attachments)


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
                ui_hint="invalid_hint",  # pyright: ignore[reportArgumentType]
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
                structured_data='{"key": "value"}',
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


# ---------------------------------------------------------------------------
# AgentResponseModel.parsed_structured_data
# ---------------------------------------------------------------------------


class TestParsedStructuredData:
    def test_parses_valid_json_string_to_dict(self):
        model = AgentResponseModel(
            message="hi",
            confidence="high",
            structured_data='{"columns": ["A", "B"], "rows": [["1", "2"]]}',
        )
        result = model.parsed_structured_data()
        assert result == {"columns": ["A", "B"], "rows": [["1", "2"]]}

    def test_returns_none_for_null(self):
        model = AgentResponseModel(message="hi", confidence="high", structured_data=None)
        assert model.parsed_structured_data() is None

    def test_returns_none_for_non_dict_json(self):
        model = AgentResponseModel(message="hi", confidence="high", structured_data='["a", "b"]')
        assert model.parsed_structured_data() is None

    def test_returns_none_for_invalid_json(self):
        model = AgentResponseModel(message="hi", confidence="high", structured_data="not json")
        assert model.parsed_structured_data() is None

    def test_returns_none_for_empty_string(self):
        model = AgentResponseModel(message="hi", confidence="high", structured_data="")
        assert model.parsed_structured_data() is None


# ---------------------------------------------------------------------------
# enrich_agent_response structured_data handling
# ---------------------------------------------------------------------------


class TestEnrichStructuredData:
    def test_enriched_response_has_parsed_structured_data(self):
        model = AgentResponseModel(
            message="Three levels",
            confidence="high",
            ui_hint="table",
            structured_data='{"columns": ["Level"], "rows": [["Official"]]}',
        )
        enriched = enrich_agent_response(model)
        assert isinstance(enriched, EnrichedAgentResponse)
        assert enriched.structured_data == {"columns": ["Level"], "rows": [["Official"]]}
        assert enriched.ui_hint == "table"

    def test_enriched_response_null_structured_data(self):
        model = AgentResponseModel(message="hi", confidence="high")
        enriched = enrich_agent_response(model)
        assert enriched.structured_data is None

    def test_enriched_response_invalid_structured_data_becomes_none(self):
        model = AgentResponseModel(
            message="hi",
            confidence="high",
            ui_hint="table",
            structured_data="not json at all",
        )
        enriched = enrich_agent_response(model)
        assert enriched.structured_data is None

    def test_enriched_response_serializes_structured_data_as_dict(self):
        """Verify the JSON sent to clients has structured_data as an object, not a string."""
        model = AgentResponseModel(
            message="answer",
            confidence="high",
            ui_hint="table",
            structured_data='{"columns": ["A"], "rows": [["1"]]}',
        )
        enriched = enrich_agent_response(model)
        dumped = enriched.model_dump(mode="json")
        assert isinstance(dumped["structured_data"], dict)
        assert dumped["structured_data"]["columns"] == ["A"]


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
