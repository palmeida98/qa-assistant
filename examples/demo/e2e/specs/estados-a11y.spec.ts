// Auditoria de acessibilidade de um estado que so existe depois de interagir (qa-audit check,
// docs/qa-audit.md seccao 22): a mensagem de erro do checkout sem dados. Nao e a promocao de um caso a N2
// (o CT-CHK-003 nao cumpre os criterios da tecnica-browser.md seccao 13): regista o relatorio do motor,
// anexado ao resultado do teste, sem falhar por causa dele (falharEm: "none").
// So corre com QA_AUDIT_CDP_PORT definido e o motor qa-audit instalado; senao e saltada.
import { expect, test } from "@playwright/test";
import { auditarEstado } from "../../../../integrations/playwright/qa-audit";

const env = (nome: string): string => {
  const v = process.env[nome];
  if (!v) throw new Error(`variavel ${nome} em falta (.env do demo ou segredo do CI)`);
  return v;
};

test.describe("Estados auditados @a11y", () => {
  test.skip(!process.env.QA_AUDIT_CDP_PORT, "QA_AUDIT_CDP_PORT nao definido (auditoria de estados desligada)");

  test("Checkout sem dados: estado de erro auditado pelo qa-audit", async ({ page }, testInfo) => {
    await page.goto("/");
    await page.getByPlaceholder("Username").fill(env("DEMO_SAUCE_STANDARD_USER"));
    await page.getByPlaceholder("Password").fill(env("DEMO_SAUCE_PASSWORD"));
    await page.getByRole("button", { name: "Login" }).click();
    await page.getByRole("button", { name: "Add to cart" }).first().click();
    await page.locator(".shopping_cart_link").click();
    await page.getByRole("button", { name: "Checkout" }).click();
    await page.getByRole("button", { name: "Continue" }).click();
    await expect(page.getByText("Error: First Name is required")).toBeVisible();

    await auditarEstado(page, testInfo, "checkout-erro-sem-dados", {
      falharEm: "none",
      redigir: ["DEMO_SAUCE_STANDARD_USER", "DEMO_SAUCE_PASSWORD"],
    });
    expect(testInfo.attachments.map((a) => a.name)).toContain("qa-audit checkout-erro-sem-dados (check.json)");
    await expect(page.getByText("Error: First Name is required")).toBeVisible(); // o estado ficou como estava
  });
});
