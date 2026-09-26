// Auditar um estado a meio de uma spec N2 com o motor qa-audit (docs/qa-audit.md seccao 22;
// tecnica-browser.md seccao 13). Copiar este ficheiro para a pasta das specs do workspace.
//
// Uso numa spec (Chromium ou Chrome):
//   test("...", async ({ page }, testInfo) => {
//     ...interacoes ate ao estado...
//     await auditarEstado(page, testInfo, "checkout-erro", { falharEm: "none" });
//   });
//
// Requisitos:
// - o motor instalado (pip install -e . no qa-assistant; comando em QA_AUDIT_CMD, por omissao "qa-audit");
// - QA_AUDIT_CDP_PORT definido e o playwright.config.ts a acrescentar
//   `--remote-debugging-port=${QA_AUDIT_CDP_PORT}` aos argumentos do Chromium, com workers: 1
//   (cada worker abria um browser na mesma porta).
// So importa tipos do @playwright/test: funciona fora da pasta do projeto Node.
import type { Page, TestInfo } from "@playwright/test";
import { spawnSync } from "node:child_process";
import { existsSync, readdirSync } from "node:fs";
import { join } from "node:path";

export type Severidade = "none" | "critical" | "high" | "medium" | "low" | "info";

export interface Opcoes {
  /** Falha o teste com problemas a partir desta severidade (por omissao "none": so regista). */
  falharEm?: Severidade;
  /** "axe" (por omissao); "axe,keyboard,reflow" mexe no foco e no tamanho da janela. */
  verificacoes?: string;
  /** Nomes das variaveis de ambiente cujos valores sao redigidos no relatorio (ex.: credenciais usadas). */
  redigir?: string[];
}

const escapar = (s: string): string => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

export async function auditarEstado(page: Page, testInfo: TestInfo, nome: string, opcoes: Opcoes = {}): Promise<void> {
  const porta = process.env.QA_AUDIT_CDP_PORT;
  if (!porta) {
    throw new Error("QA_AUDIT_CDP_PORT em falta: definir a porta e acrescentar --remote-debugging-port ao Chromium");
  }
  const browser = page.context().browser();
  if (!browser || browser.browserType().name() !== "chromium") {
    throw new Error("auditarEstado so funciona com Chromium ou Chrome (ligacao por CDP)");
  }
  const saida = testInfo.outputPath("qa-audit", nome); // uma pasta por estado: varias chamadas no mesmo teste
  const args = [
    "check", "--cdp", `http://127.0.0.1:${porta}`, "--page-url", `^${escapar(page.url())}$`,
    "--label", nome, "--checks", opcoes.verificacoes ?? "axe", "--output", saida,
    "--fail-on", opcoes.falharEm ?? "none",
    ...(opcoes.redigir ?? []).flatMap((v) => ["--redact-env", v]),
  ];
  const r = spawnSync(process.env.QA_AUDIT_CMD ?? "qa-audit", args, { encoding: "utf-8", timeout: 120_000 });
  if (r.error) throw new Error(`qa-audit nao correu (${r.error.message}); instalado? ver QA_AUDIT_CMD`);
  const pasta = existsSync(saida) ? readdirSync(saida).find((d) => d.startsWith(`check_`)) : undefined;
  if (pasta) {
    for (const f of ["check.md", "check.json"]) {
      await testInfo.attach(`qa-audit ${nome} (${f})`, { path: join(saida, pasta, f) });
    }
  }
  if (r.status === 1) {
    throw new Error(`qa-audit: problemas a partir de "${opcoes.falharEm}" no estado ${nome}: ${r.stdout.trim()}`);
  }
  if (r.status !== 0) {
    throw new Error(`qa-audit check saiu com ${r.status}: ${(r.stderr || r.stdout).trim()}`);
  }
}
