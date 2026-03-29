"""Tests for Langfuse integration across the application.

Covers the span processor setup in telemetry, Langfuse scoring in the
response pipeline, and resilience when Langfuse is unavailable or raises.
"""

from __future__ import annotations

import types
from unittest.mock import MagicMock, patch

import pytest

import src.middleware.langfuse_utils as langfuse_utils


@pytest.fixture(autouse=True)
def _reset_langfuse_cache():
    """Reset the module-level ``_enabled`` cache between tests."""
    langfuse_utils._enabled = None
    yield
    langfuse_utils._enabled = None


def _make_secret(value: str) -> MagicMock:
    """Return a mock SecretStr."""
    secret = MagicMock()
    secret.get_secret_value.return_value = value
    return secret


def _make_settings(**overrides: object) -> MagicMock:
    """Return a minimal mock ``Settings`` with Langfuse fields."""
    settings = MagicMock()
    settings.environment = overrides.get("environment", "test")
    settings.langfuse_base_url = overrides.get("langfuse_base_url", "")
    settings.langfuse_public_key = overrides.get("langfuse_public_key", "pk-test")
    secret_val = overrides.get("langfuse_secret_key", "sk-test")
    settings.langfuse_secret_key = (
        secret_val if isinstance(secret_val, MagicMock) else _make_secret(str(secret_val))
    )
    settings.langfuse_sample_rate = overrides.get("langfuse_sample_rate", 1.0)
    settings.proofread_enabled = overrides.get("proofread_enabled", False)
    return settings


# ---------------------------------------------------------------------------
# 1. langfuse_enabled / get_langfuse
# ---------------------------------------------------------------------------


def test_langfuse_disabled_when_no_config():
    settings = _make_settings(langfuse_base_url="")
    with patch("src.config.settings.get_settings", return_value=settings):
        assert langfuse_utils.langfuse_enabled() is False
        assert langfuse_utils.get_langfuse() is None


def test_langfuse_enabled_when_configured():
    settings = _make_settings(langfuse_base_url="http://langfuse:3000")
    with patch("src.config.settings.get_settings", return_value=settings):
        assert langfuse_utils.langfuse_enabled() is True


# ---------------------------------------------------------------------------
# 2. setup_telemetry — span processor integration
# ---------------------------------------------------------------------------


def _run_setup_with_langfuse(
    settings: MagicMock,
    mock_processor_cls: MagicMock,
    fake_langfuse_top: types.ModuleType,
    fake_langfuse_mod: types.ModuleType,
) -> MagicMock:
    """Run setup_telemetry in no-op mode (no Azure, no OTLP) with Langfuse mocked.

    Returns the mock tracer provider so callers can assert on ``add_span_processor``.
    """
    from src.middleware.telemetry import setup_telemetry

    mock_provider = MagicMock()

    with (
        patch.dict("os.environ", {}, clear=False),
        patch.dict(
            "sys.modules",
            {
                "langfuse": fake_langfuse_top,
                "langfuse._client": types.ModuleType("langfuse._client"),
                "langfuse._client.span_processor": fake_langfuse_mod,
            },
        ),
        patch("src.middleware.telemetry._langfuse_reachable", return_value=True),
        patch("src.middleware.telemetry.trace") as mock_trace,
        patch("src.middleware.telemetry.FastAPIInstrumentor"),
    ):
        import os

        os.environ.pop("APPLICATIONINSIGHTS_CONNECTION_STRING", None)
        os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
        mock_trace.get_tracer_provider.return_value = mock_provider
        setup_telemetry(MagicMock(), settings)

    return mock_provider


def test_langfuse_span_processor_added_when_configured():
    settings = _make_settings(
        langfuse_base_url="http://langfuse:3000",
        langfuse_public_key="pk-test",
        langfuse_secret_key="sk-test",
    )

    mock_processor_cls = MagicMock()
    fake_langfuse_mod = types.ModuleType("langfuse._client.span_processor")
    fake_langfuse_mod.LangfuseSpanProcessor = mock_processor_cls  # type: ignore[attr-defined]

    mock_is_default = MagicMock()
    fake_langfuse_top = types.ModuleType("langfuse")
    fake_langfuse_top.is_default_export_span = mock_is_default  # type: ignore[attr-defined]

    mock_provider = _run_setup_with_langfuse(
        settings,
        mock_processor_cls,
        fake_langfuse_top,
        fake_langfuse_mod,
    )

    mock_processor_cls.assert_called_once_with(
        public_key="pk-test",
        secret_key="sk-test",
        base_url="http://langfuse:3000",
        should_export_span=mock_is_default,
    )
    mock_provider.add_span_processor.assert_called()


def test_langfuse_span_processor_not_added_when_unreachable():
    """Span processor is skipped when Langfuse is configured but not reachable."""
    from src.middleware.telemetry import setup_telemetry

    settings = _make_settings(
        langfuse_base_url="http://langfuse:3000",
        langfuse_public_key="pk-test",
        langfuse_secret_key="sk-test",
    )

    with (
        patch.dict("os.environ", {}, clear=False),
        patch("src.middleware.telemetry._langfuse_reachable", return_value=False),
        patch("src.middleware.telemetry.trace") as mock_trace,
        patch("src.middleware.telemetry.FastAPIInstrumentor"),
    ):
        import os

        os.environ.pop("APPLICATIONINSIGHTS_CONNECTION_STRING", None)
        os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
        mock_provider = MagicMock()
        mock_trace.get_tracer_provider.return_value = mock_provider
        setup_telemetry(MagicMock(), settings)

    mock_provider.add_span_processor.assert_not_called()


