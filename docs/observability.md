# Observability Strategy

## Overview

This document describes the observability architecture for the full-stack-fastapi-template. It covers distributed tracing, metrics, structured logging, and dashboards using open-source tooling.

**Stack:** OpenTelemetry + Jaeger + Prometheus + Grafana + Loki

## Architecture

```
  Browser (React + OTEL Web SDK)
         │
         │  W3C traceparent headers via OTEL XHR instrumentation
         │
         ▼
      Traefik
         │
         ▼
  ┌──────────────────┐
  │     FastAPI       │──── OTLP/gRPC ────► OTEL Collector
  │  (auto-instrum.)  │                      │    │
  │  /metrics ◄──────Prometheus              │    │
  └────────┬─────────┘                       │    │
           │                            Jaeger  Loki
        PostgreSQL                    (traces) (logs)
                                         │      │
                                    ┌────▼──────▼───┐
                                    │    Grafana     │
                                    │ (unified UI)   │
                                    └────────────────┘
```

### Data Flows

| Source | Destination | Protocol | Data |
|--------|-------------|----------|------|
| React frontend | OTEL Collector (:4318) | OTLP/HTTP | Browser traces (page loads, XHR requests) |
| React frontend | FastAPI backend | HTTP | `traceparent` header on every API request |
| FastAPI backend | OTEL Collector (:4317) | OTLP/gRPC | Server traces + OTLP-exported JSON logs |
| FastAPI backend | Container stdout/stderr | Console / JSON | Human-readable local logs and runtime diagnostics |
| Prometheus | FastAPI `/metrics` | HTTP pull | Four Golden Signals metrics |
| OTEL Collector | Jaeger | OTLP/gRPC | Distributed traces |
| OTEL Collector | Loki | Loki push API | Structured JSON logs |
| Grafana | Jaeger, Prometheus, Loki | Native queries | Dashboards, exploration |

### Component Responsibilities

| Component | Purpose | Port |
|-----------|---------|------|
| **OTEL Collector** | Central telemetry pipeline — receives, processes, exports | 4317 (gRPC), 4318 (HTTP) |
| **Jaeger** | Trace storage and visualization | 16686 (UI) |
| **Prometheus** | Metrics storage and querying (PromQL) | 9090 (UI) |
| **Loki** | Log aggregation and querying (LogQL) | 3100 |
| **Grafana** | Unified dashboards across all three backends | 3000 |

## Key Design Decisions

### Trace Context Propagation

Frontend-to-backend trace continuity is achieved through OpenTelemetry browser instrumentation in `frontend/src/telemetry.ts`. `XMLHttpRequestInstrumentation` automatically creates client spans for Axios requests and injects W3C `traceparent` / `tracestate` headers.

`WebTracerProvider` in the browser registers the default W3C TraceContext propagator, so requests stay correlated without patching generated client files under `frontend/src/client/`. The backend's FastAPI auto-instrumentation extracts those headers and creates child spans, resulting in a single trace across frontend → backend → database.

### Metrics Model

Metrics use a **pull-based** Prometheus model. The `prometheus-fastapi-instrumentator` library automatically exposes HTTP request metrics (latency histograms, request counters by status/method/path) on a `/metrics` endpoint that Prometheus scrapes every 10 seconds.

Custom business metrics (login attempts, items created, DB pool state) are defined manually using the Prometheus Python client.

For clean SLO math, the backend excludes self-observability noise endpoints (`/metrics`, `f"{settings.API_V1_STR}/utils/health-check/"`) from automatic HTTP metrics.

Custom labels are intentionally low cardinality. Route labels use path templates (for example, `/api/v1/items/{id}`) instead of raw IDs to avoid unbounded time series growth.

The metrics set in this issue is intentionally focused:
- **Platform reliability metrics** track service health (latency, traffic, errors, saturation).
- **Business metrics** track domain outcomes (`login_attempts_total`, `items_created_total`) to connect technical signals to user behavior.

### Structured Logging

`structlog` replaces basic Python `logging` with a dual-path design:

1. **Stdout path** for operator-facing runtime logs
2. **OTLP log path** for centralized query and log-to-trace correlation in Loki/Jaeger

The `opentelemetry-instrumentation-logging` package and trace-context processors inject `trace_id`, `span_id`, and `trace_flags` into request-context logs so Grafana can jump directly from logs to traces.

- **Local development:** Human-readable console output
- **Non-local environments:** JSON structured logs
- **All environments with `OTEL_ENABLED=true`:** OTLP-exported JSON logs to Collector/Loki

This mirrors real SRE operations: local readability for fast debugging, structured centralized logs for incident response, and deterministic correlation to traces for root-cause analysis.

### Sentry Coexistence

Sentry continues to work alongside OpenTelemetry. When both are configured, Sentry uses `instrumenter="otel"` to read from OTEL spans rather than creating its own. This means Sentry receives the same trace data as Jaeger, providing error alerting on top of the OTEL trace pipeline.

### Sampling Strategy

| Environment | Trace Sampling | Log Level |
|-------------|---------------|-----------|
| `local` | 100% | DEBUG+ |
| `staging` | 10% | INFO+ |
| `production` | 1% | WARNING+ |

Uses `parentbased_traceidratio` sampler so that if a frontend trace is sampled, all its backend spans are also sampled (parent trace decision is respected).

## Metrics Design (Four Golden Signals)

### Latency
- `http_request_duration_seconds` — Histogram of HTTP request durations by method, handler, status

### Traffic
- `http_requests_total` — Counter of total HTTP requests by method, handler, status
- `http_requests_inprogress` — Gauge of currently in-flight requests (emitted by `prometheus-fastapi-instrumentator`)

