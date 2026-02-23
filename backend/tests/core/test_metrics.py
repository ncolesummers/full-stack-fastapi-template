from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core import metrics
from app.core.db import engine
from tests.utils.metrics import get_metric_value


def test_metrics_endpoint_returns_prometheus_text(client: TestClient) -> None:
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "http_requests_total" in response.text
    assert "http_request_duration_seconds" in response.text
    assert (
        "http_requests_inprogress" in response.text
        or "http_requests_in_progress" in response.text
    )


def test_metrics_endpoint_exposes_custom_metric_families(client: TestClient) -> None:
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "unhandled_exceptions_total" in response.text
    assert "db_connection_pool_size" in response.text
    assert "login_attempts_total" in response.text
    assert "items_created_total" in response.text


def test_init_metrics_initializes_once(monkeypatch: pytest.MonkeyPatch) -> None:
    app = FastAPI()
    instrumentator_cls = Mock()
    instrumentator_instance = Mock()
    register_pool_metrics = Mock()

    instrumentator_instance.instrument.return_value = instrumentator_instance
    instrumentator_instance.expose.return_value = instrumentator_instance
    instrumentator_cls.return_value = instrumentator_instance

    monkeypatch.setattr(metrics, "_metrics_initialized", False)
    monkeypatch.setattr(metrics, "Instrumentator", instrumentator_cls)
    monkeypatch.setattr(
        metrics, "register_db_pool_metric_listeners", register_pool_metrics
    )

    metrics.init_metrics(app)
    metrics.init_metrics(app)

    instrumentator_cls.assert_called_once_with(
        excluded_handlers=["/metrics", "/api/v1/utils/health-check/"],
        should_instrument_requests_inprogress=True,
    )
    instrumentator_instance.instrument.assert_called_once_with(app)
    instrumentator_instance.expose.assert_called_once_with(
        app,
        endpoint="/metrics",
        include_in_schema=False,
        should_gzip=True,
    )


def test_db_pool_metrics_are_non_negative(client: TestClient) -> None:
    with Session(engine) as session:
        session.exec(select(1)).one()

    active = get_metric_value(client, "db_connection_pool_size", {"state": "active"})
    idle = get_metric_value(client, "db_connection_pool_size", {"state": "idle"})

    assert active >= 0
    assert idle >= 0
