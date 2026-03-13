"""LLM proofreading pass — fix generation artefacts before final delivery.

Sends the agent's message to a fast model (Haiku) with a tightly-scoped prompt:
fix only obvious generation artefacts (dropped characters, broken markdown),
never change meaning.  Falls back to the original text on any error.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.config.settings import Settings

logger = logging.getLogger(__name__)

_MIN_LENGTH = 20  # skip very short messages
_MAX_LENGTH_DRIFT = 0.30  # reject corrections that change length by >30%
_TIMEOUT_SECONDS = 3.0

_SYSTEM_PROMPT = (
    "You are a proofreader for AI-generated text. "
    "Fix ONLY obvious generation artefacts:\n"
    "- Dropped or truncated characters (e.g. 'Y own illness' → 'Your own illness')\n"
    "- Broken markdown formatting (e.g. '** days**' → '**20 days**')\n"
    "- Clearly incomplete words at boundaries\n\n"
    "Rules:\n"
    "- NEVER change meaning, add information, or rephrase\n"
    "- NEVER add or remove sentences\n"
    "- NEVER change factual content (numbers, names, dates)\n"
    "- If the text looks correct, return it exactly as-is\n"
    "- Return ONLY the corrected text, no commentary"
)


def _build_client(settings: Settings) -> object:
    """Build a raw Anthropic client reusing the same auth config as agents."""
    if settings.anthropic_foundry_base_url:
        from anthropic import AsyncAnthropicFoundry

        return AsyncAnthropicFoundry(
            base_url=settings.anthropic_foundry_base_url,
            api_key=settings.anthropic_foundry_api_key,
        )
    from anthropic import AsyncAnthropic

    return AsyncAnthropic(api_key=settings.anthropic_api_key or None)


async def proofread_message(message: str, settings: Settings) -> str:
    """Proofread *message* using a fast model, returning the corrected text.

    Returns the original message unchanged when:
    - The message is shorter than ``_MIN_LENGTH`` characters
    - The API call fails or times out
    - The corrected text diverges in length by more than ``_MAX_LENGTH_DRIFT``
    """
    if len(message) < _MIN_LENGTH:
        return message

    try:
        client = _build_client(settings)
        response = await asyncio.wait_for(
            client.messages.create(
                model=settings.anthropic_proofread_model_id,
                max_tokens=4096,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": message}],
            ),
            timeout=_TIMEOUT_SECONDS,
        )

        corrected = response.content[0].text  # type: ignore[union-attr]

        # Length guard — reject wildly different output
        original_len = len(message)
        if original_len > 0:
            drift = abs(len(corrected) - original_len) / original_len
            if drift > _MAX_LENGTH_DRIFT:
                logger.warning(
                    "proofread: rejected correction — length drift %.1f%% exceeds %.0f%% threshold",
                    drift * 100,
                    _MAX_LENGTH_DRIFT * 100,
                )
                return message

        return corrected

    except TimeoutError:
        logger.warning("proofread: timed out after %.1fs — using original", _TIMEOUT_SECONDS)
        return message
    except Exception:
        logger.warning("proofread: API error — using original", exc_info=True)
        return message
