"""Input validation middleware for sanitising and validating user messages."""

import re

from fastapi import HTTPException

MAX_MESSAGE_LENGTH = 10_000

# Control characters except \n, \r, \t (which are harmless whitespace)
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def validate_message(message: str) -> str:
    """Validate and sanitise a user message.

    - Rejects messages longer than *MAX_MESSAGE_LENGTH* with HTTP 422.
    - Strips null bytes and control characters.

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

    return sanitised
