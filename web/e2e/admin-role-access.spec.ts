
import { test, expect } from "@playwright/test";

test.describe("Role-based Administration Access (SCRUM-269)", () => {
  test("non-admin user has administration entries hidden and cannot access admin routes", async ({
    page,
  }) => {
    await page.addInitScript(() => {
      window.localStorage.clear();

      window.sessionStorage.setItem("manta_demo", "true");

      // Simulate a user without admin permissions
      window.sessionStorage.setItem(
        "manta_demo_roles",
        JSON.stringify(["MEMBER"]),
      );
    });

    // 1. Administration section and links should be hidden
    await page.goto("/");

    await expect(
      page.getByText("ADMINISTRATION", { exact: true }),
    ).not.toBeVisible();

    await expect(
      page.getByTestId("nav-link-workspace-settings"),
    ).not.toBeVisible();

    await expect(
      page.getByTestId("nav-link-integrations"),
    ).not.toBeVisible();

    // 2. Direct navigation to admin routes should redirect to /
    await page.goto("/integrations");
    await expect(page).toHaveURL("/");

    await page.goto("/workspace-settings");
    await expect(page).toHaveURL("/");
  });
});
