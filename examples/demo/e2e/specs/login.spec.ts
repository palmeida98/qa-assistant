// Login (area Login do workbook do demo). Promovidos de N1 a N2 a 24.09.2026:
// dois Pass consecutivos pelo agente (lote 2.3 e reexecucao), dados deterministicos,
// esperado estavel (tecnica-browser.md seccao 13).
//
// Localizadores (tecnica-browser.md seccao 4.2): os campos nao tem label, so
// placeholder (nivel 3) -- observacao para o modulo acessibilidade; botao por role
// + nome (nivel 1); textos visiveis (nivel 4).
import { expect, test } from "@playwright/test";

const env = (nome: string): string => {
  const v = process.env[nome];
  if (!v) throw new Error(`variavel ${nome} em falta (.env do demo ou segredo do CI)`);
  return v;
};

test.describe("Login @smoke", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
  });

  test("CT-LOGIN-001 · Login com credenciais válidas", async ({ page }) => {
    await page.getByPlaceholder("Username").fill(env("DEMO_SAUCE_STANDARD_USER"));
    await page.getByPlaceholder("Password").fill(env("DEMO_SAUCE_PASSWORD"));
    await page.getByRole("button", { name: "Login" }).click();
    await expect(page).toHaveURL(/\/inventory\.html$/);
    await expect(page.getByText("Products", { exact: true })).toBeVisible();
  });

  test("CT-LOGIN-002 · Login de utilizador bloqueado", async ({ page }) => {
    await page.getByPlaceholder("Username").fill(env("DEMO_SAUCE_LOCKED_OUT_USER"));
    await page.getByPlaceholder("Password").fill(env("DEMO_SAUCE_PASSWORD"));
    await page.getByRole("button", { name: "Login" }).click();
    await expect(page.getByText("Epic sadface: Sorry, this user has been locked out.")).toBeVisible();
    await expect(page).toHaveURL(/\/$/);
  });

  test("CT-LOGIN-003 · Login sem username", async ({ page }) => {
    await page.getByPlaceholder("Password").fill(env("DEMO_SAUCE_PASSWORD"));
    await page.getByRole("button", { name: "Login" }).click();
    await expect(page.getByText("Epic sadface: Username is required")).toBeVisible();
  });
});
