// Accessibility audit of a state that only exists after interacting (qa-audit check,
// docs/qa-audit.md section 22): the checkout error message without data. It is not the promotion of a case to N2
// (TC-CHK-003 does not meet the criteria of technique-browser.md section 13): it records the engine's report,
// attached to the test's result, without failing because of it (failOn: "none").
// Runs only with QA_AUDIT_CDP_PORT defined and the qa-audit engine installed; otherwise it is skipped.
import { expect, test } from "@playwright/test";
import { auditState } from "../../../../integrations/playwright/qa-audit";

const env = (name: string): string => {
  const v = process.env[name];
  if (!v) throw new Error(`variable ${name} missing (demo's .env or CI secret)`);
  return v;
};

test.describe("Audited states @a11y", () => {
  test.skip(!process.env.QA_AUDIT_CDP_PORT, "QA_AUDIT_CDP_PORT not defined (state auditing off)");

  test("Checkout without data: error state audited by qa-audit", async ({ page }, testInfo) => {
    await page.goto("/");
    await page.getByPlaceholder("Username").fill(env("DEMO_SAUCE_STANDARD_USER"));
    await page.getByPlaceholder("Password").fill(env("DEMO_SAUCE_PASSWORD"));
    await page.getByRole("button", { name: "Login" }).click();
    await page.getByRole("button", { name: "Add to cart" }).first().click();
    await page.locator(".shopping_cart_link").click();
    await page.getByRole("button", { name: "Checkout" }).click();
    await page.getByRole("button", { name: "Continue" }).click();
    await expect(page.getByText("Error: First Name is required")).toBeVisible();

    await auditState(page, testInfo, "checkout-error-no-data", {
      failOn: "none",
      redact: ["DEMO_SAUCE_STANDARD_USER", "DEMO_SAUCE_PASSWORD"],
    });
    expect(testInfo.attachments.map((a) => a.name)).toContain("qa-audit checkout-error-no-data (check.json)");
    await expect(page.getByText("Error: First Name is required")).toBeVisible(); // the state stayed as it was
  });
});
