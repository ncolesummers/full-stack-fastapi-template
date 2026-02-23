/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_URL: string
  readonly VITE_OTEL_COLLECTOR_URL?: string
  readonly VITE_OTEL_SERVICE_NAME?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