### Errors
- `http_requests_total{status=~"4xx|5xx"}` — Subset of request counter for error responses
- `unhandled_exceptions_total` — Counter of unhandled exceptions by type and path

### Saturation
- `db_connection_pool_size` — Gauge of DB connection pool by state (active/idle)

### Business Metrics
- `login_attempts_total{result=success|failure}` — Counter of login attempts
- `items_created_total` — Counter of items created

### Future Work (Not in Issue #3 Scope)
- `db_query_duration_seconds` — query-level DB latency histogram
- `process_resident_memory_bytes` — process memory saturation
- `process_cpu_seconds_total` — process CPU saturation

## Grafana Dashboard

The pre-provisioned "Application Overview" dashboard has six rows:

1. **Traffic & Errors** — Request rate, error rate %, active requests gauge, top endpoints table
2. **Latency** — P50/P95/P99 time series, latency heatmap, slow endpoints table
3. **Database** — Query latency P95, connection pool active/idle, pool overflow events
4. **System Resources** — CPU usage, memory usage
5. **Business Metrics** — Login attempts by result, items created rate
6. **Traces & Logs** — Recent traces from Jaeger, error logs from Loki with clickable trace_id links

Template variables: `$interval` (auto), `$method`, `$endpoint`

## Local Development

### Starting the Stack

```bash
docker compose up
```

All observability services start alongside the application. Access:

| Service | URL |
|---------|-----|
| Application | http://localhost:5173 |
| Jaeger UI | http://localhost:16686 |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 (admin/admin) |
| OTEL Collector (gRPC) | localhost:4317 |
| OTEL Collector (HTTP) | localhost:4318 |

### Verifying Traces

1. Open the application and perform some actions (login, create items)
2. Open Jaeger UI at http://localhost:16686
3. Select service "fastapi-backend" or "react-frontend"
4. Click "Find Traces" to see distributed traces spanning frontend → backend → database

### Verifying Metrics

1. Open Prometheus at http://localhost:9090
2. Query `http_requests_total` to see request counts
3. Query `histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))` for P95 latency

### PromQL Cookbook (Issue #3)

Use these directly in Prometheus or Grafana Explore to validate the implementation:

```promql
# Traffic: total request rate (RPS)
sum(rate(http_requests_total[5m]))

# Latency: global p95 by histogram buckets
histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket[5m])) by (le))

# Errors: server-side error rate (%)
100 *
sum(rate(http_requests_total{status=~"5xx"}[5m]))
/
sum(rate(http_requests_total[5m]))

# Business: successful logins / minute
sum(increase(login_attempts_total{result="success"}[1m]))

# Business: failed logins / minute
sum(increase(login_attempts_total{result="failure"}[1m]))

# Business: item creations / 5m
sum(increase(items_created_total[5m]))

# Saturation: DB pool active/idle
db_connection_pool_size{state="active"}
db_connection_pool_size{state="idle"}
```

### Verifying Logs

1. Open Grafana at http://localhost:3000
2. Go to Explore, select Loki data source
3. Generate backend traffic, for example: `curl -s http://localhost:8000/api/v1/utils/health-check/ > /dev/null`
4. Query `{service_name="fastapi-backend"}` to see structured logs
5. Confirm log entries include `trace_id` and `span_id`
6. Click any `trace_id` value to jump to the corresponding trace in Jaeger

## Configuration Reference

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OTEL_SERVICE_NAME` | `fastapi-backend` | Backend service name in traces |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://otel-collector:4317` | OTEL Collector gRPC endpoint |
| `OTEL_SAMPLING_RATE` | `1.0` | Trace sampling ratio (0.0 to 1.0) |
| `OTEL_ENABLED` | `true` | Enable/disable OTEL instrumentation |
| `LOG_LEVEL` | unset | Optional log-level override (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`) |
| `VITE_OTEL_COLLECTOR_URL` | `http://localhost:4318` | OTEL Collector HTTP endpoint (frontend) |
| `VITE_OTEL_SERVICE_NAME` | `react-frontend` | Frontend service name in traces |

### Adding Custom Spans (Backend)

```python
from opentelemetry import trace

tracer = trace.get_tracer(__name__)

@tracer.start_as_current_span("my_operation")
def my_function():
    span = trace.get_current_span()
    span.set_attribute("my.attribute", "value")
    # ... business logic ...
```

### Adding Custom Metrics (Backend)

```python
from prometheus_client import Counter

my_counter = Counter(
    "my_operation_total",
    "Description of my counter",
    ["label1", "label2"]
)

def my_function():
    my_counter.labels(label1="value1", label2="value2").inc()
```

## Troubleshooting

### No traces in Jaeger
- Verify OTEL Collector is running: `docker compose ps otel-collector`
- Check collector logs: `docker compose logs otel-collector`
- Verify backend OTLP endpoint: check `OTEL_EXPORTER_OTLP_ENDPOINT` env var
- For frontend: check browser console for OTEL errors, verify `VITE_OTEL_COLLECTOR_URL`

### No metrics in Prometheus
- Verify backend `/metrics` endpoint: `curl http://localhost:8000/metrics`
- Check Prometheus targets: http://localhost:9090/targets
- Ensure backend service is healthy in Docker Compose

### Logs not appearing in Loki
- Check Loki is running: `docker compose ps loki`
- Verify OTEL Collector log pipeline in `otel-collector-config.yaml`
- Check collector logs for export errors: `docker compose logs otel-collector`

### Frontend traces not linking to backend
- Verify `traceparent` header is present in browser DevTools Network tab
- Check CORS allows the `traceparent` header (should be covered by `allow_headers=["*"]`)
- Ensure both frontend and backend OTEL are sending to the same Collector instance
