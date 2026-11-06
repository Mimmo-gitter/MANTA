import { test, expect } from "@playwright/test";

test.describe("Table Action Confirmation Dialogs — 4 Figma Screens (513:7655, 513:8103, 513:8545, 513:8986)", () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      window.localStorage.clear();
      window.sessionStorage.setItem("manta_demo", "true");
      window.sessionStorage.setItem("manta_demo_roles", JSON.stringify(["TENANT_ADMIN"]));
    });
    await page.goto("/access-roles");
    await expect(page.getByTestId("members-page-title")).toBeVisible();
  });

  test("1. Re-issue Workspace Invitation Dialog (Figma 513:7655)", async ({ page }) => {
    // Click 'Resend' on expired member Mykhailo Shevtsov (mem-007)
    const resendBtn = page.getByTestId("resend-btn-mem-007");
    await expect(resendBtn).toBeVisible();
    await resendBtn.click();

    // Verify Dialog rendered
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();

    // Badge & Titles
    await expect(dialog.getByText("INVITATION EXPIRED · 2D AGO")).toBeVisible();
    await expect(dialog.getByText("Re-issue Workspace Invitation")).toBeVisible();
    await expect(
      dialog.getByText("Generate a fresh 48-hour token and resend onboarding instructions.")
    ).toBeVisible();

    // Recipient card
    await expect(dialog.getByText("MS")).toBeVisible();
    await expect(dialog.getByText("Mykhailo Shevtsov")).toBeVisible();
    await expect(dialog.getByText("m.shevtsov@acme.io")).toBeVisible();
    await expect(dialog.getByText("Expired 2d ago")).toBeVisible();

    // Assigned role field
    await expect(dialog.getByText("Assigned role (verify before resending)")).toBeVisible();
    await expect(dialog.getByText("Developer")).toBeVisible();

    // Security callout
    await expect(
      dialog.getByText("Previous expired token (tkn_01J98A...) is permanently invalidated.")
    ).toBeVisible();

    // Footer
    await expect(dialog.getByText("New TTL: 48 hours")).toBeVisible();
    await expect(dialog.getByRole("button", { name: "Cancel" })).toBeVisible();

    const confirmBtn = page.getByTestId("reissue-confirm-btn");
    await expect(confirmBtn).toHaveText("Generate & Resend Invite →");
    await confirmBtn.click();

    // Closes dialog and shows toast
    await expect(dialog).not.toBeVisible();
    await expect(
      page.getByRole("status").filter({ hasText: "Invitation resent to m.shevtsov@acme.io" })
    ).toBeVisible();
  });

  test("2. Confirm Member Role Change Dialog (Figma 513:8103)", async ({ page }) => {
    // Open Inline Role Selector for Dmytro Hontarenko (mem-004)
    const row = page.getByTestId("member-row-mem-004");
    await row.getByRole("button", { name: /Developer/i }).click();

    const popover = page.getByTestId("inline-role-popover-mem-004");
    await expect(popover).toBeVisible();

    // Select Solution Architect
    await popover.getByRole("option", { name: /Solution Architect/i }).click();

    // Confirm dialog opens
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();

    // Badge & Titles
    await expect(dialog.getByText("SRS RBAC POLICY UPDATE")).toBeVisible();
    await expect(dialog.getByText("Confirm Member Role Change")).toBeVisible();
    await expect(
      dialog.getByText("Update workspace role permissions for Dmytro Hontarenko (d.hontarenko@acme.io).")
    ).toBeVisible();

    // Comparison box
    await expect(dialog.getByText("Current: Developer")).toBeVisible();
    await expect(dialog.getByText("New: Solution Architect")).toBeVisible();

    // Permissions delta list
    await expect(dialog.getByText("+ Grants: Review & approve PII-sanitized ADRs")).toBeVisible();
    await expect(dialog.getByText("= Retains: Personal code quality metrics")).toBeVisible();
    await expect(dialog.getByText("✕ Restricted: Tenant billing, OAuth integrations")).toBeVisible();

    // Security callout
    await expect(
      dialog.getByText("Active JWT claims session policies refresh immediately upon confirmation")
    ).toBeVisible();

    // Confirm update
    const confirmBtn = page.getByTestId("confirm-role-btn");
    await expect(confirmBtn).toHaveText("Confirm Role Update");
    await confirmBtn.click();

    await expect(dialog).not.toBeVisible();
    await expect(
      page.getByRole("status").filter({ hasText: "Role updated to Solution Architect" })
    ).toBeVisible();
  });

  test("3. Revoke Pending Invitation Dialog (Figma 513:8545)", async ({ page }) => {
    // Open context menu for Matvii Yeromenko (mem-006, Invite Sent)
    const row = page.getByTestId("member-row-mem-006");
    const menuTrigger = row.getByLabel("More actions for Matvii Yeromenko");
    await menuTrigger.click();

    // Click 'Revoke invitation...'
    const revokeItem = page.getByRole("menuitem", { name: "Revoke invitation..." });
    await expect(revokeItem).toBeVisible();
    await revokeItem.click();

    // Confirm dialog opens
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();

    // Badge & Titles
    await expect(dialog.getByText("DESTRUCTIVE ACTION · TOKEN BURN")).toBeVisible();
    await expect(dialog.getByText("Revoke Pending Invitation?")).toBeVisible();
    await expect(
      dialog.getByText("Immediately invalidate the active 48-hour onboarding link sent to Matvii Yeromenko.")
    ).toBeVisible();

    // Recipient card
    await expect(dialog.getByText("MY")).toBeVisible();
    await expect(dialog.getByText("Matvii Yeromenko", { exact: true })).toBeVisible();
    await expect(dialog.getByText("m.yeromenko@acme.io · Role: Developer")).toBeVisible();
    await expect(dialog.getByText("18h left")).toBeVisible();

    // Consequences
    await expect(
      dialog.getByText("• Visiting the emailed invitation URL will return HTTP 410 (Invitation Revoked).")
    ).toBeVisible();
    await expect(
      dialog.getByText("• No workspace account is created. You can invite m.yeromenko@acme.io again later.")
    ).toBeVisible();

    // Security callout & Footer
    await expect(
      dialog.getByText("Token hash is purged immediately from the tenant OIDC allowlist.")
    ).toBeVisible();
    await expect(dialog.getByText("Immediate revocation")).toBeVisible();
    await expect(dialog.getByRole("button", { name: "Keep Invite" })).toBeVisible();

    // Confirm revocation
    const confirmBtn = page.getByTestId("revoke-confirm-btn");
    await expect(confirmBtn).toHaveText("Revoke Invitation");
    await confirmBtn.click();

    await expect(dialog).not.toBeVisible();
    await expect(
      page.getByRole("status").filter({ hasText: "Invitation revoked for m.yeromenko@acme.io" })
    ).toBeVisible();
  });

  test("4. Remove Member from Workspace Dialog (Figma 513:8986)", async ({ page }) => {
    // Open context menu for Dmytro Hontarenko (mem-004, Active)
    const row = page.getByTestId("member-row-mem-004");
    const menuTrigger = row.getByLabel("More actions for Dmytro Hontarenko");
    await menuTrigger.click();

    // Click 'Remove from workspace...'
    const removeItem = page.getByRole("menuitem", { name: "Remove from workspace..." });
    await expect(removeItem).toBeVisible();
    await removeItem.click();

    // Confirm dialog opens
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();

    // Badge & Titles
    await expect(dialog.getByText("REVOKE TENANT ACCESS · US-12")).toBeVisible();
    await expect(dialog.getByText("Remove Member from Workspace?")).toBeVisible();
    await expect(
      dialog.getByText("Immediately terminate Acme Consulting access for Dmytro Hontarenko.")
    ).toBeVisible();

    // Recipient card
    await expect(dialog.getByText("DH")).toBeVisible();
    await expect(dialog.getByText("Dmytro Hontarenko", { exact: true })).toBeVisible();
    await expect(dialog.getByText("d.hontarenko@acme.io · Active Member")).toBeVisible();
    await expect(dialog.getByText("Developer")).toBeVisible();

    // Consequences with Privacy link
    await expect(
      dialog.getByText("• All active OAuth2 / JWT browser sessions are revoked immediately.")
    ).toBeVisible();
    await expect(
      dialog.getByText("• Sanitized ADR records & sprint velocity history remain preserved for tenant analytics.")
    ).toBeVisible();
    await expect(dialog.getByText("Open Administration → Privacy & Erasure →")).toBeVisible();

    // Footer
    await expect(dialog.getByText("Audit: MEMBER_REMOVED")).toBeVisible();
    await expect(dialog.getByRole("button", { name: "Cancel" })).toBeVisible();

    // Confirm removal
    const confirmBtn = page.getByTestId("remove-confirm-btn");
    await expect(confirmBtn).toHaveText("Remove Member");
    await confirmBtn.click();

    await expect(dialog).not.toBeVisible();
    await expect(
      page.getByRole("status").filter({ hasText: "Member Dmytro Hontarenko removed from workspace" })
    ).toBeVisible();
  });
});
