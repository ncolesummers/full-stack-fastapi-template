import { SpanStatusCode, trace } from "@opentelemetry/api"
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-http"
import { DocumentLoadInstrumentation } from "@opentelemetry/instrumentation-document-load"
import { XMLHttpRequestInstrumentation } from "@opentelemetry/instrumentation-xml-http-request"
import { resourceFromAttributes } from "@opentelemetry/resources"
import { BatchSpanProcessor } from "@opentelemetry/sdk-trace-base"
import { WebTracerProvider } from "@opentelemetry/sdk-trace-web"
import { ATTR_SERVICE_NAME } from "@opentelemetry/semantic-conventions"

const DEFAULT_OTEL_COLLECTOR_URL = "http://localhost:4318"
const DEFAULT_OTEL_SERVICE_NAME = "react-frontend"

let telemetryInitialized = false

const removeTrailingSlash = (value: string): string => value.replace(/\/+$/, "")

const getTraceExporterUrl = (collectorUrl: string): string =>
  `${removeTrailingSlash(collectorUrl)}/v1/traces`

const escapeRegex = (value: string): string =>
  value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")

const toError = (value: unknown): Error => {
  if (value instanceof Error) {
    return value
  }

  if (typeof value === "string") {
    return new Error(value)
  }

  try {
    return new Error(JSON.stringify(value))
  } catch {
    return new Error("Unknown frontend error")
  }
}

const recordFrontendError = (
  serviceName: string,
  spanName: string,
  error: unknown,
  attributes: Record<string, string | number>,
): void => {
  const tracer = trace.getTracer(serviceName)
  const span = tracer.startSpan(spanName)
  const normalizedError = toError(error)

  span.recordException(normalizedError)
  span.setStatus({
    code: SpanStatusCode.ERROR,
    message: normalizedError.message,
  })

  for (const [key, value] of Object.entries(attributes)) {
    span.setAttribute(key, value)
  }

  span.end()
}

const registerFrontendErrorHandlers = (serviceName: string): void => {
  window.addEventListener("error", (event) => {
    recordFrontendError(
      serviceName,
      "frontend.unhandled_error",
      event.error ?? event.message,
      {
        "error.source": event.filename || window.location.href,
        "error.line": event.lineno,
        "error.column": event.colno,
      },
    )
  })

  window.addEventListener("unhandledrejection", (event) => {
    recordFrontendError(
      serviceName,
      "frontend.unhandled_rejection",
      event.reason,
      {
        "error.source": "promise",
      },
    )
  })
}

export const initTelemetry = (): void => {
  if (telemetryInitialized) {
    return
  }

  try {
    const collectorUrl =
      import.meta.env.VITE_OTEL_COLLECTOR_URL || DEFAULT_OTEL_COLLECTOR_URL
    const serviceName =
      import.meta.env.VITE_OTEL_SERVICE_NAME || DEFAULT_OTEL_SERVICE_NAME
    const apiBaseUrl = import.meta.env.VITE_API_URL
    const traceExporterUrl = getTraceExporterUrl(collectorUrl)

    const traceExporter = new OTLPTraceExporter({
      timeoutMillis: 3000,
      url: traceExporterUrl,
    })

    const provider = new WebTracerProvider({
      resource: resourceFromAttributes({
        [ATTR_SERVICE_NAME]: serviceName,
      }),
      spanProcessors: [new BatchSpanProcessor(traceExporter)],
    })

    // Defaults include W3C TraceContext + StackContextManager, so no zone.js is required.
    provider.register()

    const xhrInstrumentation = new XMLHttpRequestInstrumentation({
      ignoreUrls: [traceExporterUrl],
      propagateTraceHeaderCorsUrls: [new RegExp(`^${escapeRegex(apiBaseUrl)}`)],
    })
    xhrInstrumentation.setTracerProvider(provider)
    xhrInstrumentation.enable()

    const documentLoadInstrumentation = new DocumentLoadInstrumentation()
    documentLoadInstrumentation.setTracerProvider(provider)
    documentLoadInstrumentation.enable()

    registerFrontendErrorHandlers(serviceName)
    telemetryInitialized = true
  } catch (error) {
    telemetryInitialized = true
    console.warn(
      "[telemetry] Failed to initialize frontend OpenTelemetry",
      error,
    )
  }
}
