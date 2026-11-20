import { test, expect } from "@playwright/test";
import { type Role, canUser } from "../src/lib/permissions";
import {
  fulfillCorsJson,
  mockAuthenticatedSession,
  mockMembersEndpoints,
} from "./helpers/mock-auth";

const EIGHT_MEMBERS = [
  // Deliberately place current user second in API response to verify matching by email + sorting first
  { id: "m-002", email: "m.bielei@company.com", role: "SOLUTION_ARCHITECT" },
  { id: "m-001", email: "p.zhuk@company.com", role: "TENANT_ADMIN" },
  { id: "m-003", email: "v.karban@company.com", role: "PROJECT_MANAGER" },
  { id: "m-004", email: "d.hontarenko@company.com", role: "DEVELOPER" },
  { id: "m-005", email: "b.shcherbak@company.com", role: "DELIVERY_DIRECTOR" },
  { id: "m-006", email: "m.yeromenko@company.com", role: "DEVELOPER" },
  { id: "m-007", email: "m.shevtsov@company.com", role: "SCRUM_MASTER" },
  { id: "m-008", email: "o.kovalenko@company.com", role: "TOP_MANAGEMENT" },
];

test.describe("Members Page & Invitation Flow (SCRUM-248 / US-12)", () => {
  test("AC 1: Administrator sees backend members directory, current-user match by email, KPI summary, and sends invitations", async ({
    page,
  }) => {
    await mockAuthenticatedSession(page, {
      roles: ["TENANT_ADMIN"],
      name: "Pavlo Zhuk",
      email: "p.zhuk@company.com",
      tenantName: "Alpha Workspace",
    });
    const state = await mockMembersEndpoints(page, EIGHT_MEMBERS.slice(0, 4));

    await page.goto("/access-roles");

    await expect(page.getByTestId("members-page-title")).toHaveText(
      "Members & Access",
    );
    await expect(page.getByTestId("members-page-breadcrumb")).toContainText(
      "Administration",
    );
    await expect(page.getByTestId("members-page-breadcrumb")).toContainText(
      "Access & Roles",
    );

    const kpiStrip = page.getByTestId("members-kpi-strip");
    await expect(kpiStrip).toBeVisible();
    await expect(kpiStrip).toContainText("4 members");
    await expect(kpiStrip).toContainText("4 active");
    await expect(kpiStrip).toContainText("0 invite sent");

    // Current user matched by email (even though not index 0 in API payload) and sorted first
    const currentRow = page.getByTestId("member-row-m-001");
    await expect(currentRow).toContainText("p.zhuk@company.com");
    await expect(currentRow.getByText("YOU", { exact: true })).toBeVisible();
    await expect(
      page.getByTestId("member-row-m-002").getByText("YOU", { exact: true }),
    ).not.toBeVisible();

    // Send real POST /api/v1/invitations
    await page.getByTestId("invite-member-btn").click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();

    await page.getByTestId("invite-email-input").fill("anna.smith@company.com");
    await page.getByTestId("invite-role-select").selectOption("SOLUTION_ARCHITECT");
    await page.getByTestId("invite-submit-btn").click();

    await expect(
      dialog.getByText("Invitation Sent Successfully"),
    ).toBeVisible();
    expect(state.invitations).toEqual([
      { email: "anna.smith@company.com", role: "SOLUTION_ARCHITECT" },
    ]);

    await page.getByTestId("invite-done-btn").click();
    await expect(dialog).not.toBeVisible();
  });

  test("AC 2: Invalid emails are rejected client-side with visual feedback and disabled submit", async ({
    page,
  }) => {
    await mockAuthenticatedSession(page, { roles: ["TENANT_ADMIN"] });
    await mockMembersEndpoints(page, EIGHT_MEMBERS.slice(0, 2));

    await page.goto("/access-roles");
    await page.getByTestId("invite-member-btn").click();
    const dialog = page.getByRole("dialog");

    const emailInput = page.getByTestId("invite-email-input");
    const submitBtn = page.getByTestId("invite-submit-btn");

    await expect(submitBtn).toBeDisabled();

    await emailInput.fill("invalid-email-address");
    await emailInput.blur();
    const errorMsg = page.getByTestId("invite-email-error");
    await expect(errorMsg).toBeVisible();
    await expect(errorMsg).toContainText("Please enter a valid email address");
    await expect(emailInput).toHaveAttribute("aria-invalid", "true");
    await expect(submitBtn).toBeDisabled();

    await emailInput.fill("user@domain");
    await emailInput.blur();
    await expect(errorMsg).toBeVisible();
    await expect(submitBtn).toBeDisabled();

    await emailInput.fill("valid.colleague@company.com");
    await expect(errorMsg).not.toBeVisible();
    await expect(emailInput).toHaveAttribute("aria-invalid", "false");
    await expect(submitBtn).toBeEnabled();

    await page.getByTestId("invite-cancel-btn").click();
    await expect(dialog).not.toBeVisible();
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
    test(`AC 3: Non-administrator (${role}) cannot open /access-roles or /members and navigation is hidden`, async ({
      page,
    }) => {
      expect(canUser([role], "MANAGE_TENANT_USERS_AND_ROLES")).toBe(false);

      await mockAuthenticatedSession(page, {
        roles: [role],
        email: `${role.toLowerCase()}@company.com`,
      });

      await page.goto("/");
      await expect(page.getByTestId("nav-link-access-roles")).not.toBeVisible();

      await page.goto("/access-roles");
      await expect(page).toHaveURL("/");

      await page.goto("/members");
      await expect(page).toHaveURL("/");
    });
  }

  test("Search, role filtering, and strict 7-per-page pagination work on backend dataset", async ({
    page,
  }) => {
    await mockAuthenticatedSession(page, { roles: ["TENANT_ADMIN"] });
    await mockMembersEndpoints(page, EIGHT_MEMBERS);

    await page.goto("/access-roles");

    await expect(
      page.getByText("Showing 1–7 of 8 workspace members"),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: "Prev" })).toBeDisabled();
    const nextBtn = page.getByRole("button", { name: "Next" });
    await expect(nextBtn).toBeEnabled();

    await nextBtn.click();
    await expect(
      page.getByText("Showing 8–8 of 8 workspace members"),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: "Prev" })).toBeEnabled();
    await expect(nextBtn).toBeDisabled();

    await page.getByRole("button", { name: "Prev" }).click();

    const searchInput = page.getByTestId("members-search-input");
    await searchInput.fill("v.karban");
    await expect(page.getByText("v.karban@company.com")).toBeVisible();
    await expect(page.getByText("m.bielei@company.com")).not.toBeVisible();

    await searchInput.fill("");
    const roleFilter = page.getByTestId("members-role-filter");
    await roleFilter.selectOption("SOLUTION_ARCHITECT");
    await expect(page.getByText("m.bielei@company.com")).toBeVisible();
    await expect(page.getByText("v.karban@company.com")).not.toBeVisible();
  });

  test("GET /api/v1/members error shows inline error alert and Retry recovers", async ({
    page,
  }) => {
    await mockAuthenticatedSession(page, { roles: ["TENANT_ADMIN"] });

    let shouldFail = true;
    await page.route(
      (url) => url.pathname === "/api/v1/members",
      async (route) => {
        if (route.request().method() === "OPTIONS") {
          await fulfillCorsJson(route, 204);
          return;
        }
        if (shouldFail) {
          await fulfillCorsJson(route, 500, { detail: "Database timeout" });
          return;
        }
        await fulfillCorsJson(route, 200, EIGHT_MEMBERS.slice(0, 2));
      },
    );

    await page.goto("/access-roles");
    const errAlert = page.getByTestId("members-load-error");
    await expect(errAlert).toBeVisible();
    await expect(errAlert).toContainText("Database timeout");

    shouldFail = false;
    await page.getByTestId("members-retry-btn").click();
    await expect(page.getByTestId("members-kpi-strip")).toContainText("2 members");
  });
});
