"""Tests for _output.py sanitization and _MessageFieldExtractor pollution guard."""

import json
from typing import Any

import pytest

from src.agents._output import (
    _extract_json_object,  # pyright: ignore[reportPrivateUsage]
    _normalise_structured_data,  # pyright: ignore[reportPrivateUsage]
    _sanitize_agent_response,  # pyright: ignore[reportPrivateUsage]
    extract_sources,
    parse_agent_output,
)
from src.models.agent import AgentResponseModel, Source

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _source_block(n: int = 1) -> str:
    return (
        f"=== SOURCE {n} ===\n"
        f'title: "Enterprise Agreement 2024"\n'
        f'section: "6.2 Nine Day Fortnight"\n'
        f'document_id: "abc{n}"\n'
        f"relevance: 0.95\n"
        f"url: null\n"
        f'snippet: "Employees working a 9DFN shall be entitled to an RDO"\n\n'
        f"CONTENT:\nSome full text content here.\n\n"
        f"=== END SOURCE {n} ==="
    )


# ---------------------------------------------------------------------------
# _sanitize_agent_response
# ---------------------------------------------------------------------------


class TestSanitizeAgentResponse:
    def test_clean_message_passes_through(self):
        model = AgentResponseModel(
            message=(
                "Under clause 6.2.3, the RDO day is agreed between"
                " the employee and their supervisor."
            ),
            sources=[],
            confidence="high",
        )
        result = _sanitize_agent_response(model)
        assert result.message == model.message
        assert result.sources == []

    def test_strips_source_block_from_message(self):
        dirty_message = _source_block(1)
        model = AgentResponseModel(message=dirty_message, sources=[], confidence="high")
        result = _sanitize_agent_response(model)
        assert "=== SOURCE" not in result.message
        assert "=== END SOURCE" not in result.message

    def test_recovers_source_from_stripped_block(self):
        dirty_message = _source_block(1)
        model = AgentResponseModel(message=dirty_message, sources=[], confidence="high")
        result = _sanitize_agent_response(model)
        assert len(result.sources) == 1
        src = result.sources[0]
        assert src.document_id == "abc1"
        assert src.title == "Enterprise Agreement 2024"
        assert src.confidence == pytest.approx(0.95)

    def test_recovers_multiple_sources(self):
        dirty = _source_block(1) + "\n\n" + _source_block(2)
        model = AgentResponseModel(message=dirty, sources=[], confidence="high")
        result = _sanitize_agent_response(model)
        assert len(result.sources) == 2
        assert {s.document_id for s in result.sources} == {"abc1", "abc2"}

    def test_preserves_existing_sources_when_present(self):
        existing = Source(title="Existing", document_id="existing1", confidence=0.8)
        dirty = _source_block(1)
        model = AgentResponseModel(message=dirty, sources=[existing], confidence="high")
        result = _sanitize_agent_response(model)
        # Existing sources not replaced by recovered ones
        assert len(result.sources) == 1
        assert result.sources[0].document_id == "existing1"

    def test_fallback_message_when_only_source_blocks(self):
        model = AgentResponseModel(message=_source_block(1), sources=[], confidence="high")
        result = _sanitize_agent_response(model)
        assert result.message  # not empty
        assert "=== SOURCE" not in result.message

    def test_prose_before_source_block_preserved(self):
        msg = "Here is the information you need.\n\n" + _source_block(1)
        model = AgentResponseModel(message=msg, sources=[], confidence="high")
        result = _sanitize_agent_response(model)
        assert "Here is the information you need." in result.message
        assert "=== SOURCE" not in result.message


# ---------------------------------------------------------------------------
# _normalise_structured_data
# ---------------------------------------------------------------------------


