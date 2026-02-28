"""OpenTelemetry configuration with OTLP exporter and FastAPI instrumentation."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

if TYPE_CHECKING:
    from fastapi import FastAPI

    from src.config.settings import Settings

logger = logging.getLogger(__name__)

# Module-level tracer for creating custom spans
tracer = trace.get_tracer("surf-api")


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
