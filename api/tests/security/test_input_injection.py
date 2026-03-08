"""Security tests for input validation and injection handling.

These tests call validate_message() directly to verify sanitisation
and rejection behaviour.
"""

import pytest
from fastapi import HTTPException

from src.middleware.input_validation import MAX_MESSAGE_LENGTH, validate_message


class TestInputInjection:
    """validate_message() must sanitise control chars and reject oversized input."""

    def test_null_bytes_stripped(self):
        """Null bytes (\\x00) must be removed from the message."""
        message = "hello\x00world\x00"
        result = validate_message(message)
        assert "\x00" not in result
        assert result == "helloworld"

    def test_sql_injection_passes_through(self):
        """SQL injection strings are not blocked — they pass through unchanged."""
        message = "'; DROP TABLE users; --"
        result = validate_message(message)
        assert result == message

    def test_xss_script_tag_blocked(self):
        """XSS script tags are blocked as a prompt-injection vector."""
        message = "<script>alert(1)</script>"
        with pytest.raises(HTTPException) as exc_info:
            validate_message(message)
        assert exc_info.value.status_code == 422

    def test_extremely_long_message_rejected(self):
        """Messages longer than MAX_MESSAGE_LENGTH (10,000 chars) raise HTTP 422."""
        long_message = "a" * (MAX_MESSAGE_LENGTH + 1)
        with pytest.raises(HTTPException) as exc_info:
            validate_message(long_message)
        assert exc_info.value.status_code == 422

    def test_unicode_rtl_override_not_stripped(self):
        """RTL override (U+202E) is a Unicode character above ASCII range.

        The current _CONTROL_CHAR_RE pattern only covers ASCII control characters
        (\\x00-\\x1f, \\x7f). U+202E (\\u202E) is NOT in that range and therefore
        passes through the sanitiser unchanged. This test documents actual behaviour.
        """
        message = "safe\u202etext"
        result = validate_message(message)
        # U+202E is not stripped by the ASCII-range control char regex
        assert "\u202e" in result

    def test_zero_width_chars_preserved(self):
        """Zero-width space (U+200B) is not an ASCII control char and passes through."""
        message = "word\u200bspace"
        result = validate_message(message)
        assert "\u200b" in result
        assert result == message
