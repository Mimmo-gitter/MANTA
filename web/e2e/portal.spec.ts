import { test, expect } from "@playwright/test";

test.describe("Portal Shell & Navigation (US-3)", () => {
  test("renders portal header and platform title", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveTitle(/MANTA/);
    await expect(page.getByText("AI-Augmented Engineering Intelligence Platform")).toBeVisible();
    await expect(page.getByText("Lead Time for Changes")).toBeVisible();
  });
});
