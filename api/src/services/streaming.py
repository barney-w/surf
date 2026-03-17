"""Streaming infrastructure for SSE chat endpoints.

Contains the SSE formatting helper, message field extraction from streaming
JSON, and prompt-length detection utilities.
"""

import json
import logging
import re

from anthropic import BadRequestError as AnthropicBadRequestError

logger = logging.getLogger(__name__)

# Number of heartbeat ticks (5s each) before emitting phase(waiting).
HEARTBEAT_WAIT_TICKS = 2  # 10 seconds

PROMPT_TOO_LONG_MESSAGE = (
    "The uploaded document is too large to process. "
    "Please try a shorter document or ask about specific pages."
)


def is_prompt_too_long(exc: Exception) -> bool:
    """Check if an exception is an Anthropic prompt-too-long error."""
    if isinstance(exc, AnthropicBadRequestError):
        msg = str(exc).lower()
        return "prompt is too long" in msg or "too many tokens" in msg
    return False


def sse(data: dict[str, object]) -> str:
    """Format a dict as a single SSE data event."""
    return f"data: {json.dumps(data)}\n\n"


class MessageFieldExtractor:
    """Extract the 'message' string value from a streaming JSON document.

    Domain agents emit AgentResponseModel JSON via response_format. Streaming
    delivers it as raw token chunks (e.g. '{"message": "Ann', 'ual leave...').
    This class scans for the "message": " marker and forwards the string value
    characters as they arrive, so the client can render them in real time.

    Since 'message' is the first field in AgentResponseModel, readable text
    starts flowing almost immediately after the LLM begins generating.

    Source-pollution guard: if the LLM puts raw === SOURCE === blocks inside the
    message field (prompt non-compliance), the extractor detects the prefix and
    suppresses all streaming output. The sanitized final response is delivered
    via the 'done' event instead, so the user still gets a clean answer.
    """

    _NEEDLE = re.compile(r'"message"\s*:\s*"')
    # Prefix that indicates the LLM leaked RAG source markers into the message.
    _SOURCE_POLLUTION_PREFIX = "=== SOURCE"
    _GUARD_LEN = len(_SOURCE_POLLUTION_PREFIX)

    def __init__(self) -> None:
        self._buf = ""  # pre-marker accumulation
        self._in_value = False
        self._escape = False
        self._done = False
        self._guard_buf = ""  # buffer first N chars for pollution check
        self._suppressed = False  # True once pollution is detected
        self._unicode_remaining = 0  # hex digits still expected for \uXXXX
        self._unicode_hex = ""  # accumulated hex digits

    def feed(self, chunk: str) -> str:
        """Feed a token chunk. Returns any message content ready to stream."""
        if self._done or self._suppressed:
            return ""

        if not self._in_value:
            self._buf += chunk
            m = self._NEEDLE.search(self._buf)
            if not m:
                return ""
            self._in_value = True
            remainder = self._buf[m.end() :]
            self._buf = ""
            return self._guarded_read(remainder)

        return self._guarded_read(chunk)

    def _guarded_read(self, s: str) -> str:
        """Read string chars, applying the pollution guard then normal extraction."""
        if self._suppressed:
            return ""

        # Still buffering the guard window.  Process the full input through
        # _read_string (escape sequences like \uXXXX consume multiple input
        # chars per output char, so we cannot split by input length).
        if len(self._guard_buf) < self._GUARD_LEN:
            out_inner, done = self._read_string(s)
            self._guard_buf += out_inner
            if done:
                self._done = True
                if self._guard_buf.startswith(self._SOURCE_POLLUTION_PREFIX):
                    self._suppressed = True
                    logger.warning(
                        "MessageFieldExtractor: source pollution detected — suppressing stream"
                    )
                    return ""
                return self._guard_buf

            if len(self._guard_buf) >= self._GUARD_LEN:
                if self._guard_buf.startswith(self._SOURCE_POLLUTION_PREFIX):
                    self._suppressed = True
                    logger.warning(
                        "MessageFieldExtractor: source pollution detected — suppressing stream"
                    )
                    return ""
                # Guard passed — emit all buffered chars.
                flushed = self._guard_buf
                self._guard_buf = ""
                return flushed

            return ""  # guard window not yet full

        # Guard already passed — normal extraction.
        out, done = self._read_string(s)
        if done:
            self._done = True
        return out

    def _read_string(self, s: str) -> tuple[str, bool]:
        """Read characters from inside a JSON string value until the closing quote."""
        out: list[str] = []
        for ch in s:
            # Accumulating hex digits for a \uXXXX escape
            if self._unicode_remaining > 0:
                self._unicode_hex += ch
                self._unicode_remaining -= 1
                if self._unicode_remaining == 0:
                    try:
                        out.append(chr(int(self._unicode_hex, 16)))
                    except ValueError:
                        out.append(self._unicode_hex)
                    self._unicode_hex = ""
                continue

            if self._escape:
                if ch == "u":
                    # Start \uXXXX — need 4 hex digits (may span chunks)
                    self._unicode_remaining = 4
                    self._unicode_hex = ""
                else:
                    out.append(
                        {
                            "n": "\n",
                            "t": "\t",
                            "r": "\r",
                            '"': '"',
                            "\\": "\\",
                            "/": "/",
                            "b": "\b",
                            "f": "\f",
                        }.get(ch, ch)
                    )
                self._escape = False
            elif ch == "\\":
                self._escape = True
            elif ch == '"':
                return "".join(out), True
            else:
                out.append(ch)
        return "".join(out), False
