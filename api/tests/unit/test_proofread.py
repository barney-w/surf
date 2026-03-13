"""Tests for the LLM proofreading module."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents._proofread import proofread_message
from src.config.settings import Settings


def _settings(**overrides: object) -> Settings:
    defaults = {
        "anthropic_api_key": "test-key",
        "anthropic_proofread_model_id": "claude-haiku-4-5-20251001",
        "proofread_enabled": True,
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def _mock_response(text: str) -> MagicMock:
    block = MagicMock()
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    return resp


class TestProofreadShortMessage:
    @pytest.mark.asyncio
    async def test_short_message_skipped(self):
        result = await proofread_message("Hello!", _settings())
        assert result == "Hello!"

    @pytest.mark.asyncio
    async def test_empty_message_skipped(self):
        result = await proofread_message("", _settings())
        assert result == ""


class TestProofreadTimeout:
    @pytest.mark.asyncio
    async def test_timeout_returns_original(self):
        original = "Your annual leave entitlement is 20 days per year."
        with patch("src.agents._proofread._build_client") as mock_build:
            client = AsyncMock()
            client.messages.create = AsyncMock(side_effect=TimeoutError("timed out"))
            mock_build.return_value = client
            result = await proofread_message(original, _settings())
        assert result == original


class TestProofreadApiError:
    @pytest.mark.asyncio
    async def test_api_error_returns_original(self):
        original = "Your annual leave entitlement is 20 days per year."
        with patch("src.agents._proofread._build_client") as mock_build:
            client = AsyncMock()
            client.messages.create = AsyncMock(side_effect=RuntimeError("API down"))
            mock_build.return_value = client
            result = await proofread_message(original, _settings())
        assert result == original


class TestProofreadLengthDivergence:
    @pytest.mark.asyncio
    async def test_length_divergence_rejected(self):
        original = "Your annual leave entitlement is 20 days per year."
        bloated = original + " " * 200  # >30% longer
        with patch("src.agents._proofread._build_client") as mock_build:
            client = AsyncMock()
            client.messages.create = AsyncMock(return_value=_mock_response(bloated))
            mock_build.return_value = client
            result = await proofread_message(original, _settings())
        assert result == original


class TestProofreadSuccess:
    @pytest.mark.asyncio
    async def test_successful_correction(self):
        original = "Y own illness or injury entitles you to ** days per year**."
        corrected = "Your own illness or injury entitles you to **10 days per year**."
        with patch("src.agents._proofread._build_client") as mock_build:
            client = AsyncMock()
            client.messages.create = AsyncMock(return_value=_mock_response(corrected))
            mock_build.return_value = client
            result = await proofread_message(original, _settings())
        assert result == corrected

    @pytest.mark.asyncio
    async def test_no_change_returns_original(self):
        original = "Your annual leave entitlement is 20 days per year."
        with patch("src.agents._proofread._build_client") as mock_build:
            client = AsyncMock()
            client.messages.create = AsyncMock(return_value=_mock_response(original))
            mock_build.return_value = client
            result = await proofread_message(original, _settings())
        assert result == original
