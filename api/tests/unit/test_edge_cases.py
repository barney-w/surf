"""Edge case tests for boundary conditions and unusual inputs."""

import pytest
from fastapi import HTTPException

from src.middleware.input_validation import MAX_MESSAGE_LENGTH, validate_message


class TestMessageEdgeCases:
    def test_exact_max_length_accepted(self):
        msg = "a" * MAX_MESSAGE_LENGTH
        result = validate_message(msg)
        assert len(result) == MAX_MESSAGE_LENGTH

    def test_one_over_max_length_rejected(self):
        with pytest.raises(HTTPException) as exc_info:
            validate_message("a" * (MAX_MESSAGE_LENGTH + 1))
        assert exc_info.value.status_code == 422

    def test_empty_string_passes(self):
        result = validate_message("")
        assert result == ""

    def test_unicode_emoji_preserved(self):
        msg = "Hello 👋 World 🌍"
        result = validate_message(msg)
        assert "👋" in result
        assert "🌍" in result

    def test_zero_width_chars_preserved(self):
        msg = "test\u200bword"  # zero-width space
        result = validate_message(msg)
        assert "\u200b" in result  # U+200B is above ASCII control char range

    def test_newlines_and_tabs_preserved(self):
        msg = "line1\nline2\ttab"
        result = validate_message(msg)
        assert "\n" in result
        assert "\t" in result

    def test_only_whitespace(self):
        result = validate_message("   \n\t  ")
        assert result == "   \n\t  "

    def test_null_bytes_stripped(self):
        msg = "hello\x00world"
        result = validate_message(msg)
        assert "\x00" not in result


class TestConversationIdEdgeCases:
    def test_valid_uuid_format(self):
        import re

        uuid_re = re.compile(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
            re.IGNORECASE,
        )
        assert uuid_re.match("550e8400-e29b-41d4-a716-446655440000")
        assert not uuid_re.match("not-a-uuid")
        assert not uuid_re.match("")
        assert not uuid_re.match("550e8400e29b41d4a716446655440000")  # no hyphens
        assert not uuid_re.match("../../etc/passwd")
