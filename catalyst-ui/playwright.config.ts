import { defineConfig, devices } from "@playwright/test";

const configuredBaseUrl = process.env.PLAYWRIGHT_BASE_URL;
const baseURL = configuredBaseUrl ?? "http://127.0.0.1:4173";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: configuredBaseUrl
    ? undefined
    : {
        command: "npm run dev -- --host 127.0.0.1 --port 4173",
        url: baseURL,
        reuseExistingServer: true,
        timeout: 120_000,
      },
  projects: [
    {
      name: "deterministic",
      // The visual baseline is a review instrument, not a gate: font
      // rasterization differs between operating systems, so grading CI
      // against snapshots taken on a laptop reports differences that are
      // not changes. It runs on request instead.
      testIgnore: /visual-baseline\.spec\.ts/,
      use: {
        ...devices["Desktop Chrome"],
        video: "off",
      },
    },
    {
      name: "baseline",
      testMatch: /visual-baseline\.spec\.ts/,
      // One at a time. These share a dev server, and a screenshot taken while
      // a neighbour is mid-navigation records the neighbour's screen.
      fullyParallel: false,
      use: {
        ...devices["Desktop Chrome"],
        video: "off",
        // A baseline that moves because a caret blinked is worthless.
        launchOptions: { args: ["--force-prefers-reduced-motion"] },
      },
    },
    {
      name: "demo-video",
      testIgnore: /visual-baseline\.spec\.ts/,
      use: {
        ...devices["Desktop Chrome"],
        video: "on",
        trace: "on",
        screenshot: "on",
      },
    },
  ],
});
