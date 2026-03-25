"""OpenTelemetry configuration with OTLP exporter and FastAPI instrumentation."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import (  # pyright: ignore[reportMissingTypeStubs]
    FastAPIInstrumentor,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

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


def record_token_usage(
    input_tokens: int, output_tokens: int, agent_name: str = "unknown"
) -> None:
    """Increment the ``surf.chat.tokens_total`` counter with direction labels."""
    chat_tokens.add(input_tokens, {"agent": agent_name, "direction": "input"})
    chat_tokens.add(output_tokens, {"agent": agent_name, "direction": "output"})


def setup_telemetry(app: FastAPI, settings: Settings) -> None:
    """Initialise OpenTelemetry tracing and instrument FastAPI.

    This must be called **before** the orchestrator/agents are created so their
    built-in traces flow through the same exporter pipeline.

    Parameters
    ----------
    app:
        The FastAPI application instance to instrument.
    settings:
        Application settings (used for resource attributes).
    """
    resource = Resource.create(
        {
            "service.name": "surf-api",
            "deployment.environment": settings.environment,
        }
    )

    provider = TracerProvider(resource=resource)

    # OTLP gRPC exporter — sends to a local collector by default
    # (OTEL_EXPORTER_OTLP_ENDPOINT env var can override the destination)
    exporter = OTLPSpanExporter()
    provider.add_span_processor(BatchSpanProcessor(exporter))

    trace.set_tracer_provider(provider)

    # Instrument FastAPI — this wraps every incoming request in a span
    FastAPIInstrumentor.instrument_app(app)

    logger.info(
        "OpenTelemetry initialised (service.name=surf-api, env=%s)",
        settings.environment,
    )

    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        if settings.environment == "dev":
            logger.info(
                "OTEL_EXPORTER_OTLP_ENDPOINT not set"
                " — telemetry data will be discarded (expected in dev)"
            )
        else:
            logger.error("OTEL_EXPORTER_OTLP_ENDPOINT not set — telemetry data will be discarded")


def create_anthropic_span(model_id: str, agent_name: str = "unknown"):
    """Create a span for an Anthropic API call."""
    return tracer.start_as_current_span(
        "anthropic.messages.create",
        attributes={
            "llm.model": model_id,
            "llm.agent": agent_name,
        },
    )


def span_conversation_persistence(conversation_id: str) -> trace.Span:
    """Start a custom span for conversation persistence operations."""
    return tracer.start_span(
        "conversation.persistence",
        attributes={"conversation.id": conversation_id},
    )


def span_request_routing(path: str, method: str) -> trace.Span:
    """Start a custom span for request routing decisions."""
    return tracer.start_span(
        "request.routing",
        attributes={"http.route": path, "http.method": method},
    )
