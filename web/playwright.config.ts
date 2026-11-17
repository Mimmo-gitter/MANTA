import { defineConfig, devices } from "@playwright/test";
import fs from "fs";

const hasProductionBuild = fs.existsSync("./.next/BUILD_ID");

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.PLAYWRIGHT_WORKERS ? Number(process.env.PLAYWRIGHT_WORKERS) : (process.env.CI ? 2 : 1),
  reporter: "html",
  use: {
    baseURL: process.env.PLAYWRIGHT_TEST_BASE_URL || "http://localhost:3000",
    trace: "on-first-retry",
    screenshot: "only-on-failure"
  },
  projects: process.env.CI || !process.env.ALL_BROWSERS
    ? [
        {
          name: "chromium",
          use: { ...devices["Desktop Chrome"] }
        }
      ]
    : [
        {
          name: "chromium",
          use: { ...devices["Desktop Chrome"] }
        },
        {
          name: "firefox",
          use: { ...devices["Desktop Firefox"] }
        },
        {
          name: "webkit",
          use: { ...devices["Desktop Safari"] }
        }
      ],
  webServer: {
    command: process.platform === "win32"
      ? (hasProductionBuild ? "npm.cmd run start" : "npm.cmd run dev")
      : (hasProductionBuild ? "npm run start" : "npm run dev"),
    url: "http://localhost:3000",
    reuseExistingServer: !process.env.CI,
    timeout: 120 * 1000
  }
});
