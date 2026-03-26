"""Langfuse integration utilities.

Provides safe helpers that never raise — Langfuse failures must not
break the application.  When ``langfuse_base_url`` is empty (the default),
all helpers short-circuit and return ``None`` / ``False``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_enabled: bool | None = None


def langfuse_enabled() -> bool:
    """Return True if Langfuse is configured and available."""
    global _enabled  # noqa: PLW0603
    if _enabled is None:
        try:
            from src.config.settings import get_settings

            s = get_settings()
            _enabled = bool(s.langfuse_base_url and s.langfuse_public_key)
        except Exception:
            _enabled = False
    return _enabled


def get_langfuse():  # noqa: ANN201
    """Return the Langfuse client singleton, or ``None`` if disabled."""
    if not langfuse_enabled():
        return None
    try:
        from langfuse import get_client

        return get_client()
    except Exception:
        logger.warning("Failed to get Langfuse client", exc_info=True)
        return None
