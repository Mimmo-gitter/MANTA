import { test, expect } from "@playwright/test";
import { mockAuthenticatedSession } from "./helpers/mock-auth";

test.describe("Role-based Administration Access (SCRUM-269)", () => {
  test("non-admin developer has administration entries hidden and cannot access restricted routes", async ({
    page,
  }) => {
    await mockAuthenticatedSession(page, {
      roles: ["DEVELOPER"],
      name: "Dev User",
      email: "dev@company.com",
    });

    // 1. Administration section and links should be hidden
    await page.goto("/");

    await expect(page.getByTestId("user-role-badge")).toHaveText("Developer");

    await expect(
      page.getByText("ADMINISTRATION", { exact: true }),
    ).not.toBeVisible();

    await expect(page.getByTestId("nav-link-workspace-settings")).not.toBeVisible();
    await expect(page.getByTestId("nav-link-integrations")).not.toBeVisible();
    await expect(page.getByTestId("nav-link-access-roles")).not.toBeVisible();
    await expect(page.getByTestId("nav-link-privacy-erasure")).not.toBeVisible();
    await expect(page.getByTestId("nav-link-adr-governance")).not.toBeVisible();

    // 2. Direct navigation to restricted routes should redirect to /
    const restrictedRoutes = [
      "/integrations",
      "/workspace-settings",
      "/access-roles",
      "/privacy-erasure",
      "/adr-governance",
    ];

    for (const route of restrictedRoutes) {
      await page.goto(route);
      await expect(page).toHaveURL("/");
    }
  });

  test("unauthenticated visitor accessing portal root is redirected to /login", async ({
    page,
  }) => {
    await page.addInitScript(() => {
      window.localStorage.clear();
      window.sessionStorage.clear();
    });

    await page.goto("/");
    await expect(page).toHaveURL(/\/login/);
  });
});
