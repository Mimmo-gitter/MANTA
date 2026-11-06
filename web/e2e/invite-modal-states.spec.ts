import { test, expect } from "@playwright/test";

test.describe("Invite Member Modal — 6 Figma States (513:4408, 513:4861, 513:5335, 513:5831, 513:6734, 513:7200)", () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      window.localStorage.clear();
      window.sessionStorage.setItem("manta_demo", "true");
      window.sessionStorage.setItem("manta_demo_roles", JSON.stringify(["TENANT_ADMIN"]));
    });
    await page.goto("/access-roles");
    await page.getByTestId("invite-member-btn").click();
    await expect(page.getByRole("dialog")).toBeVisible();
  });

  test("State 1 (Figma 513:4408): Default Single Form layout, badge, role preview, and TTL", async ({ page }) => {
    const dialog = page.getByRole("dialog");

    // 1. Badge & Title
    await expect(dialog.getByText("WORKSPACE INVITATION")).toBeVisible();
    await expect(dialog.getByText("Invite Team Member")).toBeVisible();
    await expect(dialog.getByText("Send a single-use onboarding invitation to join Acme Consulting.")).toBeVisible();

    // 2. Field inputs & SRS preview
    await expect(dialog.getByText("Work email")).toBeVisible();
    await expect(dialog.getByText("Assigned role")).toBeVisible();
    const roleTrigger = page.getByTestId("invite-role-trigger");
    await expect(roleTrigger).toBeVisible();
    await expect(roleTrigger).toContainText("Project Manager / Scrum Master");

    const previewCard = page.getByTestId("invite-role-preview");
    await expect(previewCard).toBeVisible();
    await expect(
      previewCard.getByText("Grants access to sprint velocity, burndown charts, blocked Jira tickets & WIP congestion limits.")
    ).toBeVisible();

    // 3. Security callout & Footer TTL
    await expect(dialog.getByText("Single-use invitation token expires in 48 hours.")).toBeVisible();
    await expect(dialog.getByText("1 recipient · 48h TTL")).toBeVisible();
    await expect(dialog.getByTestId("invite-cancel-btn")).toBeVisible();
    await expect(dialog.getByTestId("invite-submit-btn")).toHaveText("Send Invitation →");
  });

  test("State 2 (Figma 513:4861): Role Selector Dropdown with 5 official SRS roles", async ({ page }) => {
    const dialog = page.getByRole("dialog");

    // Click the assigned role field to open the 5-role SRS dropdown
    await page.getByTestId("invite-role-trigger").click();

    // Badge updates to SELECT ROLE
    await expect(dialog.getByText("SELECT ROLE")).toBeVisible();
    await expect(dialog.getByText("Choose the least-privilege system role defined in M.A.N.T.A.")).toBeVisible();

    // 5 Official Roles with descriptions in listbox
    const listbox = dialog.getByRole("listbox");
    await expect(listbox).toBeVisible();
    await expect(listbox.getByRole("option", { name: /Tenant Administrator/i })).toBeVisible();
    await expect(listbox.getByRole("option", { name: /Delivery Director/i })).toBeVisible();
    await expect(listbox.getByRole("option", { name: /Project Manager \/ Scrum Master/i })).toBeVisible();
    await expect(listbox.getByRole("option", { name: /Solution Architect/i })).toBeVisible();
    await expect(listbox.getByRole("option", { name: /Developer/i })).toBeVisible();

    // Footer shows '5 official roles'
    await expect(dialog.getByText("5 official roles")).toBeVisible();

    // Select Developer role
    await listbox.getByRole("option", { name: /Developer/i }).click();
    await expect(dialog.getByText("SELECT ROLE")).not.toBeVisible();
    await expect(
      page
        .getByTestId("invite-role-preview")
        .getByText("Grants access to assigned repositories, developer telemetry")
    ).toBeVisible();
  });

  test("State 3 (Figma 513:5335): Batch Invitation with multiple rows, row removal, and slot counter", async ({
    page,
  }) => {
    const dialog = page.getByRole("dialog");

    // Click '+ Add another member to batch'
    await page.getByTestId("invite-batch-add-btn").click();

    // Badge and title change to Batch Mode
    await expect(dialog.getByText("BATCH INVITATION · 2 QUEUED")).toBeVisible();
    await expect(dialog.getByText("Invite Multiple Team Members")).toBeVisible();
    await expect(dialog.getByText("Queue up to 10 workspace invitations with individual role assignments.")).toBeVisible();

    // Row inputs and slots remaining counter
    await expect(page.getByTestId("invite-email-input-1")).toBeVisible();
    await expect(page.getByTestId("invite-batch-add-btn")).toContainText("Add another member (8 slots remaining)");
    await expect(dialog.getByText("2 recipients · 48h TTL")).toBeVisible();
    await expect(dialog.getByTestId("invite-submit-btn")).toHaveText("Send 2 Invitations →");

    // Add 3rd row
    await page.getByTestId("invite-batch-add-btn").click();
    await expect(dialog.getByText("BATCH INVITATION · 3 QUEUED")).toBeVisible();
    await expect(dialog.getByText("3 recipients · 48h TTL")).toBeVisible();
    await expect(dialog.getByTestId("invite-submit-btn")).toHaveText("Send 3 Invitations →");

    // Remove row 3 using trash button
    await dialog.getByLabel("Remove recipient 3").click();
    await expect(dialog.getByText("BATCH INVITATION · 2 QUEUED")).toBeVisible();
  });

  test("State 4 (Figma 513:5831): Dispatched state with magic token, copy link, and expiration pill", async ({
    page,
  }) => {
    const dialog = page.getByRole("dialog");

    // Fill valid invitation
    await page.getByTestId("invite-email-input").fill("v.dziubin@acme.io");
    await page.getByTestId("invite-submit-btn").click();

    // Assert Dispatched State
    await expect(dialog.getByText("INVITATION DISPATCHED · 48H ACTIVE")).toBeVisible();
    await expect(dialog.getByText("Invitation Sent Successfully")).toBeVisible();
    await expect(dialog.getByText("Expires in 48h")).toBeVisible();
    await expect(dialog.getByText("One-time invitation URL (optional direct share via Slack)")).toBeVisible();

    // Copy link
    const copyLinkBtn = dialog.getByRole("button", { name: "Copy Link" });
    await expect(copyLinkBtn).toBeVisible();
    await copyLinkBtn.click();
    await expect(dialog.getByText("Copied!")).toBeVisible();

    // Action buttons
    await expect(dialog.getByRole("button", { name: "Invite Another" })).toBeVisible();
    await expect(page.getByTestId("invite-done-btn")).toBeVisible();
  });

  test("State 5 (Figma 513:6734): Validation Blocked on directory conflicts and SSO policy", async ({ page }) => {
    const dialog = page.getByRole("dialog");

    // Add 2nd row for batch conflicts
    await page.getByTestId("invite-batch-add-btn").click();

    // Row 1: active member conflict
    await page.getByTestId("invite-email-input").fill("m.bielei@acme.io");
    await page.getByTestId("invite-email-input").blur();

    // Row 2: public email domain conflict
    await page.getByTestId("invite-email-input-1").fill("colleague@gmail.com");
    await page.getByTestId("invite-email-input-1").blur();

    // Assert Validation Blocked banner & badge
    await expect(dialog.getByText("VALIDATION BLOCKED · 2 ISSUES")).toBeVisible();
    await expect(dialog.getByText("Cannot send invitations: 2 recipient addresses failed tenant policy checks.")).toBeVisible();
    await expect(dialog.getByText("m.bielei@acme.io is already an active member (Maksym Bielei · Solution Architect).")).toBeVisible();
    await expect(
      dialog.getByText("Public email domains (@gmail.com) are restricted by Acme Consulting SSO/OIDC policy. Use @acme.io.")
    ).toBeVisible();

    // Footer shows 2 validation errors and submit button is disabled
    await expect(dialog.getByText("2 validation errors")).toBeVisible();
    await expect(page.getByTestId("invite-submit-btn")).toBeDisabled();
  });

  test("State 6 (Figma 513:7200): Delivery Failed HTTP 503 gateway timeout with retry", async ({ page }) => {
    const dialog = page.getByRole("dialog");

    // Enter timeout trigger email
    await page.getByTestId("invite-email-input").fill("timeout@acme.io");
    await page.getByTestId("invite-submit-btn").click();

    // Assert 503 Failure Frame
    await expect(dialog.getByText("DELIVERY FAILED · HTTP 503")).toBeVisible();
    await expect(dialog.getByText("Unable to dispatch invitation email (503 Mail Gateway Timeout)")).toBeVisible();
    await expect(
      dialog.getByText("Transactional mail relay did not respond within 5,000ms. Atomic rollback completed — no orphaned invite token was committed.")
    ).toBeVisible();
    await expect(dialog.getByText("ERR_SMTP_RELAY_TIMEOUT · TRACE: req_44b91e0c")).toBeVisible();

    // Form data is preserved
    await expect(page.getByTestId("invite-email-input")).toHaveValue("timeout@acme.io");

    // Safe to retry & Retry button
    await expect(dialog.getByText("Safe to retry · 0 duplicates")).toBeVisible();
    const retryBtn = dialog.getByRole("button", { name: "Retry Sending" });
    await expect(retryBtn).toBeVisible();
  });
});
