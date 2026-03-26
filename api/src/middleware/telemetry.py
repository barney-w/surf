"""OpenTelemetry configuration with dual-mode support.

Supports two telemetry back-ends selected at startup:

1. **Azure Monitor mode** — activated when the ``APPLICATIONINSIGHTS_CONNECTION_STRING``
   env var is present.  Calls ``configure_azure_monitor()`` which registers a
   TracerProvider, MeterProvider *and* LoggerProvider automatically, so all metric
   instruments and spans flow to Application Insights.

2. **Local / OTLP fallback** — used when no Azure connection string is found.
   * If ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set, a real TracerProvider with
     ``OTLPSpanExporter`` **and** a real MeterProvider with ``OTLPMetricExporter``
     are created so both traces and metrics reach a local collector.
   * If neither env var is set, no-op providers are used and a warning is logged.

In **both** modes the optional Langfuse span processor is attached when
``settings.langfuse_base_url`` is configured, and ``FastAPIInstrumentor`` is
applied to the FastAPI application.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from opentelemetry import metrics, trace
from opentelemetry.instrumentation.fastapi import (  # pyright: ignore[reportMissingTypeStubs]
    FastAPIInstrumentor,
)

if TYPE_CHECKING:
    from fastapi import FastAPI

    from src.config.settings import Settings

logger = logging.getLogger(__name__)

# Module-level tracer for creating custom spans
tracer = trace.get_tracer("surf-api")

# ---------------------------------------------------------------------------
# Metrics instruments
# ---------------------------------------------------------------------------
meter = metrics.get_meter("surf-api")

# Histograms
chat_duration = meter.create_histogram(
    "surf.chat.duration_seconds",
    description="Chat request duration",
    unit="s",
)

rag_search_duration = meter.create_histogram(
    "surf.rag.search_duration_seconds",
    description="RAG search duration",
    unit="s",
)

rag_results_count = meter.create_histogram(
    "surf.rag.results_count",
    description="Number of RAG results returned",
)

# Counters
chat_tokens = meter.create_counter(
    "surf.chat.tokens_total",
    description="Token count by agent and direction",
)

quality_gate_triggers = meter.create_counter(
    "surf.quality_gate.triggers_total",
    description="Quality gate trigger count",
)

rate_limit_hits = meter.create_counter(
    "surf.rate_limit.hits_total",
    description="Rate limit hit count",
)

workflow_timeouts = meter.create_counter(
    "surf.workflow.timeout_total",
    description="Workflow timeout count",
)


def record_token_usage(input_tokens: int, output_tokens: int, agent_name: str = "unknown") -> None:
    """Increment the ``surf.chat.tokens_total`` counter with direction labels."""
    chat_tokens.add(input_tokens, {"agent": agent_name, "direction": "input"})
    chat_tokens.add(output_tokens, {"agent": agent_name, "direction": "output"})


# ---------------------------------------------------------------------------
# Langfuse span processor helper (shared by both modes)
# ---------------------------------------------------------------------------


def _attach_langfuse(settings: Settings) -> None:
    """Add a ``LangfuseSpanProcessor`` to the active TracerProvider if configured."""
    if not settings.langfuse_base_url:
        return
    try:
        from langfuse import is_default_export_span
        from langfuse._client.span_processor import LangfuseSpanProcessor

        provider = trace.get_tracer_provider()
        # The real SDK TracerProvider exposes add_span_processor; the no-op
        # ProxyTracerProvider does not — guard against that.
        if hasattr(provider, "add_span_processor"):
            provider.add_span_processor(  # type: ignore[union-attr]
                LangfuseSpanProcessor(
                    public_key=settings.langfuse_public_key,
                    secret_key=settings.langfuse_secret_key.get_secret_value(),
                    base_url=settings.langfuse_base_url,
                    should_export_span=is_default_export_span,
                )
            )
            logger.info("Langfuse span processor added (base_url=%s)", settings.langfuse_base_url)
        else:
            logger.warning(
                "Cannot attach Langfuse — tracer provider does not support span processors"
            )
    except Exception:
        logger.warning("Failed to initialise Langfuse span processor", exc_info=True)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def setup_telemetry(app: FastAPI, settings: Settings) -> None:
    """Initialise OpenTelemetry and instrument FastAPI.

    This must be called **before** the orchestrator/agents are created so their
    built-in traces flow through the same exporter pipeline.

    Parameters
    ----------
    app:
        The FastAPI application instance to instrument.
    settings:
        Application settings (used for resource attributes).
    """
    ai_conn_str = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")

    if ai_conn_str:
        # ── Azure Monitor mode ──────────────────────────────────────────
        from azure.monitor.opentelemetry import configure_azure_monitor

        configure_azure_monitor(
            connection_string=ai_conn_str,
            resource_attributes={
                "service.name": "surf-api",
                "deployment.environment": settings.environment,
            },
        )
        logger.info(
            "Azure Monitor telemetry configured (service.name=surf-api, env=%s)",
            settings.environment,
        )
    else:
        # ── Local / OTLP fallback ──────────────────────────────────────
        otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")

        if otlp_endpoint:
            from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
                OTLPMetricExporter,
            )
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )
            from opentelemetry.sdk.metrics import MeterProvider
            from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            resource = Resource.create(
                {
                    "service.name": "surf-api",
                    "deployment.environment": settings.environment,
                }
            )

            # Traces
            tracer_provider = TracerProvider(resource=resource)
            tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
            trace.set_tracer_provider(tracer_provider)

            # Metrics
            metric_reader = PeriodicExportingMetricReader(OTLPMetricExporter())
            meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
            metrics.set_meter_provider(meter_provider)

            logger.info(
                "OTLP telemetry configured (endpoint=%s, service.name=surf-api, env=%s)",
                otlp_endpoint,
                settings.environment,
            )
        elif settings.langfuse_base_url:
            # No OTLP exporter, but Langfuse is configured — create a real
            # TracerProvider so the Langfuse span processor can attach to it.
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider

            resource = Resource.create(
                {
                    "service.name": "surf-api",
                    "deployment.environment": settings.environment,
                }
            )
            trace.set_tracer_provider(TracerProvider(resource=resource))
            logger.info(
                "TracerProvider created for Langfuse (no OTLP exporter, env=%s)",
                settings.environment,
            )
        else:
            # No exporter configured — no-op providers are already the default
            if settings.environment == "dev":
                logger.info(
                    "OTEL_EXPORTER_OTLP_ENDPOINT not set"
                    " — telemetry data will be discarded (expected in dev)"
                )
            else:
                logger.error(
                    "OTEL_EXPORTER_OTLP_ENDPOINT not set — telemetry data will be discarded"
                )

    # ── Langfuse (works in all modes) ───────────────────────────────────
    _attach_langfuse(settings)

    # ── Instrument FastAPI ──────────────────────────────────────────────
    FastAPIInstrumentor.instrument_app(app)

    logger.info(
        "OpenTelemetry initialised (service.name=surf-api, env=%s)",
        settings.environment,
    )


def span_conversation_persistence(conversation_id: str):  # noqa: ANN201
    """Context manager span for conversation persistence operations."""
    return tracer.start_as_current_span(
        "conversation.persistence",
        attributes={"conversation.id": conversation_id},
    )
