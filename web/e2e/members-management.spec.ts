import { test, expect } from "@playwright/test";
import { type Role, canUser } from "../src/lib/permissions";

test.describe("Members Page & Invitation Flow (SCRUM-248 / US-12)", () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      window.localStorage.clear();
      window.sessionStorage.setItem("manta_demo", "true");
      window.sessionStorage.setItem("manta_demo_roles", JSON.stringify(["TENANT_ADMIN"]));
    });
  });

  test("AC 1: Administrator can see members directory, KPI summary, and send invitations from the UI", async ({
    page,
  }) => {
    await page.goto("/access-roles");

    // 1. Verify Header & Breadcrumbs
    await expect(page.getByTestId("members-page-title")).toHaveText("Members & Access");
    await expect(page.getByText("Manage workspace members, role assignments, and pending invitations.")).toBeVisible();
    await expect(page.getByTestId("members-page-breadcrumb")).toContainText("Administration");
    await expect(page.getByTestId("members-page-breadcrumb")).toContainText("Access & Roles");

    // 2. Verify KPI Summary Strip
    const kpiStrip = page.getByTestId("members-kpi-strip");
    await expect(kpiStrip).toBeVisible();
    await expect(kpiStrip).toContainText("7 members");
    await expect(kpiStrip).toContainText("4 active");
    await expect(kpiStrip).toContainText("2 invite sent");
    await expect(kpiStrip).toContainText("1 expired");

    // 3. Verify Table Rows
    await expect(page.getByTestId("member-row-mem-001")).toContainText("Pavlo Zhuk");
    await expect(page.getByTestId("member-row-mem-001")).toContainText("p.zhuk@acme.io");
    await expect(page.getByText("Maksym Bielei")).toBeVisible();
    await expect(page.getByText("Dmytro Hontarenko")).toBeVisible();

    // 4. Open Invite Dialog
    const inviteBtn = page.getByTestId("invite-member-btn");
    await expect(inviteBtn).toBeVisible();
    await inviteBtn.click();

    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole("heading", { name: "Invite Member" })).toBeVisible();

    // 5. Fill Invitation Form
    const emailInput = page.getByTestId("invite-email-input");
    await emailInput.fill("anna.smith@acme.io");

    const roleSelect = page.getByTestId("invite-role-select");
    await roleSelect.selectOption("SOLUTION_ARCHITECT");

    const submitBtn = page.getByTestId("invite-submit-btn");
    await expect(submitBtn).toBeEnabled();
    await submitBtn.click();

    // 6. Assert Success Feedback & List Update
    await expect(page.getByRole("status").filter({ hasText: "Invitation sent to anna.smith@acme.io" })).toBeVisible();

    // Dialog closes automatically
    await expect(dialog).not.toBeVisible();

    // New invite appears in list with "Invite Sent"
    await expect(page.getByText("anna.smith@acme.io")).toBeVisible();
  });

  test("AC 2: Invalid emails are rejected client-side with visual feedback and disabled submit", async ({
    page,
  }) => {
    await page.goto("/access-roles");

    // Open Invite Dialog
    await page.getByTestId("invite-member-btn").click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();

    const emailInput = page.getByTestId("invite-email-input");
    const submitBtn = page.getByTestId("invite-submit-btn");

    // Initially empty email -> submit disabled
    await expect(submitBtn).toBeDisabled();

    // Enter invalid email without '@'
    await emailInput.fill("invalid-email-address");
    await emailInput.blur();

    // Check client-side validation error
    const errorMsg = page.getByTestId("invite-email-error");
    await expect(errorMsg).toBeVisible();
    await expect(errorMsg).toContainText("Please enter a valid email address");
    await expect(emailInput).toHaveAttribute("aria-invalid", "true");
    await expect(submitBtn).toBeDisabled();

    // Enter email with '@' but missing domain part
    await emailInput.fill("user@");
    await emailInput.blur();
    await expect(errorMsg).toBeVisible();
    await expect(submitBtn).toBeDisabled();

    // Enter email with domain but missing TLD
    await emailInput.fill("user@domain");
    await emailInput.blur();
    await expect(errorMsg).toBeVisible();
    await expect(submitBtn).toBeDisabled();

    // Correct the email to a valid address
    await emailInput.fill("valid.colleague@acme.io");
    await expect(errorMsg).not.toBeVisible();
    await expect(emailInput).toHaveAttribute("aria-invalid", "false");
    await expect(submitBtn).toBeEnabled();

    // Close with Cancel button
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

      await page.addInitScript((assignedRole) => {
        window.localStorage.clear();
        window.sessionStorage.setItem("manta_demo", "true");
        window.sessionStorage.setItem("manta_demo_roles", JSON.stringify([assignedRole]));
      }, role);

      await page.goto("/");

      // 1. Navigation item is hidden from sidebar
      await expect(page.getByTestId("nav-link-access-roles")).not.toBeVisible();

      // 2. Direct browser navigation to /access-roles is redirected to /
      await page.goto("/access-roles");
      await expect(page).toHaveURL("/");

      // 3. Direct browser navigation to /members alias is redirected to /
      await page.goto("/members");
      await expect(page).toHaveURL("/");
    });
  }

  test("Search and filtering: filters members by query, role, and status", async ({
    page,
  }) => {
    await page.goto("/access-roles");

    // Search by name
    const searchInput = page.getByTestId("members-search-input");
    await searchInput.fill("Vadym");
    await expect(page.getByText("Vadym Karban")).toBeVisible();
    await expect(page.getByText("Maksym Bielei")).not.toBeVisible();

    // Clear search
    await searchInput.fill("");
    await expect(page.getByText("Maksym Bielei")).toBeVisible();

    // Filter by status: EXPIRED
    const statusFilter = page.getByTestId("members-status-filter");
    await statusFilter.selectOption("EXPIRED");
    await expect(page.getByText("Mykhailo Shevtsov")).toBeVisible();
    await expect(page.getByText("Vadym Karban")).not.toBeVisible();

    // Reset status filter
    await statusFilter.selectOption("ALL");
    await expect(page.getByText("Vadym Karban")).toBeVisible();

    // Filter by role: SOLUTION_ARCHITECT
    const roleFilter = page.getByTestId("members-role-filter");
    await roleFilter.selectOption("SOLUTION_ARCHITECT");
    await expect(page.getByText("Maksym Bielei")).toBeVisible();
    await expect(page.getByText("Vadym Karban")).not.toBeVisible();
  });

  test("Copy link and resend action buttons behave correctly", async ({ page }) => {
    await page.goto("/access-roles");

    // Copy link on pending invite (Bohdan Shcherbak)
    const copyBtn = page.getByTestId("copy-link-btn-mem-005");
    await expect(copyBtn).toBeVisible();
    await copyBtn.click();
    await expect(copyBtn).toContainText("Copied");

    // Resend action on expired member (Mykhailo Shevtsov)
    const resendBtn = page.getByTestId("resend-btn-mem-007");
    await expect(resendBtn).toBeVisible();
    await resendBtn.click();
    await expect(page.getByRole("status").filter({ hasText: "Invitation resent to m.shevtsov@acme.io" })).toBeVisible();
  });
});
