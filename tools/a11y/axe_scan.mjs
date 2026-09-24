// Varrimento axe-core via Playwright (docs/tecnica-acessibilidade.md, plano 3.2).
// Chamado por scripts/auditar_a11y.py: le a configuracao JSON do stdin e escreve
// o resultado JSON no stdout. As credenciais chegam por variaveis de ambiente
// (QA_A11Y_UTILIZADOR, QA_A11Y_PALAVRA_PASSE), nunca por argumentos.
//
// Configuracao: {browser: "chrome"|"chromium", tags: [...], timeout_ms,
//   login: {url, campo_utilizador, campo_palavra_passe, botao} | null,
//   paginas: [{nome, url, login: bool}]}

import { chromium } from "playwright";
import { AxeBuilder } from "@axe-core/playwright";

const lerStdin = async () => {
  const partes = [];
  for await (const p of process.stdin) partes.push(p);
  return JSON.parse(Buffer.concat(partes).toString("utf8"));
};

const cfg = await lerStdin();
const timeout = cfg.timeout_ms ?? 30000;
const browser = await chromium.launch(cfg.browser === "chrome" ? { channel: "chrome" } : {});
const resultado = { browser: `${cfg.browser} ${browser.version()}`, paginas: [] };

const fazerLogin = async (page) => {
  const l = cfg.login;
  await page.goto(l.url, { timeout });
  await page.fill(l.campo_utilizador, process.env.QA_A11Y_UTILIZADOR ?? "");
  await page.fill(l.campo_palavra_passe, process.env.QA_A11Y_PALAVRA_PASSE ?? "");
  await Promise.all([page.waitForLoadState("load"), page.click(l.botao)]);
};

try {
  for (const p of cfg.paginas) {
    const context = await browser.newContext();
    const page = await context.newPage();
    const r = { nome: p.nome, url: p.url };
    try {
      if (p.login) await fazerLogin(page);
      await page.goto(p.url, { timeout });
      await page.waitForLoadState("load");
      r.url_final = page.url();
      r.titulo = await page.title();
      const axe = await new AxeBuilder({ page }).withTags(cfg.tags).analyze();
      r.axe_versao = axe.testEngine?.version;
      const resumir = (v) => ({
        id: v.id, impacto: v.impact, tags: v.tags, ajuda: v.help, ajuda_url: v.helpUrl,
        elementos: v.nodes.length,
        exemplos: v.nodes.slice(0, 5).map((n) => ({
          alvo: n.target.join(" "), html: (n.html || "").slice(0, 200), resumo: n.failureSummary || "",
        })),
      });
      r.violacoes = axe.violations.map(resumir);
      r.incompletos = axe.incomplete.map(resumir);
      r.passaram = axe.passes.length;
    } catch (e) {
      r.erro = String(e.message || e).split("\n")[0];
    }
    resultado.paginas.push(r);
    await context.close();
  }
} finally {
  await browser.close();
}
process.stdout.write(JSON.stringify(resultado));
