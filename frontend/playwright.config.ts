import { defineConfig, devices } from "@playwright/test";

/**
 * The end-to-end gate: ask → citation → click → `<mark>` in view.
 *
 * That chain is the product's central claim, and it is the one thing no unit
 * test can verify. The offset arithmetic is unit-tested, the SSE parser is
 * unit-tested against recorded bytes, the server verifies every citation
 * against the stored document before persisting it — and none of that proves a
 * real browser renders a highlight over the right words.
 *
 * Runs against the live compose stack rather than a mocked API, because the
 * assertion is about the *whole* chain: Django's offsets, the wire format, the
 * parser, the renderer, and the DOM.
 */
export default defineConfig({
  testDir: "./e2e",
  // A stub answer streams for a couple of seconds by design (LLM_FAKE_DELAY_S),
  // so the default 5s expect timeout is too tight for "answer finished".
  expect: { timeout: 15_000 },
  timeout: 90_000,
  fullyParallel: false,
  // One worker: every test drives the same session-per-context against one
  // backend, and the demo seed refuses a non-empty workspace.
  workers: 1,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
