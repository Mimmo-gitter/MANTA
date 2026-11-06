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

    // Close confirmation dialog via Done button
    await page.getByTestId("invite-done-btn").click();
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

    // Resend action on expired member (Mykhailo Shevtsov - opens ReissueInviteDialog, Figma 513:7655)
    const resendBtn = page.getByTestId("resend-btn-mem-007");
    await expect(resendBtn).toBeVisible();
    await resendBtn.click();
    const reissueConfirmBtn = page.getByTestId("reissue-confirm-btn");
    await expect(reissueConfirmBtn).toBeVisible();
    await reissueConfirmBtn.click();
    await expect(page.getByRole("status").filter({ hasText: "Invitation resent to m.shevtsov@acme.io" })).toBeVisible();
  });

  test("Strict pagination: exactly 7 members per page and navigating to page 2", async ({ page }) => {
    await page.goto("/access-roles");

    // Initially 7 members, page 1 of 1
    await expect(page.getByText("Showing 1–7 of 7 workspace members")).toBeVisible();
    await expect(page.getByRole("button", { name: "Prev" })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Next" })).toBeDisabled();

    // Invite an 8th member
    await page.getByTestId("invite-member-btn").click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await page.getByTestId("invite-email-input").fill("extra.user@acme.io");
    await page.getByTestId("invite-submit-btn").click();
    await page.getByTestId("invite-done-btn").click();

    // Dialog closes
    await expect(dialog).not.toBeVisible();

    // After adding 8th member, footer shows 1-7 of 8
    await expect(page.getByText("Showing 1–7 of 8 workspace members")).toBeVisible();
    const nextBtn = page.getByRole("button", { name: "Next" });
    await expect(nextBtn).toBeEnabled();

    // The newly invited member is at the top of page 1 table
    await expect(page.locator("[data-testid^='member-row-']").filter({ hasText: "extra.user@acme.io" })).toBeVisible();

    // Navigate to page 2
    await nextBtn.click();
    await expect(page.getByText("Showing 8–8 of 8 workspace members")).toBeVisible();
    await expect(page.getByRole("button", { name: "Prev" })).toBeEnabled();
    await expect(page.getByRole("button", { name: "Next" })).toBeDisabled();
  });

  test("Row context menu & Inline Role Selector: matches Figma 513:3083, 513:3059, 513:3530, 513:3957, 513:3979 and enforces single open popover", async ({
    page,
  }) => {
    await page.goto("/access-roles");

    // 1. Inline SRS Role Selector (Figma 513:3083) on Maksym Bielei
    const maksymRow = page.getByTestId("member-row-mem-002");
    await maksymRow.getByRole("button", { name: /Solution Architect/i }).click();
    const rolePopover = page.getByTestId("inline-role-popover-mem-002");
    await expect(rolePopover).toBeVisible();
    await expect(rolePopover).toContainText("ASSIGN — MAKSYM BIELEI");
    await expect(rolePopover).toContainText("Approve PII-sanitized ADRs, SCIP call-graphs & tech debt");

    // 2. Opening Row 2 Context Menu (Figma 513:3530 - Active member) closes Inline Role Selector
    const row2MenuBtn = maksymRow.getByLabel("More actions for Maksym Bielei");
    await row2MenuBtn.click();
    await expect(rolePopover).not.toBeVisible();

    await expect(page.getByText("MAKSYM BIELEI · ACTIVE")).toBeVisible();
    await expect(page.getByRole("menuitem", { name: "Change assigned role..." })).toBeVisible();
    await expect(page.getByRole("menuitem", { name: "Copy work email" })).toBeVisible();
    await expect(page.getByRole("menuitem", { name: "View role permissions" })).toBeVisible();
    await expect(page.getByRole("menuitem", { name: "Remove from workspace..." })).toBeVisible();

    // Clicking "Change assigned role..." closes context menu and opens Inline Role Selector (513:3083)
    await page.getByRole("menuitem", { name: "Change assigned role..." }).click();
    await expect(page.getByRole("menu")).toHaveCount(0);
    await expect(rolePopover).toBeVisible();

    // 3. Row 5 (Bohdan Shcherbak - Invite Sent menu, Figma 513:3957) closes Inline Role Selector
    const row5MenuBtn = page.getByTestId("member-row-mem-005").getByLabel("More actions for Bohdan Shcherbak");
    await row5MenuBtn.click();
    await expect(rolePopover).not.toBeVisible();
    await expect(page.getByRole("menu")).toHaveCount(1);
    await expect(page.getByText("INVITE SENT · 41H LEFT (BOHDAN)")).toBeVisible();
    await expect(page.getByRole("menuitem", { name: "Copy one-time invite link" })).toBeVisible();
    await expect(page.getByRole("menuitem", { name: "Resend invitation email" })).toBeVisible();
    await expect(page.getByRole("menuitem", { name: "Change invited role..." })).toBeVisible();
    await expect(page.getByRole("menuitem", { name: "Revoke invitation..." })).toBeVisible();

    // Clicking "Resend invitation email" opens the Reissue Workspace Invitation modal (Figma 513:7655)
    await page.getByRole("menuitem", { name: "Resend invitation email" }).click();
    const reissueModal = page.getByRole("dialog");
    await expect(reissueModal).toBeVisible();
    await expect(reissueModal.getByText("Re-issue Workspace Invitation")).toBeVisible();
    await expect(reissueModal.getByText("INVITATION ACTIVE · 41H LEFT")).toBeVisible();

    // Confirm reissue
    await reissueModal.getByRole("button", { name: /Generate & Resend Invite/i }).click();
    await expect(reissueModal).not.toBeVisible();

    // Verifies top-right Toast (Figma 513:6316)
    const toast = page.getByTestId("notification-toast");
    await expect(toast).toBeVisible();
    await expect(toast).toContainText("Invitation resent to b.shcherbak@acme.io");
    await expect(toast).toContainText("Expires in 48h");
    await expect(toast.getByTestId("toast-copy-link-btn")).toBeVisible();

    // 4. Row 7 (Mykhailo Shevtsov - Expired Invite menu, Figma 513:3979)
    const row7MenuBtn = page.getByTestId("member-row-mem-007").getByLabel("More actions for Mykhailo Shevtsov");
    await row7MenuBtn.click();
    await expect(page.getByRole("menu")).toHaveCount(1);
    await expect(page.getByText("EXPIRED · 2D AGO (MYKHAILO)")).toBeVisible();
    await expect(page.getByRole("menuitem", { name: "Re-issue invite (new 48h)..." })).toBeVisible();
    await expect(page.getByRole("menuitem", { name: "Change invited role..." })).toBeVisible();
    await expect(page.getByRole("menuitem", { name: "Copy work email" })).toBeVisible();
    await expect(page.getByRole("menuitem", { name: "Delete expired invite..." })).toBeVisible();

    // Click outside to close
    await page.getByTestId("members-page-title").click();
    await expect(page.getByRole("menu")).toHaveCount(0);
  });
});