class TestNormaliseStructuredData:
    def _model(self, **overrides: Any) -> AgentResponseModel:
        defaults: dict[str, Any] = {"message": "Answer text.", "sources": [], "confidence": "high"}
        defaults.update(overrides)
        return AgentResponseModel(**defaults)

    def test_none_structured_data_unchanged(self):
        m = self._model(structured_data=None, ui_hint="text")
        result = _normalise_structured_data(m)
        assert result.structured_data is None
        assert result.ui_hint == "text"

    def test_empty_string_normalised_to_none(self):
        m = self._model(structured_data="", ui_hint="card")
        result = _normalise_structured_data(m)
        assert result.structured_data is None
        assert result.ui_hint == "text"

    def test_empty_object_normalised_to_none(self):
        m = self._model(structured_data="{}", ui_hint="table")
        result = _normalise_structured_data(m)
        assert result.structured_data is None
        assert result.ui_hint == "text"

    def test_null_string_normalised_to_none(self):
        m = self._model(structured_data="null", ui_hint="list")
        result = _normalise_structured_data(m)
        assert result.structured_data is None
        assert result.ui_hint == "text"

    def test_ui_hint_without_structured_data_reset_to_text(self):
        m = self._model(structured_data=None, ui_hint="steps")
        result = _normalise_structured_data(m)
        assert result.structured_data is None
        assert result.ui_hint == "text"

    def test_text_hint_with_structured_data_clears_data(self):
        m = self._model(structured_data='{"steps": ["Step 1"]}', ui_hint="text")
        result = _normalise_structured_data(m)
        assert result.structured_data is None
        assert result.ui_hint == "text"

    def test_valid_structured_data_preserved(self):
        sd = '{"steps": ["Step 1", "Step 2"]}'
        m = self._model(structured_data=sd, ui_hint="steps")
        result = _normalise_structured_data(m)
        assert result.structured_data == sd
        assert result.ui_hint == "steps"

    def test_whitespace_only_normalised_to_none(self):
        m = self._model(structured_data="  \n  ", ui_hint="card")
        result = _normalise_structured_data(m)
        assert result.structured_data is None
        assert result.ui_hint == "text"


# ---------------------------------------------------------------------------
# parse_agent_output — sanitization applied at all return paths
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _extract_json_object — robust JSON extraction
# ---------------------------------------------------------------------------


class TestExtractJsonObject:
    def test_clean_json(self):
        raw = '{"message": "hello", "confidence": "high"}'
        assert _extract_json_object(raw) == raw

    def test_free_text_before_json(self):
        """The exact failure case: agent outputs commentary then JSON on same line."""
        raw = (
            'The search results returned irrelevant docs.{"message": "answer", "confidence": "low"}'
        )
        result = _extract_json_object(raw)
        assert result is not None
        data = json.loads(result)
        assert data["message"] == "answer"

    def test_free_text_with_newline_before_json(self):
        raw = 'Some commentary\n{"message": "answer", "confidence": "low"}'
        result = _extract_json_object(raw)
        assert result is not None
        data = json.loads(result)
        assert data["message"] == "answer"

    def test_nested_braces(self):
        raw = '{"message": "test", "nested": {"a": 1}}'
        result = _extract_json_object(raw)
        assert result == raw

    def test_braces_in_strings(self):
        raw = '{"message": "use { and } in text", "confidence": "high"}'
        result = _extract_json_object(raw)
        assert result == raw

    def test_escaped_quotes_in_strings(self):
        raw = r'{"message": "she said \"hello\"", "confidence": "high"}'
        result = _extract_json_object(raw)
        assert result == raw

    def test_no_json(self):
        assert _extract_json_object("just plain text") is None

    def test_empty_string(self):
        assert _extract_json_object("") is None


class TestParseAgentOutputFreeText:
    """Test parse_agent_output when agent emits free text before JSON."""

    def test_free_text_then_json_on_same_line(self):
        payload: dict[str, Any] = {
            "message": "The leave policy states 20 days annual leave.",
            "sources": [],
            "confidence": "high",
            "ui_hint": "text",
            "follow_up_suggestions": [],
        }
        raw = "Let me search for that information." + json.dumps(payload)
        result = parse_agent_output(raw, "hr_agent")
        assert result.message == "The leave policy states 20 days annual leave."
        assert result.confidence == "high"

    def test_free_text_then_json_on_new_line(self):
        payload: dict[str, Any] = {
            "message": "Annual leave is 20 days per year.",
            "sources": [],
            "confidence": "high",
            "ui_hint": "text",
            "follow_up_suggestions": [],
        }
        raw = "Here are the search results.\n" + json.dumps(payload)
        result = parse_agent_output(raw, "hr_agent")
        assert result.message == "Annual leave is 20 days per year."


# ---------------------------------------------------------------------------
# parse_agent_output — sanitization applied at all return paths
# ---------------------------------------------------------------------------


