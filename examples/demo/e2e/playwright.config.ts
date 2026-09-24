// Regressao N2 do demo (tecnica-browser.md seccao 13; plano 2.5).
// Credenciais: variaveis de ambiente; localmente lidas do .env do workspace
// (examples/demo/.env, ignorado pelo git). Nunca escritas nas specs.
// Browser: o Chrome instalado localmente; no CI (CI=true), o Chromium do Playwright.
import { defineConfig } from "@playwright/test";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

const envFile = resolve(__dirname, "..", ".env");
if (existsSync(envFile)) {
  for (const linha of readFileSync(envFile, "utf8").split(/\r?\n/)) {
    const m = linha.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$/);
    if (m && !process.env[m[1]]) process.env[m[1]] = m[2].replace(/^(['"])(.*)\1$/, "$2");
  }
}

export default defineConfig({
  testDir: "./specs",
  timeout: 30_000,
  retries: process.env.CI ? 1 : 0, // tecnica-browser 9: uma nova tentativa so para falhas de ambiente
  reporter: [["list"], ["junit", { outputFile: "test-results/junit.xml" }]],
  use: {
    baseURL: "https://www.saucedemo.com",
    channel: process.env.CI ? undefined : "chrome",
    trace: "retain-on-failure",
  },
});
