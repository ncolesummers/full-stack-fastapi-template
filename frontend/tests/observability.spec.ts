import { expect, test } from "@playwright/test"

const TRACEPARENT_PATTERN = /^00-[0-9a-f]{32}-[0-9a-f]{16}-0[0-3]$/

test("API requests include a W3C traceparent header", async ({ page }) => {
  const observedTraceparents = new Set<string>()

  page.on("request", (request) => {
    if (!request.url().includes("/api/v1/")) {
      return
    }

    const traceparent = request.headers().traceparent
    if (traceparent) {
      observedTraceparents.add(traceparent)
    }
  })

  await page.goto("/")
  await page.waitForResponse(
    (response) =>
      response.url().includes("/api/v1/users/me") && response.status() === 200,
  )

  expect(observedTraceparents.size).toBeGreaterThan(0)
  for (const traceparent of observedTraceparents) {
    expect(traceparent).toMatch(TRACEPARENT_PATTERN)
  }
})
