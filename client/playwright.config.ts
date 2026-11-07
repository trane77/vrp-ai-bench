import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./e2e",
  timeout: 120000,
  workers: 1,
  use: {
    baseURL: process.env.VRP_TEST_URL ?? "http://127.0.0.1:8000",
    viewport: { width: 1440, height: 1100 },
    trace: "retain-on-failure",
    launchOptions: { args: ["--enable-unsafe-swiftshader"] },
  },
  reporter: "list",
});
