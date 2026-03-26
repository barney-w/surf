"""Tests for api/src/middleware/telemetry.py — OTel setup and metric instruments."""

from __future__ import annotations

import types
from unittest.mock import MagicMock, patch

import pytest


def _make_settings(**overrides: object) -> MagicMock:
    """Return a minimal mock ``Settings``."""
    settings = MagicMock()
    settings.environment = overrides.get("environment", "dev")
    settings.langfuse_base_url = overrides.get("langfuse_base_url", "")
    settings.langfuse_public_key = overrides.get("langfuse_public_key", "")
    settings.langfuse_sample_rate = overrides.get("langfuse_sample_rate", 1.0)
    secret = MagicMock()
    secret.get_secret_value.return_value = overrides.get("langfuse_secret_key", "")
    settings.langfuse_secret_key = secret
    return settings


# ---------------------------------------------------------------------------
# 1. setup_telemetry mode selection
# ---------------------------------------------------------------------------


class TestSetupTelemetryModes:
    """Verify setup_telemetry() selects the correct mode."""

    def test_azure_monitor_mode(self, monkeypatch: pytest.MonkeyPatch):
        """When APPLICATIONINSIGHTS_CONNECTION_STRING is set, configure_azure_monitor is called."""
        monkeypatch.setenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "InstrumentationKey=test")

        mock_cam = MagicMock()
        with (
            patch("src.middleware.telemetry.FastAPIInstrumentor"),
            patch.dict(
                "sys.modules",
                {"azure.monitor.opentelemetry": types.ModuleType("azure.monitor.opentelemetry")},
            ),
            patch("azure.monitor.opentelemetry.configure_azure_monitor", mock_cam, create=True),
        ):
            from src.middleware.telemetry import setup_telemetry

            setup_telemetry(MagicMock(), _make_settings())

        mock_cam.assert_called_once()

    def test_local_otlp_mode(self, monkeypatch: pytest.MonkeyPatch):
        """When OTEL_EXPORTER_OTLP_ENDPOINT is set (no Azure), OTLP exporters are configured."""
        monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")

        mock_tp_instance = MagicMock()
        mock_mp_instance = MagicMock()
        mock_tp_cls = MagicMock(return_value=mock_tp_instance)
        mock_mp_cls = MagicMock(return_value=mock_mp_instance)

        # Build fake module tree for the lazy imports inside setup_telemetry
        fake_sdk_trace = types.ModuleType("opentelemetry.sdk.trace")
        fake_sdk_trace.TracerProvider = mock_tp_cls  # type: ignore[attr-defined]

        fake_sdk_trace_export = types.ModuleType("opentelemetry.sdk.trace.export")
        fake_sdk_trace_export.BatchSpanProcessor = MagicMock()  # type: ignore[attr-defined]

        fake_sdk_metrics = types.ModuleType("opentelemetry.sdk.metrics")
        fake_sdk_metrics.MeterProvider = mock_mp_cls  # type: ignore[attr-defined]

        fake_sdk_metrics_export = types.ModuleType("opentelemetry.sdk.metrics.export")
        fake_sdk_metrics_export.PeriodicExportingMetricReader = MagicMock()  # type: ignore[attr-defined]

        fake_sdk_resources = types.ModuleType("opentelemetry.sdk.resources")
        fake_sdk_resources.Resource = MagicMock()  # type: ignore[attr-defined]

        fake_otlp_trace = types.ModuleType("opentelemetry.exporter.otlp.proto.grpc.trace_exporter")
        fake_otlp_trace.OTLPSpanExporter = MagicMock()  # type: ignore[attr-defined]

        fake_otlp_metric = types.ModuleType("opentelemetry.exporter.otlp.proto.grpc.metric_exporter")
        fake_otlp_metric.OTLPMetricExporter = MagicMock()  # type: ignore[attr-defined]

        fake_modules = {
            "opentelemetry.sdk.trace": fake_sdk_trace,
            "opentelemetry.sdk.trace.export": fake_sdk_trace_export,
            "opentelemetry.sdk.metrics": fake_sdk_metrics,
            "opentelemetry.sdk.metrics.export": fake_sdk_metrics_export,
            "opentelemetry.sdk.resources": fake_sdk_resources,
            "opentelemetry.exporter.otlp.proto.grpc.trace_exporter": fake_otlp_trace,
            "opentelemetry.exporter.otlp.proto.grpc.metric_exporter": fake_otlp_metric,
            # Intermediate packages needed for import resolution
            "opentelemetry.exporter": types.ModuleType("opentelemetry.exporter"),
            "opentelemetry.exporter.otlp": types.ModuleType("opentelemetry.exporter.otlp"),
            "opentelemetry.exporter.otlp.proto": types.ModuleType("opentelemetry.exporter.otlp.proto"),
            "opentelemetry.exporter.otlp.proto.grpc": types.ModuleType("opentelemetry.exporter.otlp.proto.grpc"),
            "opentelemetry.sdk": types.ModuleType("opentelemetry.sdk"),
        }

        with (
            patch("src.middleware.telemetry.FastAPIInstrumentor"),
            patch("src.middleware.telemetry.trace") as mock_trace,
            patch("src.middleware.telemetry.metrics") as mock_metrics,
            patch.dict("sys.modules", fake_modules),
        ):
            from src.middleware.telemetry import setup_telemetry

            setup_telemetry(MagicMock(), _make_settings())

        mock_trace.set_tracer_provider.assert_called_once_with(mock_tp_instance)
        mock_metrics.set_meter_provider.assert_called_once_with(mock_mp_instance)

    def test_noop_mode_dev(self, monkeypatch: pytest.MonkeyPatch):
        """When no exporters are configured in dev, warning logged but no error."""
        monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

        with patch("src.middleware.telemetry.FastAPIInstrumentor"):
            from src.middleware.telemetry import setup_telemetry

            # Should not raise
            setup_telemetry(MagicMock(), _make_settings(environment="dev"))

    def test_noop_mode_non_dev_logs_error(self, monkeypatch: pytest.MonkeyPatch):
        """Non-dev with no exporter should log an error-level message."""
        monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

        with (
            patch("src.middleware.telemetry.FastAPIInstrumentor"),
            patch("src.middleware.telemetry.logger") as mock_logger,
        ):
            from src.middleware.telemetry import setup_telemetry

            setup_telemetry(MagicMock(), _make_settings(environment="staging"))

        # At least one error-level log should have been emitted
        mock_logger.error.assert_called()