class TestParseAgentOutputSanitization:
    def test_json_fast_path_sanitizes(self):
        payload: dict[str, Any] = {
            "message": _source_block(1),
            "sources": [],
            "confidence": "high",
            "ui_hint": "text",
            "follow_up_suggestions": [],
        }
        raw = json.dumps(payload)
        result = parse_agent_output(raw, "hr_agent")
        assert "=== SOURCE" not in result.message
        assert len(result.sources) == 1

    def test_plaintext_fallback_sanitizes(self):
        # Raw text that is NOT valid JSON but contains source markers
        raw = _source_block(1)
        result = parse_agent_output(raw, "hr_agent")
        assert "=== SOURCE" not in result.message
        assert len(result.sources) == 1

    def test_clean_json_unaffected(self):
        payload = {
            "message": "The RDO is agreed between employee and supervisor.",
            "sources": [{"title": "Enterprise Agreement", "document_id": "d1", "confidence": 0.9}],
            "confidence": "high",
            "ui_hint": "text",
            "follow_up_suggestions": [],
        }
        result = parse_agent_output(json.dumps(payload), "hr_agent")
        assert result.message == "The RDO is agreed between employee and supervisor."
        assert len(result.sources) == 1

    def test_empty_structured_data_normalised(self):
        """End-to-end: parse_agent_output normalises empty structured_data."""
        payload: dict[str, Any] = {
            "message": "Answer.",
            "sources": [],
            "confidence": "high",
            "ui_hint": "card",
            "structured_data": "",
            "follow_up_suggestions": [],
        }
        result = parse_agent_output(json.dumps(payload), "hr_agent")
        assert result.structured_data is None
        assert result.ui_hint == "text"

    def test_valid_structured_data_preserved_end_to_end(self):
        sd = '{"steps": ["Step 1", "Step 2"]}'
        payload: dict[str, Any] = {
            "message": "Here are the steps:",
            "sources": [],
            "confidence": "high",
            "ui_hint": "steps",
            "structured_data": sd,
            "follow_up_suggestions": [],
        }
        result = parse_agent_output(json.dumps(payload), "hr_agent")
        assert result.structured_data == sd
        assert result.ui_hint == "steps"


# ---------------------------------------------------------------------------
# extract_sources — public function
# ---------------------------------------------------------------------------


class TestExtractSources:
    def test_extracts_single_source(self):
        text = "Some preamble\n" + _source_block(1) + "\nSome trailing text"
        sources = extract_sources(text)
        assert len(sources) == 1
        assert sources[0].document_id == "abc1"
        assert sources[0].title == "Enterprise Agreement 2024"
        assert sources[0].confidence == pytest.approx(0.95)

    def test_extracts_multiple_sources(self):
        text = _source_block(1) + "\n\n" + _source_block(2) + "\n\n" + _source_block(3)
        sources = extract_sources(text)
        assert len(sources) == 3
        assert {s.document_id for s in sources} == {"abc1", "abc2", "abc3"}

    def test_returns_empty_for_no_blocks(self):
        sources = extract_sources("Just some plain text with no source blocks.")
        assert sources == []

    def test_returns_empty_for_empty_string(self):
        sources = extract_sources("")
        assert sources == []

    def test_skips_malformed_blocks(self):
        # Block without document_id should be skipped
        malformed = '=== SOURCE 1 ===\ntitle: "Some Title"\nrelevance: 0.8\n=== END SOURCE 1 ==='
        good = _source_block(2)
        sources = extract_sources(malformed + "\n" + good)
        assert len(sources) == 1
        assert sources[0].document_id == "abc2"


# ---------------------------------------------------------------------------
# _MessageFieldExtractor — pollution guard
# ---------------------------------------------------------------------------


class TestMessageFieldExtractorGuard:
    def _extractor(self) -> Any:
        from src.routes.chat import _MessageFieldExtractor  # pyright: ignore[reportPrivateUsage]

        return _MessageFieldExtractor()

    def _feed_all(self, extractor: Any, tokens: list[str]) -> str:
        return "".join(extractor.feed(t) for t in tokens)

    def test_normal_message_streams(self):
        ex = self._extractor()
        tokens = ['{"message": "Under clause 6.2', '.3 the RDO is agreed."}']
        result = self._feed_all(ex, tokens)
        assert "Under clause 6.2" in result

    def test_source_pollution_suppressed(self):
        ex = self._extractor()
        # Message field contains a raw source block
        msg_value = _source_block(1).replace('"', '\\"')
        raw = f'{{"message": "{msg_value}"}}'
        tokens = [raw[i : i + 5] for i in range(0, len(raw), 5)]
        result = self._feed_all(ex, tokens)
        assert result == ""

    def test_non_pollution_message_not_suppressed(self):
        ex = self._extractor()
        msg = "The RDO day is negotiated between the employee and their supervisor."
        raw = json.dumps({"message": msg})
        tokens = [raw[i : i + 3] for i in range(0, len(raw), 3)]
        result = self._feed_all(ex, tokens)
        assert "RDO" in result

    def test_short_message_not_suppressed(self):
        ex = self._extractor()
        raw = json.dumps({"message": "Yes."})
        result = self._feed_all(ex, [raw])
        assert "Yes." in result
