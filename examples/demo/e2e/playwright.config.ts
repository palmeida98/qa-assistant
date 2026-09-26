// The demo's N2 regression (technique-browser.md section 13; plan 2.5).
// Credentials: environment variables; locally read from the workspace's .env
// (examples/demo/.env, ignored by git). Never written in the specs.
// Browser: the Chrome installed locally; in the CI (CI=true), Playwright's Chromium.
import { defineConfig } from "@playwright/test";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

const envFile = resolve(__dirname, "..", ".env");
if (existsSync(envFile)) {
  for (const line of readFileSync(envFile, "utf8").split(/\r?\n/)) {
    const m = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$/);
    if (m && !process.env[m[1]]) process.env[m[1]] = m[2].replace(/^(['"])(.*)\1$/, "$2");
  }
}

// Auditing states in the middle of the specs (qa-audit check, docs/qa-audit.md section 22): with QA_AUDIT_CDP_PORT
// defined, Chromium opens that debugging port (only on 127.0.0.1) and runs one worker at a time
// (each worker would open a browser on the same port). Without the variable, the auditing specs are skipped.
const cdpPort = process.env.QA_AUDIT_CDP_PORT;

export default defineConfig({
  testDir: "./specs",
  workers: cdpPort ? 1 : undefined,
  timeout: 30_000,
  retries: process.env.CI ? 1 : 0, // technique-browser 9: one retry only for environment failures
  reporter: [["list"], ["junit", { outputFile: "test-results/junit.xml" }]],
  use: {
    baseURL: "https://www.saucedemo.com",
    channel: process.env.CI ? undefined : "chrome",
    trace: "retain-on-failure",
    launchOptions: cdpPort ? { args: [`--remote-debugging-port=${cdpPort}`] } : {},
  },
});
