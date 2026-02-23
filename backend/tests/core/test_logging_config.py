import json
import logging
from collections.abc import Generator
from unittest.mock import Mock

import pytest
import structlog
from opentelemetry.trace import SpanContext, TraceFlags, TraceState
from pydantic import ValidationError

from app.core import logging_config
from app.core.config import Settings


@pytest.fixture(scope="session", autouse=True)
def db() -> Generator[None, None, None]:
    """
    Override the global autouse DB fixture from tests/conftest.py for this module.

    These tests are pure unit tests and intentionally skip database setup.
    """
    yield


@pytest.fixture(autouse=True)
def reset_logging_state(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    root_logger = logging.getLogger()
    original_handlers = root_logger.handlers.copy()
    original_level = root_logger.level

    monkeypatch.setattr(logging_config, "_logging_configured", False)
    monkeypatch.setattr(logging_config, "_logging_instrumented", False)
    monkeypatch.setattr(logging_config, "_otel_log_handler", None)
    monkeypatch.setattr(
        logging_config.LoggingInstrumentor,
        "instrument",
        Mock(),
    )

    yield

    root_logger.handlers = original_handlers
    root_logger.setLevel(original_level)
    structlog.reset_defaults()


def test_setup_logging_local_uses_console_renderer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(logging_config.settings, "ENVIRONMENT", "local")
    monkeypatch.setattr(logging_config.settings, "LOG_LEVEL", None)
    monkeypatch.setattr(logging_config.settings, "OTEL_ENABLED", False)

    logging_config.setup_logging()

    root_logger = logging.getLogger()
    assert root_logger.level == logging.DEBUG
    assert len(root_logger.handlers) == 1

    formatter = root_logger.handlers[0].formatter
    assert formatter is not None

    record = logging.LogRecord(
        name="test.local",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="console output",
        args=(),
        exc_info=None,
    )
    formatted = formatter.format(record)
    assert "console output" in formatted
    assert not formatted.lstrip().startswith("{")


def test_setup_logging_non_local_uses_json_renderer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(logging_config.settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(logging_config.settings, "LOG_LEVEL", None)
    monkeypatch.setattr(logging_config.settings, "OTEL_ENABLED", False)

    logging_config.setup_logging()

    root_logger = logging.getLogger()
    assert root_logger.level == logging.WARNING
    assert len(root_logger.handlers) == 1

    formatter = root_logger.handlers[0].formatter
    assert formatter is not None

    record = logging.LogRecord(
        name="test.production",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="json output",
        args=(),
        exc_info=None,
    )
    parsed = json.loads(formatter.format(record))
    assert parsed["message"] == "json output"
    assert parsed["level"] == "info"
    assert parsed["service"] == logging_config.settings.OTEL_SERVICE_NAME
    assert parsed["environment"] == "production"
    assert "timestamp" in parsed


def test_add_trace_context_includes_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    span_context = SpanContext(
        trace_id=int("0af7651916cd43dd8448eb211c80319c", 16),
        span_id=int("b7ad6b7169203331", 16),
        is_remote=False,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
        trace_state=TraceState(),
    )
    span = Mock()
    span.get_span_context.return_value = span_context
    monkeypatch.setattr(
        logging_config.trace, "get_current_span", Mock(return_value=span)
    )

    event_dict = logging_config._add_trace_context(None, "info", {})
    assert event_dict["trace_id"] == "0af7651916cd43dd8448eb211c80319c"
    assert event_dict["span_id"] == "b7ad6b7169203331"
    assert event_dict["trace_flags"] == "01"


def test_add_trace_context_skips_when_span_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    span_context = SpanContext(
        trace_id=0,
        span_id=0,
        is_remote=False,
        trace_flags=TraceFlags(0x00),
        trace_state=TraceState(),
    )
    span = Mock()
    span.get_span_context.return_value = span_context
    monkeypatch.setattr(
        logging_config.trace, "get_current_span", Mock(return_value=span)
    )

    original = {"message": "hello"}
    event_dict = logging_config._add_trace_context(None, "info", original.copy())
    assert event_dict == original


def test_effective_log_level_prefers_explicit_log_level() -> None:
    base_settings = {
        "PROJECT_NAME": "Test Project",
        "SECRET_KEY": "not-changethis-secret",
        "POSTGRES_SERVER": "localhost",
        "POSTGRES_USER": "postgres",
        "POSTGRES_PASSWORD": "not-changethis-password",
        "FIRST_SUPERUSER": "admin@example.com",
        "FIRST_SUPERUSER_PASSWORD": "supersecret",
    }

    parsed = Settings.model_validate(
        {**base_settings, "ENVIRONMENT": "production", "LOG_LEVEL": "debug"}
    )
    assert parsed.effective_log_level == "DEBUG"

    parsed_default = Settings.model_validate(
        {**base_settings, "ENVIRONMENT": "production"}
    )
    assert parsed_default.effective_log_level == "WARNING"

    with pytest.raises(ValidationError):
        Settings.model_validate(
            {**base_settings, "ENVIRONMENT": "local", "LOG_LEVEL": "verbose"}
        )


def test_setup_logging_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(logging_config.settings, "ENVIRONMENT", "local")
    monkeypatch.setattr(logging_config.settings, "LOG_LEVEL", None)
    monkeypatch.setattr(logging_config.settings, "OTEL_ENABLED", False)

    instrument = Mock()
    monkeypatch.setattr(logging_config.LoggingInstrumentor, "instrument", instrument)

    logging_config.setup_logging()
    first_handlers = logging.getLogger().handlers.copy()
    logging_config.setup_logging()
    second_handlers = logging.getLogger().handlers.copy()

    assert len(first_handlers) == 1
    assert len(second_handlers) == 1
    assert first_handlers[0] is second_handlers[0]
    instrument.assert_called_once_with(set_logging_format=False)
