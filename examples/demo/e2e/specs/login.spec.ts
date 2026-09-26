// Login (Login area of the demo's workbook). Promoted from N1 to N2 on 24.09.2026:
// two consecutive Pass results by the agent (batch 2.3 and a rerun), deterministic data,
// stable expected result (technique-browser.md section 13).
//
// Locators (technique-browser.md section 4.2): the fields have no label, only a
// placeholder (level 3) -- an observation for the accessibility module; button by role
// + name (level 1); visible texts (level 4).
import { expect, test } from "@playwright/test";

const env = (name: string): string => {
  const v = process.env[name];
  if (!v) throw new Error(`variable ${name} missing (demo's .env or CI secret)`);
  return v;
};

test.describe("Login @smoke", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
  });

  test("TC-LOGIN-001 · Login with valid credentials", async ({ page }) => {
    await page.getByPlaceholder("Username").fill(env("DEMO_SAUCE_STANDARD_USER"));
    await page.getByPlaceholder("Password").fill(env("DEMO_SAUCE_PASSWORD"));
    await page.getByRole("button", { name: "Login" }).click();
    await expect(page).toHaveURL(/\/inventory\.html$/);
    await expect(page.getByText("Products", { exact: true })).toBeVisible();
  });

  test("TC-LOGIN-002 · Login of a locked out user", async ({ page }) => {
    await page.getByPlaceholder("Username").fill(env("DEMO_SAUCE_LOCKED_OUT_USER"));
    await page.getByPlaceholder("Password").fill(env("DEMO_SAUCE_PASSWORD"));
    await page.getByRole("button", { name: "Login" }).click();
    await expect(page.getByText("Epic sadface: Sorry, this user has been locked out.")).toBeVisible();
    await expect(page).toHaveURL(/\/$/);
  });

  test("TC-LOGIN-003 · Login without username", async ({ page }) => {
    await page.getByPlaceholder("Password").fill(env("DEMO_SAUCE_PASSWORD"));
    await page.getByRole("button", { name: "Login" }).click();
    await expect(page.getByText("Epic sadface: Username is required")).toBeVisible();
  });
});
