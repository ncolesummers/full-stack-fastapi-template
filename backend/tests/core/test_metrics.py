from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from starlette.requests import Request

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


def test_resolve_db_engine_uses_default_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    default_engine = Mock()
    monkeypatch.setattr("app.core.db.engine", default_engine)
    assert metrics._resolve_db_engine(None) is default_engine


def test_resolve_path_label_falls_back_to_url_path() -> None:
    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/raw/path",
            "raw_path": b"/raw/path",
            "query_string": b"",
            "headers": [],
            "scope": {"route": object()},
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 80),
        }
    )
    assert metrics._resolve_path_label(request) == "/raw/path"


def test_update_db_pool_metrics_uses_size_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = Mock()
    pool.checkedout.return_value = 2
    pool.checkedin = None
    pool.size.return_value = 5
    db_engine = Mock(pool=pool)

    labels_calls: list[str] = []
    active_set = Mock()
    idle_set = Mock()

    def labels(*, state: str) -> Mock:
        labels_calls.append(state)
        return active_set if state == "active" else idle_set

    gauge = Mock()
    gauge.labels.side_effect = labels
    monkeypatch.setattr(metrics, "DB_CONNECTION_POOL_SIZE", gauge)

    metrics.update_db_pool_metrics(db_engine)

    assert labels_calls == ["active", "idle"]
    active_set.set.assert_called_once_with(2.0)
    idle_set.set.assert_called_once_with(3.0)


def test_update_db_pool_metrics_uses_zero_idle_when_no_pool_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = Mock()
    pool.checkedout = None
    pool.checkedin = None
    pool.size = None
    db_engine = Mock(pool=pool)

    labels_calls: list[str] = []
    active_set = Mock()
    idle_set = Mock()

    def labels(*, state: str) -> Mock:
        labels_calls.append(state)
        return active_set if state == "active" else idle_set

    gauge = Mock()
    gauge.labels.side_effect = labels
    monkeypatch.setattr(metrics, "DB_CONNECTION_POOL_SIZE", gauge)

    metrics.update_db_pool_metrics(db_engine)

    assert labels_calls == ["active", "idle"]
    active_set.set.assert_called_once_with(0.0)
    idle_set.set.assert_called_once_with(0.0)


def test_register_db_pool_metric_listeners_returns_when_already_registered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(metrics, "_db_pool_metrics_registered", True)
    listener = Mock()
    monkeypatch.setattr(metrics.event, "listen", listener)

    metrics.register_db_pool_metric_listeners(Mock())

    listener.assert_not_called()


def test_init_metrics_records_unhandled_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    instrumentator_cls = Mock()
    instrumentator_instance = Mock()
    instrumentator_instance.instrument.return_value = instrumentator_instance
    instrumentator_instance.expose.return_value = instrumentator_instance
    instrumentator_cls.return_value = instrumentator_instance

    recorded_exception = Mock()

    @app.get("/explode/{item_id}")
    def explode(item_id: str) -> None:
        raise RuntimeError(item_id)

    monkeypatch.setattr(metrics, "_metrics_initialized", False)
    monkeypatch.setattr(metrics, "Instrumentator", instrumentator_cls)
    monkeypatch.setattr(metrics, "record_unhandled_exception", recorded_exception)

    metrics.init_metrics(app)

    client = TestClient(app)
    with pytest.raises(RuntimeError):
        client.get("/explode/123")

    recorded_exception.assert_called_once_with(
        "RuntimeError",
        "/explode/{item_id}",
    )
