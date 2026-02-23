from collections.abc import Generator
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from app.core import telemetry
from app.core.config import Settings


@pytest.fixture(scope="session", autouse=True)
def db() -> Generator[None, None, None]:
    yield


def test_otel_sampling_rate_validation() -> None:
    base_settings = {
        "PROJECT_NAME": "Test Project",
        "POSTGRES_SERVER": "localhost",
        "POSTGRES_USER": "postgres",
        "FIRST_SUPERUSER": "admin@example.com",
        "FIRST_SUPERUSER_PASSWORD": "supersecret",
    }

    Settings.model_validate({**base_settings, "OTEL_SAMPLING_RATE": 0.5})

    with pytest.raises(ValidationError):
        Settings.model_validate({**base_settings, "OTEL_SAMPLING_RATE": 1.5})

    with pytest.raises(ValidationError):
        Settings.model_validate({**base_settings, "OTEL_SAMPLING_RATE": -0.1})


def test_init_telemetry_noop_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    app = FastAPI()
    fastapi_instrument = Mock()
    sqlalchemy_instrument = Mock()
    httpx_instrument = Mock()

    monkeypatch.setattr(telemetry, "_telemetry_initialized", False)
    monkeypatch.setattr(telemetry.settings, "OTEL_ENABLED", False)
    monkeypatch.setattr(
        telemetry.FastAPIInstrumentor, "instrument_app", fastapi_instrument
    )
    monkeypatch.setattr(
        telemetry.SQLAlchemyInstrumentor, "instrument", sqlalchemy_instrument
    )
    monkeypatch.setattr(
        telemetry.HTTPXClientInstrumentor, "instrument", httpx_instrument
    )

    telemetry.init_telemetry(app)

    fastapi_instrument.assert_not_called()
    sqlalchemy_instrument.assert_not_called()
    httpx_instrument.assert_not_called()
    assert telemetry._telemetry_initialized is False


def test_init_telemetry_initializes_once(monkeypatch: pytest.MonkeyPatch) -> None:
    app = FastAPI()
    mock_provider = Mock()
    tracer_provider = Mock(return_value=mock_provider)
    span_exporter = Mock()
    span_processor = Mock()
    set_tracer_provider = Mock()
    resource_create = Mock()
    fastapi_instrument = Mock()
    sqlalchemy_instrument = Mock()
    httpx_instrument = Mock()

    monkeypatch.setattr(telemetry, "_telemetry_initialized", False)
    monkeypatch.setattr(telemetry.settings, "OTEL_ENABLED", True)
    monkeypatch.setattr(telemetry.settings, "OTEL_SERVICE_NAME", "fastapi-backend")
    monkeypatch.setattr(telemetry.settings, "OTEL_SAMPLING_RATE", 1.0)
    monkeypatch.setattr(
        telemetry.settings,
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "http://otel-collector:4317",
    )
    monkeypatch.setattr(telemetry.Resource, "create", resource_create)
    monkeypatch.setattr(telemetry, "TracerProvider", tracer_provider)
    monkeypatch.setattr(telemetry, "OTLPSpanExporter", span_exporter)
    monkeypatch.setattr(telemetry, "BatchSpanProcessor", span_processor)
    monkeypatch.setattr(telemetry.trace, "set_tracer_provider", set_tracer_provider)
    monkeypatch.setattr(
        telemetry.FastAPIInstrumentor, "instrument_app", fastapi_instrument
    )
    monkeypatch.setattr(
        telemetry.SQLAlchemyInstrumentor, "instrument", sqlalchemy_instrument
    )
    monkeypatch.setattr(
        telemetry.HTTPXClientInstrumentor, "instrument", httpx_instrument
    )

    telemetry.init_telemetry(app)
    telemetry.init_telemetry(app)

    resource_create.assert_called_once()
    tracer_provider.assert_called_once()
    span_exporter.assert_called_once_with(
        endpoint="http://otel-collector:4317",
        insecure=True,
    )
    span_processor.assert_called_once_with(span_exporter.return_value)
    mock_provider.add_span_processor.assert_called_once_with(
        span_processor.return_value
    )
    set_tracer_provider.assert_called_once_with(mock_provider)
    fastapi_instrument.assert_called_once_with(app)
    assert sqlalchemy_instrument.call_count == 1
    assert httpx_instrument.call_count == 1
    assert telemetry._telemetry_initialized is True
