"""Tests for the Langfuse utility helpers."""

from unittest.mock import MagicMock, patch

import pytest

import src.middleware.langfuse_utils as lu


@pytest.fixture(autouse=True)
def _reset_enabled():
    """Reset the module-level caches between tests."""
    lu._enabled = None
    lu._client = None
    yield
    lu._enabled = None
    lu._client = None


class TestLangfuseEnabled:
    def test_disabled_when_base_url_empty(self):
        settings = MagicMock()
        settings.langfuse_base_url = ""
        with patch("src.config.settings.get_settings", return_value=settings):
            assert lu.langfuse_enabled() is False

    def test_enabled_when_base_url_set(self):
        settings = MagicMock()
        settings.langfuse_base_url = "http://localhost:3000"
        with patch("src.config.settings.get_settings", return_value=settings):
            assert lu.langfuse_enabled() is True

    def test_caches_result(self):
        settings = MagicMock()
        settings.langfuse_base_url = "http://localhost:3000"
        with patch("src.config.settings.get_settings", return_value=settings) as mock_gs:
            lu.langfuse_enabled()
            lu.langfuse_enabled()
            # get_settings called only once due to caching
            assert mock_gs.call_count == 1

    def test_returns_false_on_settings_exception(self):
        with patch(
            "src.config.settings.get_settings",
            side_effect=RuntimeError("no settings"),
        ):
            assert lu.langfuse_enabled() is False


class TestGetLangfuse:
    def test_returns_none_when_disabled(self):
        lu._enabled = False
        assert lu.get_langfuse() is None

    def test_returns_client_when_enabled(self):
        lu._enabled = True
        mock_client = MagicMock()
        settings = MagicMock()
        settings.langfuse_public_key = "pk-test"
        settings.langfuse_secret_key.get_secret_value.return_value = "sk-test"
        settings.langfuse_base_url = "http://localhost:3100"
        settings.langfuse_sample_rate = 1.0
        with (
            patch("langfuse.Langfuse", return_value=mock_client),
            patch("src.config.settings.get_settings", return_value=settings),
        ):
            assert lu.get_langfuse() is mock_client

    def test_caches_client_on_subsequent_calls(self):
        lu._enabled = True
        mock_client = MagicMock()
        settings = MagicMock()
        settings.langfuse_public_key = "pk-test"
        settings.langfuse_secret_key.get_secret_value.return_value = "sk-test"
        settings.langfuse_base_url = "http://localhost:3100"
        settings.langfuse_sample_rate = 1.0
        with (
            patch("langfuse.Langfuse", return_value=mock_client) as mock_cls,
            patch("src.config.settings.get_settings", return_value=settings),
        ):
            lu.get_langfuse()
            lu.get_langfuse()
            assert mock_cls.call_count == 1

    def test_returns_none_on_import_error(self):
        lu._enabled = True
        with (
            patch("langfuse.Langfuse", side_effect=RuntimeError("not initialised")),
            patch("src.config.settings.get_settings", return_value=MagicMock()),
        ):
            assert lu.get_langfuse() is None
