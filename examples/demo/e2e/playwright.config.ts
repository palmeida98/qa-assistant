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

// Auditoria de estados a meio das specs (qa-audit check, docs/qa-audit.md seccao 22): com QA_AUDIT_CDP_PORT
// definido, o Chromium abre essa porta de depuracao (so em 127.0.0.1) e corre um worker de cada vez
// (cada worker abriria um browser na mesma porta). Sem a variavel, as specs de auditoria sao saltadas.
const cdpPort = process.env.QA_AUDIT_CDP_PORT;

export default defineConfig({
  testDir: "./specs",
  workers: cdpPort ? 1 : undefined,
  timeout: 30_000,
  retries: process.env.CI ? 1 : 0, // tecnica-browser 9: uma nova tentativa so para falhas de ambiente
  reporter: [["list"], ["junit", { outputFile: "test-results/junit.xml" }]],
  use: {
    baseURL: "https://www.saucedemo.com",
    channel: process.env.CI ? undefined : "chrome",
    trace: "retain-on-failure",
    launchOptions: cdpPort ? { args: [`--remote-debugging-port=${cdpPort}`] } : {},
  },
});
