import logging
from importlib import metadata
from typing import Any

import structlog
from opentelemetry import trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.trace import SpanContext
from structlog.processors import CallsiteParameter

from app.core.config import settings

_logging_configured = False
_logging_instrumented = False
_otel_log_handler: LoggingHandler | None = None


def _get_service_version() -> str:
    try:
        return metadata.version("app")
    except metadata.PackageNotFoundError:
        return "unknown"


def _add_service_context(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    event_dict.setdefault("service", settings.OTEL_SERVICE_NAME)
    event_dict.setdefault("environment", settings.ENVIRONMENT)
    return event_dict


def _add_trace_context(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    span_context: SpanContext = trace.get_current_span().get_span_context()
    if not span_context.is_valid:
        return event_dict

    event_dict.setdefault("trace_id", f"{span_context.trace_id:032x}")
    event_dict.setdefault("span_id", f"{span_context.span_id:016x}")
    event_dict.setdefault("trace_flags", f"{int(span_context.trace_flags):02x}")
    return event_dict


def _drop_color_message(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    # Uvicorn may populate colorized message variants that are noisy in JSON output.
    event_dict.pop("color_message", None)
    return event_dict


def _normalize_message_key(
    _: Any, __: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    if "event" in event_dict and "message" not in event_dict:
        event_dict["message"] = event_dict.pop("event")
    return event_dict


def _shared_processors() -> list[Any]:
    return [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.CallsiteParameterAdder(
            {
                CallsiteParameter.FILENAME,
                CallsiteParameter.FUNC_NAME,
                CallsiteParameter.LINENO,
            }
        ),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _add_service_context,
        _add_trace_context,
        _drop_color_message,
        _normalize_message_key,
    ]


def _build_formatter(renderer: Any, processors: list[Any]) -> logging.Formatter:
    return structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=processors,
    )


def _build_resource() -> Resource:
    return Resource.create(
        {
            "service.name": settings.OTEL_SERVICE_NAME,
            "service.version": _get_service_version(),
            "deployment.environment": settings.ENVIRONMENT,
        }
    )


def _configure_otel_log_handler(processors: list[Any]) -> LoggingHandler | None:
    global _otel_log_handler

    if not settings.OTEL_ENABLED:
        return None

    if _otel_log_handler is not None:
        return _otel_log_handler

    logger_provider = LoggerProvider(resource=_build_resource())
    otel_endpoint = settings.OTEL_EXPORTER_OTLP_ENDPOINT
    log_exporter = OTLPLogExporter(
        endpoint=otel_endpoint,
        insecure=otel_endpoint.startswith("http://"),
    )
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
    set_logger_provider(logger_provider)

    otel_log_handler = LoggingHandler(
        level=logging.NOTSET, logger_provider=logger_provider
    )
    otel_log_handler.setFormatter(
        _build_formatter(structlog.processors.JSONRenderer(), processors)
    )
    _otel_log_handler = otel_log_handler
    return _otel_log_handler


def _configure_uvicorn_loggers() -> None:
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
        uvicorn_logger = logging.getLogger(logger_name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True


def setup_logging(*, force: bool = False) -> None:
    global _logging_configured, _logging_instrumented

    if _logging_configured and not force:
        return

    processors = _shared_processors()
    stdout_renderer: Any
    if settings.ENVIRONMENT == "local":
        stdout_renderer = structlog.dev.ConsoleRenderer()
    else:
        stdout_renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            *processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    stdout_handler = logging.StreamHandler()
    stdout_handler.setFormatter(_build_formatter(stdout_renderer, processors))

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(settings.effective_log_level)
    root_logger.addHandler(stdout_handler)

    otel_handler = _configure_otel_log_handler(processors)
    if otel_handler is not None:
        root_logger.addHandler(otel_handler)

    _configure_uvicorn_loggers()

    if not _logging_instrumented:
        LoggingInstrumentor().instrument(set_logging_format=False)
        _logging_instrumented = True

    _logging_configured = True
