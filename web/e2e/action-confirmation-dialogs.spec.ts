import { test, expect } from "@playwright/test";
import {
  fulfillCorsJson,
  mockAuthenticatedSession,
  mockMembersEndpoints,
} from "./helpers/mock-auth";

const INITIAL_MEMBERS = [
  { id: "m-001", email: "p.zhuk@company.com", role: "TENANT_ADMIN" },
  { id: "m-002", email: "d.hontarenko@company.com", role: "DEVELOPER" },
];

test.describe("Members Action Confirmation Dialogs & Disabled Unimplemented Controls", () => {
  test.beforeEach(async ({ page }) => {
    await mockAuthenticatedSession(page, {
      roles: ["TENANT_ADMIN"],
      name: "Pavlo Zhuk",
      email: "p.zhuk@company.com",
    });
    await mockMembersEndpoints(page, INITIAL_MEMBERS);
    await page.goto("/access-roles");
    await expect(page.getByTestId("members-page-title")).toBeVisible();
  });

  test("Confirm Member Role Change Dialog updates role via PATCH /api/v1/members/{id}/role", async ({
    page,
  }) => {
    const row = page.getByTestId("member-row-m-002");
    await row.getByRole("button", { name: /Developer/i }).click();

    const popover = page.getByTestId("inline-role-popover-m-002");
    await expect(popover).toBeVisible();

    await popover.getByRole("option", { name: /Solution Architect/i }).click();

    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText("SRS RBAC POLICY UPDATE")).toBeVisible();
    await expect(dialog.getByText("Confirm Member Role Change")).toBeVisible();
    await expect(
      dialog.getByText(/Update workspace role permissions for.*d\.hontarenko@company\.com/i),
    ).toBeVisible();
    await expect(dialog.getByText("Current: Developer")).toBeVisible();
    await expect(dialog.getByText("New: Solution Architect")).toBeVisible();

    const confirmBtn = page.getByTestId("confirm-role-btn");
    await expect(confirmBtn).toHaveText("Confirm Role Update");
    await confirmBtn.click();

    await expect(dialog).not.toBeVisible();
    await expect(
      page.getByRole("status").filter({ hasText: "Role updated to Solution Architect" }),
    ).toBeVisible();

    await expect(row).toContainText("Solution Architect");
  });

  test("Role update displays HTTP 409 backend conflict detail when rejected", async ({
    page,
  }) => {
    await page.route(
      (url) => url.pathname === "/api/v1/members/m-002/role",
      async (route) => {
        if (route.request().method() === "OPTIONS") {
          await fulfillCorsJson(route, 204);
          return;
        }
        await fulfillCorsJson(route, 409, {
          detail: "Не можна понизити останнього адміністратора",
        });
      },
    );

    const row = page.getByTestId("member-row-m-002");
    await row.getByRole("button", { name: /Developer/i }).click();
    await page
      .getByTestId("inline-role-popover-m-002")
      .getByRole("option", { name: /Project Manager/i })
      .click();

    await page.getByTestId("confirm-role-btn").click();
    await expect(page.getByRole("status")).toContainText(
      "Не можна понизити останнього адміністратора",
    );
  });

  test("Context menu preserves unimplemented member removal action as disabled button", async ({
    page,
  }) => {
    const row = page.getByTestId("member-row-m-002");
    await row.getByLabel(/More actions for/i).click();

    const removeItem = page.getByRole("menuitem", {
      name: "Remove from workspace...",
    });
    await expect(removeItem).toBeVisible();
    await expect(removeItem).toBeDisabled();
    await expect(removeItem).toHaveAttribute("aria-disabled", "true");
  });
});
