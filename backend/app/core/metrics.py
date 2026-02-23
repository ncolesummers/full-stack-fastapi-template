from __future__ import annotations

from typing import Any, Literal

from fastapi import FastAPI, Request
from prometheus_client import Counter, Gauge
from prometheus_fastapi_instrumentator import Instrumentator
from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.core.config import settings

UNHANDLED_EXCEPTIONS_TOTAL = Counter(
    "unhandled_exceptions_total",
    "Count of unhandled exceptions by exception type and route path.",
    ["exception_type", "path"],
)
DB_CONNECTION_POOL_SIZE = Gauge(
    "db_connection_pool_size",
    "Database connection pool size by state.",
    ["state"],
)
LOGIN_ATTEMPTS_TOTAL = Counter(
    "login_attempts_total",
    "Total login attempts by result.",
    ["result"],
)
ITEMS_CREATED_TOTAL = Counter(
    "items_created_total",
    "Total number of items created.",
)

_metrics_initialized = False
_db_pool_metrics_registered = False


def _resolve_db_engine(db_engine: Engine | None) -> Engine:
    if db_engine is not None:
        return db_engine

    from app.core.db import engine as default_engine  # local import to avoid cycles

    return default_engine


def _resolve_path_label(request: Request) -> str:
    route = request.scope.get("route")
    route_path = getattr(route, "path", None)
    if isinstance(route_path, str) and route_path:
        return route_path
    return request.url.path


def record_unhandled_exception(exception_type: str, path: str) -> None:
    UNHANDLED_EXCEPTIONS_TOTAL.labels(exception_type=exception_type, path=path).inc()


def record_login_attempt(result: Literal["success", "failure"]) -> None:
    LOGIN_ATTEMPTS_TOTAL.labels(result=result).inc()


def record_item_created() -> None:
    ITEMS_CREATED_TOTAL.inc()


def update_db_pool_metrics(db_engine: Engine | None = None) -> None:
    engine = _resolve_db_engine(db_engine)
    pool = engine.pool
    checkedout = getattr(pool, "checkedout", None)
    checkedin = getattr(pool, "checkedin", None)
    size = getattr(pool, "size", None)

    active = float(checkedout()) if callable(checkedout) else 0.0

    if callable(checkedin):
        idle = float(checkedin())
    elif callable(size):
        idle = float(size() - active)
    else:
        idle = 0.0

    DB_CONNECTION_POOL_SIZE.labels(state="active").set(max(active, 0.0))
    DB_CONNECTION_POOL_SIZE.labels(state="idle").set(max(idle, 0.0))


def register_db_pool_metric_listeners(db_engine: Engine | None = None) -> None:
    global _db_pool_metrics_registered

    if _db_pool_metrics_registered:
        return

    engine = _resolve_db_engine(db_engine)

    def _refresh_pool_metrics(*_args: Any, **_kwargs: Any) -> None:
        update_db_pool_metrics(engine)

    for event_name in ("checkout", "checkin", "connect", "close", "invalidate"):
        event.listen(engine.pool, event_name, _refresh_pool_metrics)

    update_db_pool_metrics(engine)
    _db_pool_metrics_registered = True


def init_metrics(app: FastAPI) -> None:
    global _metrics_initialized

    if _metrics_initialized:
        return

    health_check_path = f"{settings.API_V1_STR}/utils/health-check/"
    instrumentator = Instrumentator(
        excluded_handlers=["/metrics", health_check_path],
        should_instrument_requests_inprogress=True,
    )
    instrumentator.instrument(app).expose(
        app,
        endpoint="/metrics",
        include_in_schema=False,
        should_gzip=True,
    )

    @app.middleware("http")
    async def count_unhandled_exceptions(request: Request, call_next: Any) -> Any:
        try:
            return await call_next(request)
        except Exception as exc:
            record_unhandled_exception(type(exc).__name__, _resolve_path_label(request))
            raise

    # Emit zero-value series for stable dashboards/alerts before first login event.
    LOGIN_ATTEMPTS_TOTAL.labels(result="success")
    LOGIN_ATTEMPTS_TOTAL.labels(result="failure")

    _metrics_initialized = True
