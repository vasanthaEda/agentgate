"""OpenTelemetry wiring: one tracer, shared across the whole call path.

Every gateway invocation produces a single root span (``agentgate.invoke``)
with child spans for auth, policy evaluation, rate/budget checks, and the
downstream call -- so a single trace shows exactly where time went and where
a request was rejected. No exporter is configured by default (safe for
offline tests); set ``AGENTGATE_OTEL_CONSOLE_EXPORT=true`` to print spans, or
point an OTLP exporter at the provider in a real deployment.
"""
from __future__ import annotations

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

from app.config import settings

_initialized = False


def setup_tracing() -> trace.Tracer:
    global _initialized
    if not _initialized:
        resource = Resource.create({"service.name": settings.otel_service_name})
        provider = TracerProvider(resource=resource)
        if settings.otel_console_export:
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        trace.set_tracer_provider(provider)
        _initialized = True
    return trace.get_tracer(settings.otel_service_name)


def get_tracer() -> trace.Tracer:
    return setup_tracing()
