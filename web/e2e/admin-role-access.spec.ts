
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

    await expect(
      page.getByTestId("nav-link-access-roles"),
    ).not.toBeVisible();

    await expect(
      page.getByTestId("nav-link-privacy-erasure"),
    ).not.toBeVisible();

    await expect(
      page.getByTestId("nav-link-adr-governance"),
    ).not.toBeVisible();

    // 2. Direct navigation to admin routes should redirect to /
    const adminRoutes = [
      "/integrations",
      "/workspace-settings",
      "/access-roles",
      "/privacy-erasure",
      "/adr-governance",
    ];

    for (const route of adminRoutes) {
      await page.goto(route);
      await expect(page).toHaveURL("/");
    }
  });
});
