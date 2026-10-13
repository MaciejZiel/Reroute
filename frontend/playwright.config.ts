import { defineConfig } from "@playwright/test";

// The API is mocked in every test (e2e/mock.ts), so only the built-in dev server is needed.
// PW_CHANNEL=chrome uses an installed Chrome instead of the downloaded Chromium.
export default defineConfig({
  testDir: "e2e",
  fullyParallel: true,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "list" : "line",
  use: { baseURL: "http://127.0.0.1:5198", channel: process.env.PW_CHANNEL || undefined },
  webServer: {
    command: "npx vite --port 5198 --host 127.0.0.1",
    url: "http://127.0.0.1:5198",
    reuseExistingServer: !process.env.CI,
  },
});
