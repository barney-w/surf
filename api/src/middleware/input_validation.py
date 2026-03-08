"""Input validation middleware for sanitising and validating user messages."""

import logging
import re

from fastapi import HTTPException

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 10_000

# Patterns that indicate prompt-injection or abuse — blocked with HTTP 422.
_SUSPICIOUS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"ignore\s+(previous|above)\s+instructions", re.IGNORECASE),
    re.compile(r"system\s*prompt", re.IGNORECASE),
    re.compile(r"<\s*script", re.IGNORECASE),
    re.compile(r"\{\{.*\}\}"),  # template injection markers
]

# Control characters except \n, \r, \t (which are harmless whitespace)
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def validate_message(message: str) -> str:
    """Validate and sanitise a user message.

    - Rejects messages longer than *MAX_MESSAGE_LENGTH* with HTTP 422.
    - Strips null bytes and control characters.
    - Rejects messages matching known prompt-injection patterns with HTTP 422.

    Returns the sanitised message.
    """
    # Length check
    if len(message) > MAX_MESSAGE_LENGTH:
        raise HTTPException(
            status_code=422,
            detail=f"Message too long: {len(message)} characters (max {MAX_MESSAGE_LENGTH})",
        )

    # Strip null bytes and control characters
    sanitised = _CONTROL_CHAR_RE.sub("", message)

    # Block messages matching prompt-injection patterns
    for pattern in _SUSPICIOUS_PATTERNS:
        if pattern.search(sanitised):
            logger.warning(
                "Blocked message matching prompt-injection pattern: %r (pattern=%s)",
                sanitised[:200],
                pattern.pattern,
            )
            raise HTTPException(
                status_code=422,
                detail="Message contains disallowed content.",
            )

    return sanitised
