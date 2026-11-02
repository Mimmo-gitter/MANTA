import { test, expect } from "@playwright/test";

test.describe("Portal Shell & Navigation (US-3)", () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      window.sessionStorage.setItem("manta_demo", "true");
    });
  });

  test("renders portal header and platform title", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveTitle(/MANTA/);
    await expect(page.getByTestId("portal-sidebar")).toBeVisible();
    await expect(page.getByTestId("portal-header")).toBeVisible();
    await expect(page.getByText("M.A.N.T.A.")).toBeVisible();
    await expect(page.getByTestId("environment-badge")).toContainText("PROD US-EAST-1");
  });
});