def test_langfuse_span_processor_not_added_when_unconfigured():
    from src.middleware.telemetry import setup_telemetry

    settings = _make_settings(langfuse_base_url="")

    with (
        patch.dict("os.environ", {}, clear=False),
        patch("src.middleware.telemetry.trace"),
        patch("src.middleware.telemetry.FastAPIInstrumentor"),
    ):
        import os

        os.environ.pop("APPLICATIONINSIGHTS_CONNECTION_STRING", None)
        os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
        setup_telemetry(MagicMock(), settings)

    # LangfuseSpanProcessor should never have been imported or instantiated.


def test_langfuse_should_export_span_forwarded():
    """is_default_export_span is passed as should_export_span to LangfuseSpanProcessor."""
    settings = _make_settings(
        langfuse_base_url="http://langfuse:3000",
        langfuse_public_key="pk-test",
        langfuse_secret_key="sk-test",
    )

    mock_processor_cls = MagicMock()
    fake_langfuse_mod = types.ModuleType("langfuse._client.span_processor")
    fake_langfuse_mod.LangfuseSpanProcessor = mock_processor_cls  # type: ignore[attr-defined]

    sentinel_fn = MagicMock(name="is_default_export_span")
    fake_langfuse_top = types.ModuleType("langfuse")
    fake_langfuse_top.is_default_export_span = sentinel_fn  # type: ignore[attr-defined]

    _run_setup_with_langfuse(settings, mock_processor_cls, fake_langfuse_top, fake_langfuse_mod)

    _, kwargs = mock_processor_cls.call_args
    assert kwargs["should_export_span"] is sentinel_fn


# ---------------------------------------------------------------------------
# 3. process_agent_response — Langfuse scoring
# ---------------------------------------------------------------------------


def _make_agent_response(**overrides: object) -> MagicMock:
    resp = MagicMock()
    resp.message = overrides.get("message", "Test response")
    resp.confidence = overrides.get("confidence", "medium")
    resp.sources = overrides.get("sources", [])
    resp.follow_up_suggestions = overrides.get("follow_up_suggestions", [])
    resp.model_copy = MagicMock(return_value=resp)
    return resp


@pytest.mark.asyncio
async def test_langfuse_failure_doesnt_break_pipeline():
    from src.services.response_pipeline import process_agent_response

    agent_response = _make_agent_response(confidence="high")
    mock_langfuse = MagicMock()
    mock_langfuse.score_current_trace.side_effect = RuntimeError("Langfuse connection failed")

    mock_gate = MagicMock()
    mock_gate.check = "passed"
    mock_gate.remediated = agent_response

    with (
        patch("src.services.response_pipeline.get_langfuse", return_value=mock_langfuse),
        patch("src.services.response_pipeline.run_quality_gate", return_value=mock_gate),
        patch("src.services.response_pipeline.AgentRegistry"),
    ):
        result, gate_result = await process_agent_response(agent_response, [], "test_agent")

    assert result is agent_response
    assert gate_result is mock_gate
    mock_langfuse.score_current_trace.assert_called()


@pytest.mark.asyncio
async def test_langfuse_scoring_called_in_pipeline():
    from src.services.response_pipeline import process_agent_response

    mock_src = MagicMock()
    mock_src.model_copy = MagicMock(return_value=mock_src)
    agent_response = _make_agent_response(confidence="high", sources=[mock_src, mock_src])
    mock_langfuse = MagicMock()

    mock_gate = MagicMock()
    mock_gate.check = "passed"
    mock_gate.remediated = agent_response

    with (
        patch("src.services.response_pipeline.get_langfuse", return_value=mock_langfuse),
        patch("src.services.response_pipeline.run_quality_gate", return_value=mock_gate),
        patch("src.services.response_pipeline.AgentRegistry"),
    ):
        result, _ = await process_agent_response(agent_response, [], "test_agent")

    assert mock_langfuse.score_current_trace.call_count == 3

    calls = mock_langfuse.score_current_trace.call_args_list
    call_names = [c.kwargs.get("name") or c[1].get("name", "") for c in calls]
    assert "quality_gate" in call_names
    assert "confidence" in call_names
    assert "source_count" in call_names


@pytest.mark.asyncio
async def test_pipeline_works_without_langfuse():
    from src.services.response_pipeline import process_agent_response

    agent_response = _make_agent_response(confidence="medium")

    mock_gate = MagicMock()
    mock_gate.check = "passed"
    mock_gate.remediated = agent_response

    with (
        patch("src.services.response_pipeline.get_langfuse", return_value=None),
        patch("src.services.response_pipeline.run_quality_gate", return_value=mock_gate),
        patch("src.services.response_pipeline.AgentRegistry"),
    ):
        result, gate_result = await process_agent_response(agent_response, [], "test_agent")

    assert result is agent_response
    assert gate_result.check == "passed"
