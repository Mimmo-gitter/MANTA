import { test, expect } from "@playwright/test";
import { canUser, type Role } from "../src/lib/permissions";
import { mockAuthenticatedSession } from "./helpers/mock-auth";

test.describe("Integration Role Access & UI Behavior (SCRUM-324 / SCRUM-249)", () => {
  test("Developer role: navigation is hidden and direct access redirects to / (UI HTTP 403 equivalent)", async ({
    page,
  }) => {
    await mockAuthenticatedSession(page, {
      roles: ["DEVELOPER"],
      name: "Dev Member",
      email: "dev@company.com",
    });

    await page.goto("/");

    await expect(page.getByTestId("nav-link-integrations")).not.toBeVisible();
    await expect(page.getByText("ADMINISTRATION", { exact: true })).not.toBeVisible();

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
      expect(canUser([role], "MANAGE_WEBHOOK_INTEGRATIONS")).toBe(false);

      await mockAuthenticatedSession(page, {
        roles: [role],
        name: `${role} User`,
        email: `${role.toLowerCase()}@company.com`,
      });

      await page.goto("/");
      await expect(page.getByTestId("nav-link-integrations")).not.toBeVisible();

      await page.goto("/integrations");
      await expect(page).toHaveURL("/");
    });
  }

  test("Tenant Administrator: sees integrations in navigation and can access /integrations page", async ({
    page,
  }) => {
    expect(canUser(["TENANT_ADMIN"], "MANAGE_WEBHOOK_INTEGRATIONS")).toBe(true);

    await mockAuthenticatedSession(page, {
      roles: ["TENANT_ADMIN"],
      integrations: [
        {
          id: "int-1",
          provider: "github",
          external_account_id: "acme-org",
          is_active: false,
          created_at: "2026-11-10T10:00:00Z",
        },
      ],
    });

    await page.goto("/");

    await expect(page.getByText("ADMINISTRATION", { exact: true })).toBeVisible();
    await expect(page.getByTestId("nav-link-integrations")).toBeVisible();

    await page.goto("/integrations");
    await expect(page).toHaveURL("/integrations");
    await expect(page.getByRole("heading", { name: "Integrations" })).toBeVisible();

    // Unimplemented OAuth Reconnect remains visible and disabled per disabled-button policy
    const reconnectBtn = page.getByRole("button", { name: /Reconnect/i });
    await expect(reconnectBtn).toBeVisible();
    await expect(reconnectBtn).toBeDisabled();
    await expect(reconnectBtn).toHaveAttribute("aria-disabled", "true");
  });

  test("Multi-role composition: DEVELOPER + TENANT_ADMIN grants integrations access", async ({
    page,
  }) => {
    expect(
      canUser(["DEVELOPER", "TENANT_ADMIN"], "MANAGE_WEBHOOK_INTEGRATIONS"),
    ).toBe(true);

    await mockAuthenticatedSession(page, {
      roles: ["DEVELOPER", "TENANT_ADMIN"],
    });

    await page.goto("/integrations");
    await expect(page).toHaveURL("/integrations");
  });
});
