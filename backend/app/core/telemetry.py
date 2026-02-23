import logging
from importlib import metadata

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

from app.core.config import settings
from app.core.db import engine

logger = logging.getLogger(__name__)

_telemetry_initialized = False


def _get_service_version() -> str:
    try:
        return metadata.version("app")
    except metadata.PackageNotFoundError:
        return "unknown"


def init_telemetry(app: FastAPI) -> None:
    """
    Initialize OpenTelemetry tracing and auto-instrument framework/libraries.
    """
    global _telemetry_initialized

    if _telemetry_initialized or not settings.OTEL_ENABLED:
        return

    resource = Resource.create(
        {
            "service.name": settings.OTEL_SERVICE_NAME,
            "service.version": _get_service_version(),
            "deployment.environment": settings.ENVIRONMENT,
        }
    )
    sampler = ParentBased(root=TraceIdRatioBased(settings.OTEL_SAMPLING_RATE))
    tracer_provider = TracerProvider(resource=resource, sampler=sampler)

    otel_endpoint = settings.OTEL_EXPORTER_OTLP_ENDPOINT
    # gRPC exporter uses insecure transport only for explicit http:// endpoints.
    # https:// and bare host:port endpoints should use secure transport.
    insecure = otel_endpoint.startswith("http://")
    span_exporter = OTLPSpanExporter(
        endpoint=otel_endpoint,
        insecure=insecure,
    )
    tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
    trace.set_tracer_provider(tracer_provider)

    FastAPIInstrumentor.instrument_app(app)
    SQLAlchemyInstrumentor().instrument(engine=engine)
    HTTPXClientInstrumentor().instrument()

    _telemetry_initialized = True
    logger.info(
        "OpenTelemetry instrumentation initialized",
        extra={
            "otel_service_name": settings.OTEL_SERVICE_NAME,
            "otel_sampling_rate": settings.OTEL_SAMPLING_RATE,
        },
    )
