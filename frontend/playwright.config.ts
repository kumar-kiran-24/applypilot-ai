import { defineConfig, devices } from "@playwright/test";

const FRONTEND_PORT = 3100;
const MOCK_API_PORT = 8010;
const BASE_URL = `http://127.0.0.1:${FRONTEND_PORT}`;

export default defineConfig({
  testDir: "./e2e",
  outputDir: "test-results",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 90_000,
  expect: { timeout: 15_000 },
  reporter: [["list"]],
  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: [
    {
      command: "node e2e/mock-backend.mjs",
      url: `http://127.0.0.1:${MOCK_API_PORT}/health`,
      reuseExistingServer: false,
      timeout: 20_000,
      env: { MOCK_API_PORT: String(MOCK_API_PORT) },
    },
    {
      // A production build removes Next.js dev-server recompiles, which made
      // navigation and reloads flaky on a cold cache.
      command:
        `npx next build && npx next start -H 127.0.0.1 -p ${FRONTEND_PORT}`,
      url: `${BASE_URL}/login`,
      reuseExistingServer: false,
      timeout: 300_000,
      env: {
        NEXT_PUBLIC_API_BASE_URL: `http://127.0.0.1:${MOCK_API_PORT}/api/v1`,
        SESSION_MAX_AGE_SECONDS: "3600",
        SESSION_COOKIE_SECURE: "false",
        NEXT_TELEMETRY_DISABLED: "1",
      },
    },
  ],
});
