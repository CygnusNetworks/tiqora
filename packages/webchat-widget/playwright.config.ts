import { defineConfig, devices } from "@playwright/test";

// The widget e2e needs two origins (a host page embedding the widget, and "Tiqora"), so
// the mock server listens on three loopback ports: 5190 Tiqora (frame + API), 5191 an
// allowed host page, 5192 a host page that is not allowed. Run `pnpm build` first.
export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  expect: { timeout: 8_000 },
  fullyParallel: false,
  workers: 1, // the mock server keeps one in-memory state
  retries: 0,
  reporter: [["list"]],
  use: { trace: "off", locale: "de-DE" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "node scripts/mock-server.mjs",
    url: "http://127.0.0.1:5190/__test/health",
    // Never adopt a foreign server that happens to hold the port.
    reuseExistingServer: false,
    timeout: 15_000,
    stdout: "pipe",
  },
});
