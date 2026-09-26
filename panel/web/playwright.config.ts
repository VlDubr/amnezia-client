import { defineConfig, devices } from "@playwright/test";

const API_PORT = 8765;
const WEB_PORT = 5174;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: [["list"]],
  use: { baseURL: `http://127.0.0.1:${WEB_PORT}`, locale: "ru-RU", trace: "retain-on-failure" },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "phone", use: { ...devices["Pixel 7"] }, testMatch: /mobile\.spec\.ts/ },
  ],
  webServer: [
    {
      command: "uv run python -m tests.e2e_server",
      cwd: "../backend",
      url: `http://127.0.0.1:${API_PORT}/api/health`,
      timeout: 180_000,
      reuseExistingServer: false,
      env: { E2E_API_PORT: String(API_PORT) },
    },
    {
      // The production build, as Caddy serves it.
      command: `npm run build && npx vite preview --port ${WEB_PORT} --strictPort --host 127.0.0.1`,
      url: `http://127.0.0.1:${WEB_PORT}`,
      timeout: 120_000,
      reuseExistingServer: false,
      env: { PANEL_API: `http://127.0.0.1:${API_PORT}` },
    },
  ],
});
