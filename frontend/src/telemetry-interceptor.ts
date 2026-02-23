import { context, propagation } from "@opentelemetry/api"
import type { AxiosRequestConfig } from "axios"
import { OpenAPI } from "./client"

let traceContextInterceptorRegistered = false

const toHeaderRecord = (
  headers: AxiosRequestConfig["headers"],
): Record<string, string> => {
  if (!headers) {
    return {}
  }

  const maybeAxiosHeaders = headers as {
    toJSON?: () => Record<string, unknown>
  }
  const serializedHeaders =
    typeof maybeAxiosHeaders.toJSON === "function"
      ? maybeAxiosHeaders.toJSON()
      : (headers as Record<string, unknown>)

  return Object.fromEntries(
    Object.entries(serializedHeaders)
      .filter(([, value]) => value !== undefined && value !== null)
      .map(([key, value]) => [key, String(value)]),
  )
}

export const traceContextInterceptor = (
  config: AxiosRequestConfig,
): AxiosRequestConfig => {
  const traceHeaders: Record<string, string> = {}
  propagation.inject(context.active(), traceHeaders)

  if (Object.keys(traceHeaders).length === 0) {
    return config
  }

  config.headers = {
    ...toHeaderRecord(config.headers),
    ...traceHeaders,
  }

  return config
}

export const registerTraceContextInterceptor = (): void => {
  if (traceContextInterceptorRegistered) {
    return
  }

  OpenAPI.interceptors.request.use(traceContextInterceptor)
  traceContextInterceptorRegistered = true
}
