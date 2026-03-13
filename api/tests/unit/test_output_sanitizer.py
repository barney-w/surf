"""Tests for _output.py sanitization and _MessageFieldExtractor pollution guard."""

import json
from typing import Any

import pytest

from src.agents._output import (
    deduplicate_sources,
    extract_json_object,
    extract_sources,
    normalise_structured_data,
    parse_agent_output,
    sanitize_agent_response,
    strip_source_urls,
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
# sanitize_agent_response
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
        result = sanitize_agent_response(model)
        assert result.message == model.message
        assert result.sources == []

    def test_strips_source_block_from_message(self):
        dirty_message = _source_block(1)
        model = AgentResponseModel(message=dirty_message, sources=[], confidence="high")
        result = sanitize_agent_response(model)
        assert "=== SOURCE" not in result.message
        assert "=== END SOURCE" not in result.message

    def test_recovers_source_from_stripped_block(self):
        dirty_message = _source_block(1)
        model = AgentResponseModel(message=dirty_message, sources=[], confidence="high")
        result = sanitize_agent_response(model)
        assert len(result.sources) == 1
        src = result.sources[0]
        assert src.document_id == "abc1"
        assert src.title == "Enterprise Agreement 2024"
        assert src.confidence == pytest.approx(0.95)  # pyright: ignore[reportUnknownMemberType]

    def test_recovers_multiple_sources(self):
        dirty = _source_block(1) + "\n\n" + _source_block(2)
        model = AgentResponseModel(message=dirty, sources=[], confidence="high")
        result = sanitize_agent_response(model)
        assert len(result.sources) == 2
        assert {s.document_id for s in result.sources} == {"abc1", "abc2"}

    def test_preserves_existing_sources_when_present(self):
        existing = Source(title="Existing", document_id="existing1", confidence=0.8)
        dirty = _source_block(1)
        model = AgentResponseModel(message=dirty, sources=[existing], confidence="high")
        result = sanitize_agent_response(model)
        # Existing sources not replaced by recovered ones
        assert len(result.sources) == 1
        assert result.sources[0].document_id == "existing1"

    def test_fallback_message_when_only_source_blocks(self):
        model = AgentResponseModel(message=_source_block(1), sources=[], confidence="high")
        result = sanitize_agent_response(model)
        assert result.message  # not empty
        assert "=== SOURCE" not in result.message

    def test_prose_before_source_block_preserved(self):
        msg = "Here is the information you need.\n\n" + _source_block(1)
        model = AgentResponseModel(message=msg, sources=[], confidence="high")
        result = sanitize_agent_response(model)
        assert "Here is the information you need." in result.message
        assert "=== SOURCE" not in result.message


# ---------------------------------------------------------------------------
# normalise_structured_data
# ---------------------------------------------------------------------------


class TestNormaliseStructuredData:
    def _model(self, **overrides: Any) -> AgentResponseModel:
        defaults: dict[str, Any] = {"message": "Answer text.", "sources": [], "confidence": "high"}
        defaults.update(overrides)
        return AgentResponseModel(**defaults)

    def test_none_structured_data_unchanged(self):
        m = self._model(structured_data=None, ui_hint="text")
        result = normalise_structured_data(m)
        assert result.structured_data is None
        assert result.ui_hint == "text"

    def test_empty_string_normalised_to_none(self):
        m = self._model(structured_data="", ui_hint="card")
        result = normalise_structured_data(m)
        assert result.structured_data is None
        assert result.ui_hint == "text"

    def test_empty_object_normalised_to_none(self):
        m = self._model(structured_data="{}", ui_hint="table")
        result = normalise_structured_data(m)
        assert result.structured_data is None
        assert result.ui_hint == "text"

    def test_null_string_normalised_to_none(self):
        m = self._model(structured_data="null", ui_hint="list")
        result = normalise_structured_data(m)
        assert result.structured_data is None
        assert result.ui_hint == "text"

    def test_ui_hint_without_structured_data_reset_to_text(self):
        m = self._model(structured_data=None, ui_hint="steps")
        result = normalise_structured_data(m)
        assert result.structured_data is None
        assert result.ui_hint == "text"

    def test_text_hint_with_structured_data_clears_data(self):
        m = self._model(structured_data='{"steps": ["Step 1"]}', ui_hint="text")
        result = normalise_structured_data(m)
        assert result.structured_data is None
        assert result.ui_hint == "text"

    def test_valid_structured_data_preserved(self):
        sd = '{"steps": ["Step 1", "Step 2"]}'
        m = self._model(structured_data=sd, ui_hint="steps")
        result = normalise_structured_data(m)
        assert result.structured_data == sd
        assert result.ui_hint == "steps"

    def test_whitespace_only_normalised_to_none(self):
        m = self._model(structured_data="  \n  ", ui_hint="card")
        result = normalise_structured_data(m)
        assert result.structured_data is None
        assert result.ui_hint == "text"


# ---------------------------------------------------------------------------
# parse_agent_output — sanitization applied at all return paths
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# extract_json_object — robust JSON extraction
# ---------------------------------------------------------------------------


class TestExtractJsonObject:
    def test_clean_json(self):
        raw = '{"message": "hello", "confidence": "high"}'
        assert extract_json_object(raw) == raw

    def test_free_text_before_json(self):
        """The exact failure case: agent outputs commentary then JSON on same line."""
        raw = (
            'The search results returned irrelevant docs.{"message": "answer", "confidence": "low"}'
        )
        result = extract_json_object(raw)
        assert result is not None
        data = json.loads(result)
        assert data["message"] == "answer"

    def test_free_text_with_newline_before_json(self):
        raw = 'Some commentary\n{"message": "answer", "confidence": "low"}'
        result = extract_json_object(raw)
        assert result is not None
        data = json.loads(result)
        assert data["message"] == "answer"

    def test_nested_braces(self):
        raw = '{"message": "test", "nested": {"a": 1}}'
        result = extract_json_object(raw)
        assert result == raw

    def test_braces_in_strings(self):
        raw = '{"message": "use { and } in text", "confidence": "high"}'
        result = extract_json_object(raw)
        assert result == raw

    def test_escaped_quotes_in_strings(self):
        raw = r'{"message": "she said \"hello\"", "confidence": "high"}'
        result = extract_json_object(raw)
        assert result == raw

    def test_no_json(self):
        assert extract_json_object("just plain text") is None

    def test_empty_string(self):
        assert extract_json_object("") is None


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
        assert sources[0].confidence == pytest.approx(0.95)  # pyright: ignore[reportUnknownMemberType]

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

    def test_unicode_escape_produces_correct_char(self):
        ex = self._extractor()
        # \u00e9 = é — the JSON string contains a literal \u escape
        raw = '{"message": "caf\\u00e9 culture"}'
        result = self._feed_all(ex, [raw])
        assert "café culture" in result

    def test_unicode_escape_across_chunks(self):
        ex = self._extractor()
        # Split \u00e9 across two chunks: "\\u00" and "e9"
        chunk1 = '{"message": "caf\\u00'
        chunk2 = 'e9 culture"}'
        result = self._feed_all(ex, [chunk1, chunk2])
        assert "café culture" in result

    def test_markdown_formatted_message_streams_correctly(self):
        """Markdown content with newlines, bold, and lists must stream intact."""
        ex = self._extractor()
        msg = (
            "To book a community venue:\n\n"
            "1. **Choose a room** — view photos and floorplans\n"
            "2. **Review fees** — check the fees and charges documents\n"
            "3. **Submit an enquiry** — complete the online form\n\n"
            "All bookings must be paid before confirmation."
        )
        raw = json.dumps({"message": msg, "confidence": "high"})
        tokens = [raw[i : i + 7] for i in range(0, len(raw), 7)]
        result = self._feed_all(ex, tokens)
        assert "**Choose a room**" in result
        assert "1." in result
        assert "\n" in result
        assert "All bookings must be paid" in result

    def test_markdown_headings_and_bullets_stream(self):
        """Headings and bullet points must pass through the extractor."""
        ex = self._extractor()
        msg = (
            "### Eligibility\n\n"
            "You must meet **all** of the following:\n\n"
            "- Be a current employee\n"
            "- Have completed *probation*\n"
            "- Hold a valid certification"
        )
        raw = json.dumps({"message": msg})
        tokens = [raw[i : i + 10] for i in range(0, len(raw), 10)]
        result = self._feed_all(ex, tokens)
        assert "### Eligibility" in result
        assert "**all**" in result
        assert "*probation*" in result
        assert "- Be a current employee" in result


# ---------------------------------------------------------------------------
# parse_agent_output — markdown-formatted messages
# ---------------------------------------------------------------------------


class TestParseAgentOutputMarkdown:
    """Verify that markdown-formatted message fields survive parsing."""

    def test_markdown_lists_preserved(self):
        payload = {
            "message": (
                "To apply for leave:\n\n"
                "1. **Log in** to the HR portal\n"
                "2. **Select** your leave type\n"
                "3. **Submit** for manager approval\n\n"
                "You will receive an email confirmation."
            ),
            "sources": [],
            "confidence": "high",
            "ui_hint": "text",
            "follow_up_suggestions": [],
        }
        result = parse_agent_output(json.dumps(payload), "hr_agent")
        assert "1. **Log in**" in result.message
        assert "2. **Select**" in result.message
        assert "\n" in result.message

    def test_markdown_headings_preserved(self):
        payload = {
            "message": (
                "### Annual Leave\n\n"
                "You are entitled to **20 days** per year.\n\n"
                "### Personal Leave\n\n"
                "You are entitled to **10 days** per year."
            ),
            "sources": [],
            "confidence": "high",
            "ui_hint": "text",
            "follow_up_suggestions": [],
        }
        result = parse_agent_output(json.dumps(payload), "hr_agent")
        assert "### Annual Leave" in result.message
        assert "### Personal Leave" in result.message
        assert "**20 days**" in result.message

    def test_markdown_bold_italic_preserved(self):
        payload = {
            "message": (
                "The deadline is **Friday 15 March**. Late submissions will *not* be accepted."
            ),
            "sources": [],
            "confidence": "high",
            "ui_hint": "text",
            "follow_up_suggestions": [],
        }
        result = parse_agent_output(json.dumps(payload), "hr_agent")
        assert "**Friday 15 March**" in result.message
        assert "*not*" in result.message


# ---------------------------------------------------------------------------
# content_source extraction and website source collapsing
# ---------------------------------------------------------------------------


def _website_source_block(n: int = 1) -> str:
    return (
        f"=== SOURCE {n} ===\n"
        f'title: "Waste Collection Services"\n'
        f'section: "Residential Bins"\n'
        f'document_id: "web{n}"\n'
        f"relevance: 0.85\n"
        f'url: "https://example.com/services/waste"\n'
        f'content_source: "website"\n'
        f'snippet: "Bins are collected weekly on your designated day"\n\n'
        f"CONTENT:\nFull content about waste collection.\n\n"
        f"=== END SOURCE {n} ==="
    )


class TestContentSourceExtraction:
    def test_extracts_content_source_from_block(self):
        sources = extract_sources(_website_source_block(1))
        assert len(sources) == 1
        assert sources[0].content_source == "website"

    def test_content_source_null_when_absent(self):
        sources = extract_sources(_source_block(1))
        assert len(sources) == 1
        assert sources[0].content_source is None

    def test_mixed_sources_preserve_content_source(self):
        text = _source_block(1) + "\n\n" + _website_source_block(2)
        sources = extract_sources(text)
        assert len(sources) == 2
        assert sources[0].content_source is None
        assert sources[1].content_source == "website"


class TestWebsiteSourceCollapsing:
    def test_no_website_sources_unchanged(self):
        sources = [
            Source(title="Policy A", document_id="d1", confidence=0.9),
            Source(title="Policy B", document_id="d2", confidence=0.8),
        ]
        result = deduplicate_sources(sources)
        assert len(result) == 2
        assert result[0].document_id == "d1"
        assert result[1].document_id == "d2"

    def test_website_sources_collapsed_to_single_entry(self):
        sources = [
            Source(title="Page A", document_id="w1", confidence=0.9, content_source="website"),
            Source(title="Page B", document_id="w2", confidence=0.7, content_source="website"),
        ]
        result = deduplicate_sources(sources)
        assert len(result) == 1
        assert result[0].title == "Public Website"
        assert result[0].document_id == "website"
        assert result[0].content_source == "website"

    def test_collapsed_entry_uses_best_confidence(self):
        sources = [
            Source(title="Page A", document_id="w1", confidence=0.6, content_source="website"),
            Source(title="Page B", document_id="w2", confidence=0.9, content_source="website"),
        ]
        result = deduplicate_sources(sources)
        assert result[0].confidence == pytest.approx(0.9)  # pyright: ignore[reportUnknownMemberType]

    def test_collapsed_entry_uses_best_url(self):
        sources = [
            Source(
                title="Page A",
                document_id="w1",
                confidence=0.9,
                content_source="website",
                url="https://example.com/best",
            ),
            Source(title="Page B", document_id="w2", confidence=0.7, content_source="website"),
        ]
        result = deduplicate_sources(sources)
        assert result[0].url == "https://example.com/best"

    def test_mixed_sources_preserves_non_website(self):
        sources = [
            Source(title="Policy A", document_id="d1", confidence=0.9),
            Source(title="Page A", document_id="w1", confidence=0.85, content_source="website"),
            Source(title="Page B", document_id="w2", confidence=0.7, content_source="website"),
            Source(title="Policy B", document_id="d2", confidence=0.8),
        ]
        result = deduplicate_sources(sources)
        assert len(result) == 3
        assert result[0].document_id == "d1"
        assert result[1].document_id == "website"  # collapsed, at first website position
        assert result[2].document_id == "d2"

    def test_collapsed_entry_inserted_at_first_website_position(self):
        sources = [
            Source(title="Page A", document_id="w1", confidence=0.85, content_source="website"),
            Source(title="Policy A", document_id="d1", confidence=0.9),
        ]
        result = deduplicate_sources(sources)
        assert result[0].document_id == "website"
        assert result[1].document_id == "d1"

    def test_empty_sources_returns_empty(self):
        assert deduplicate_sources([]) == []


class TestStripSourceUrls:
    def test_removes_urls_from_all_sources(self):
        model = AgentResponseModel(
            message="Leave entitlements are outlined below.",
            sources=[
                Source(
                    title="Leave Policy",
                    document_id="d1",
                    confidence=0.9,
                    url="https://hetty123.sharepoint.com/Shared%20Documents/policies/leave.pdf",
                ),
                Source(
                    title="EA 2024",
                    document_id="d2",
                    confidence=0.85,
                    url="https://hetty123.sharepoint.com/Shared%20Documents/ea.pdf",
                    section="Clause 12",
                ),
            ],
            confidence="high",
            follow_up_suggestions=["Check leave balance"],
        )
        result = strip_source_urls(model)
        for src in result.sources:
            assert src.url is None
        # Other fields preserved
        assert result.sources[0].title == "Leave Policy"
        assert result.sources[1].section == "Clause 12"
        assert result.message == model.message

    def test_noop_when_no_sources(self):
        model = AgentResponseModel(
            message="No docs found.",
            sources=[],
            confidence="low",
            follow_up_suggestions=[],
        )
        result = strip_source_urls(model)
        assert result is model

    def test_preserves_sources_without_urls(self):
        model = AgentResponseModel(
            message="Info here.",
            sources=[
                Source(title="Policy A", document_id="d1", confidence=0.8),
            ],
            confidence="high",
            follow_up_suggestions=[],
        )
        result = strip_source_urls(model)
        assert result.sources[0].url is None
        assert result.sources[0].title == "Policy A"
