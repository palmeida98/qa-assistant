// Audit a state in the middle of an N2 spec with the qa-audit engine (docs/qa-audit.md section 22;
// technique-browser.md section 13). Copy this file into the workspace's specs folder.
//
// Use in a spec (Chromium or Chrome):
//   test("...", async ({ page }, testInfo) => {
//     ...interactions up to the state...
//     await auditState(page, testInfo, "checkout-error", { failOn: "none" });
//   });
//
// Requirements:
// - the engine installed (pip install -e . in qa-assistant; command in QA_AUDIT_CMD, default "qa-audit");
// - QA_AUDIT_CDP_PORT defined and playwright.config.ts adding
//   `--remote-debugging-port=${QA_AUDIT_CDP_PORT}` to Chromium's arguments, with workers: 1
//   (each worker would open a browser on the same port).
// Imports only types from @playwright/test: it works outside the Node project's folder.
import type { Page, TestInfo } from "@playwright/test";
import { spawnSync } from "node:child_process";
import { existsSync, readdirSync } from "node:fs";
import { join } from "node:path";

export type Severity = "none" | "critical" | "high" | "medium" | "low" | "info";

export interface Options {
  /** Fails the test with problems from this severity up (default "none": only records). */
  failOn?: Severity;
  /** "axe" (default); "axe,keyboard,reflow" moves the focus and changes the window's size. */
  checks?: string;
  /** Names of the environment variables whose values are redacted in the report (e.g. credentials used). */
  redact?: string[];
}

const escape = (s: string): string => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

export async function auditState(page: Page, testInfo: TestInfo, name: string, options: Options = {}): Promise<void> {
  const port = process.env.QA_AUDIT_CDP_PORT;
  if (!port) {
    throw new Error("QA_AUDIT_CDP_PORT missing: define the port and add --remote-debugging-port to Chromium");
  }
  const browser = page.context().browser();
  if (!browser || browser.browserType().name() !== "chromium") {
    throw new Error("auditState only works with Chromium or Chrome (connection over CDP)");
  }
  const output = testInfo.outputPath("qa-audit", name); // one folder per state: several calls in the same test
  const args = [
    "check", "--cdp", `http://127.0.0.1:${port}`, "--page-url", `^${escape(page.url())}$`,
    "--label", name, "--checks", options.checks ?? "axe", "--output", output,
    "--fail-on", options.failOn ?? "none",
    ...(options.redact ?? []).flatMap((v) => ["--redact-env", v]),
  ];
  const r = spawnSync(process.env.QA_AUDIT_CMD ?? "qa-audit", args, { encoding: "utf-8", timeout: 120_000 });
  if (r.error) throw new Error(`qa-audit did not run (${r.error.message}); installed? see QA_AUDIT_CMD`);
  const folder = existsSync(output) ? readdirSync(output).find((d) => d.startsWith(`check_`)) : undefined;
  if (folder) {
    for (const f of ["check.md", "check.json"]) {
      await testInfo.attach(`qa-audit ${name} (${f})`, { path: join(output, folder, f) });
    }
  }
  if (r.status === 1) {
    throw new Error(`qa-audit: problems from "${options.failOn}" up in the state ${name}: ${r.stdout.trim()}`);
  }
  if (r.status !== 0) {
    throw new Error(`qa-audit check exited with ${r.status}: ${(r.stderr || r.stdout).trim()}`);
  }
}