# ---------------------------------------------------------------------------
# 2. Dead code removal
# ---------------------------------------------------------------------------


class TestDeletedFunctions:
    """Verify dead span helpers were removed."""

    def test_create_anthropic_span_removed(self):
        """create_anthropic_span should no longer exist."""
        from src.middleware import telemetry

        assert not hasattr(telemetry, "create_anthropic_span")

    def test_span_request_routing_removed(self):
        """span_request_routing should no longer exist."""
        from src.middleware import telemetry

        assert not hasattr(telemetry, "span_request_routing")

    def test_span_conversation_persistence_exists(self):
        """span_conversation_persistence should still exist."""
        from src.middleware import telemetry

        assert hasattr(telemetry, "span_conversation_persistence")
        assert callable(telemetry.span_conversation_persistence)


# ---------------------------------------------------------------------------
# 3. Metric instruments
# ---------------------------------------------------------------------------


class TestMetricInstrumentsExist:
    """Verify all metric instruments are defined at module level."""

    def test_all_instruments_accessible(self):
        from src.middleware.telemetry import (
            chat_duration,
            chat_tokens,
            quality_gate_triggers,
            rag_results_count,
            rag_search_duration,
            rate_limit_hits,
            workflow_timeouts,
        )

        for inst in [
            chat_duration,
            chat_tokens,
            quality_gate_triggers,
            rag_results_count,
            rag_search_duration,
            rate_limit_hits,
            workflow_timeouts,
        ]:
            assert inst is not None

    def test_record_token_usage_callable(self):
        from src.middleware.telemetry import record_token_usage

        # Should not raise with valid args
        record_token_usage(10, 20, agent_name="test")


# ---------------------------------------------------------------------------
# 4. span_conversation_persistence
# ---------------------------------------------------------------------------


class TestSpanConversationPersistence:
    """Verify span_conversation_persistence creates a span context manager."""

    def test_returns_context_manager(self):
        from src.middleware.telemetry import span_conversation_persistence

        cm = span_conversation_persistence("test-conv-id")
        # Should be usable as a context manager
        assert hasattr(cm, "__enter__") and hasattr(cm, "__exit__")
