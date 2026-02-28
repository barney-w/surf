"""Input validation middleware for sanitising and validating user messages."""

import logging
import re

from fastapi import HTTPException

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 10_000

# Patterns that may indicate prompt-injection or abuse (log only, never block)
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
    - Logs (but does **not** block) messages matching suspicious patterns.

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

    # Log suspicious patterns (no blocking)
    for pattern in _SUSPICIOUS_PATTERNS:
        if pattern.search(sanitised):
            logger.warning(
                "Suspicious pattern detected in message: %r (pattern=%s)",
                sanitised[:200],
                pattern.pattern,
            )
            break  # one log line is enough

    return sanitised
