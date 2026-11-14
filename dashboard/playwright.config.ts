// Playwright against `mu dashboard serve --dev` serving the BUILT files with the real headers (docs/design/dashboard.md
// §4.8). The server is started outside this process: by CI (dashboard.yml) or by tools/dashboard-npm e2e (outside the
// sandbox). DASH_E2E_PORT names its port; DASH_DEV_RUN its run/dev folder (the token).
import { defineConfig, devices } from "@playwright/test";

const port = Number(process.env.DASH_E2E_PORT ?? "47999");

export default defineConfig({
  testDir: "e2e",
  outputDir: "test-results",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"], ["html", { outputFolder: "playwright-report", open: "never" }]],
  use: { baseURL: `http://127.0.0.1:${port}`, trace: "retain-on-failure" },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "webkit", use: { ...devices["Desktop Safari"] } },
  ],
});
