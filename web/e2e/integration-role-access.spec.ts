import { test, expect } from "@playwright/test";
import { canUser, type Role } from "../src/lib/permissions";

test.describe("Integration Role Access & UI Behavior (SCRUM-324 / SCRUM-249)", () => {
  test("Developer role: navigation is hidden and direct access redirects to / (UI HTTP 403 equivalent)", async ({
    page,
  }) => {
    await page.addInitScript(() => {
      window.localStorage.clear();
      window.sessionStorage.setItem("manta_demo", "true");
      window.sessionStorage.setItem("manta_demo_roles", JSON.stringify(["DEVELOPER"]));
    });

    await page.goto("/");

    // 1. Integrations link and administration section must be hidden from DOM
    await expect(page.getByTestId("nav-link-integrations")).not.toBeVisible();
    await expect(page.getByText("ADMINISTRATION", { exact: true })).not.toBeVisible();

    // 2. Direct browser navigation to /integrations is intercepted by PortalAuthGuard and redirected to /
    await page.goto("/integrations");
    await expect(page).toHaveURL("/");
  });

  const nonAdminRoles: Role[] = [
    "DEVELOPER",
    "SCRUM_MASTER",
    "PROJECT_MANAGER",
    "SOLUTION_ARCHITECT",
    "DELIVERY_DIRECTOR",
    "TOP_MANAGEMENT",
  ];

  for (const role of nonAdminRoles) {
    test(`Role restriction: ${role} cannot access integrations navigation or /integrations route`, async ({
      page,
    }) => {
      // Permission matrix assertion
      expect(canUser([role], "MANAGE_WEBHOOK_INTEGRATIONS")).toBe(false);

      await page.addInitScript((assignedRole) => {
        window.localStorage.clear();
        window.sessionStorage.setItem("manta_demo", "true");
        window.sessionStorage.setItem("manta_demo_roles", JSON.stringify([assignedRole]));
      }, role);

      await page.goto("/");
      await expect(page.getByTestId("nav-link-integrations")).not.toBeVisible();

      await page.goto("/integrations");
      await expect(page).toHaveURL("/");
    });
  }

  test("Tenant Administrator: sees integrations in navigation and can access /integrations page", async ({
    page,
  }) => {
    // Permission matrix assertion
    expect(canUser(["TENANT_ADMIN"], "MANAGE_WEBHOOK_INTEGRATIONS")).toBe(true);

    await page.addInitScript(() => {
      window.localStorage.clear();
      window.sessionStorage.setItem("manta_demo", "true");
      window.sessionStorage.setItem("manta_demo_roles", JSON.stringify(["TENANT_ADMIN"]));
    });

    await page.goto("/");

    // 1. Navigation controls are visible
    await expect(page.getByText("ADMINISTRATION", { exact: true })).toBeVisible();
    await expect(page.getByTestId("nav-link-integrations")).toBeVisible();

    // 2. Navigation to /integrations succeeds
    await page.goto("/integrations");
    await expect(page).toHaveURL("/integrations");
    await expect(page.getByRole("heading", { name: "Integrations" })).toBeVisible();
  });

  test("Multi-role composition: DEVELOPER + TENANT_ADMIN grants integrations access", async ({
    page,
  }) => {
    expect(
      canUser(["DEVELOPER", "TENANT_ADMIN"], "MANAGE_WEBHOOK_INTEGRATIONS")
    ).toBe(true);

    await page.addInitScript(() => {
      window.localStorage.clear();
      window.sessionStorage.setItem("manta_demo", "true");
      window.sessionStorage.setItem(
        "manta_demo_roles",
        JSON.stringify(["DEVELOPER", "TENANT_ADMIN"])
      );
    });

    await page.goto("/integrations");
    await expect(page).toHaveURL("/integrations");
  });
});
