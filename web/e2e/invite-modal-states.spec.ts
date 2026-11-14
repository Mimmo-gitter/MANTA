import { test, expect } from "@playwright/test";
import {
  fulfillCorsJson,
  mockAuthenticatedSession,
  mockMembersEndpoints,
} from "./helpers/mock-auth";

const EXISTING_MEMBERS = [
  { id: "m-001", email: "p.zhuk@company.com", role: "TENANT_ADMIN" },
  { id: "m-002", email: "m.bielei@company.com", role: "SOLUTION_ARCHITECT" },
];

test.describe("Invite Member Modal — 6 Figma States & Real API Contracts", () => {
  test.beforeEach(async ({ page }) => {
    await mockAuthenticatedSession(page, {
      roles: ["TENANT_ADMIN"],
      name: "Pavlo Zhuk",
      email: "p.zhuk@company.com",
      tenantName: "Alpha Workspace",
    });
    await mockMembersEndpoints(page, EXISTING_MEMBERS);
    await page.goto("/access-roles");
    await page.getByTestId("invite-member-btn").click();
    await expect(page.getByRole("dialog")).toBeVisible();
  });

  test("State 1 (Figma 513:4408): Default Single Form layout uses dynamic tenantName, role preview, and TTL", async ({
    page,
  }) => {
    const dialog = page.getByRole("dialog");

    await expect(dialog.getByText("WORKSPACE INVITATION")).toBeVisible();
    await expect(dialog.getByText("Invite Team Member")).toBeVisible();
    await expect(
      dialog.getByText(
        "Send a single-use onboarding invitation to join Alpha Workspace.",
      ),
    ).toBeVisible();

    await expect(dialog.getByText("Work email")).toBeVisible();
    await expect(dialog.getByText("Assigned role")).toBeVisible();
    const roleTrigger = page.getByTestId("invite-role-trigger");
    await expect(roleTrigger).toBeVisible();
    await expect(roleTrigger).toContainText("Project Manager / Scrum Master");

    const previewCard = page.getByTestId("invite-role-preview");
    await expect(previewCard).toBeVisible();
    await expect(
      previewCard.getByText(
        "Grants access to sprint velocity, burndown charts, blocked Jira tickets & WIP congestion limits.",
      ),
    ).toBeVisible();

    await expect(
      dialog.getByText("Single-use invitation token expires in 48 hours."),
    ).toBeVisible();
    await expect(dialog.getByText("1 recipient · 48h TTL")).toBeVisible();
    await expect(dialog.getByTestId("invite-cancel-btn")).toBeVisible();
    await expect(dialog.getByTestId("invite-submit-btn")).toHaveText(
      "Send Invitation →",
    );
  });

  test("State 2 (Figma 513:4861): Role Selector Dropdown with 5 official SRS roles", async ({
    page,
  }) => {
    const dialog = page.getByRole("dialog");
    await page.getByTestId("invite-role-trigger").click();

    await expect(dialog.getByText("SELECT ROLE")).toBeVisible();
    await expect(
      dialog.getByText("Choose the least-privilege system role defined in M.A.N.T.A."),
    ).toBeVisible();

    const listbox = dialog.getByRole("listbox");
    await expect(listbox).toBeVisible();
    await expect(listbox.getByRole("option", { name: /Tenant Administrator/i })).toBeVisible();
    await expect(listbox.getByRole("option", { name: /Delivery Director/i })).toBeVisible();
    await expect(
      listbox.getByRole("option", { name: /Project Manager \/ Scrum Master/i }),
    ).toBeVisible();
    await expect(listbox.getByRole("option", { name: /Solution Architect/i })).toBeVisible();
    await expect(listbox.getByRole("option", { name: /Developer/i })).toBeVisible();

    await expect(dialog.getByText("5 official roles")).toBeVisible();

    await listbox.getByRole("option", { name: /Developer/i }).click();
    await expect(dialog.getByText("SELECT ROLE")).not.toBeVisible();
    await expect(
      page
        .getByTestId("invite-role-preview")
        .getByText("Grants access to assigned repositories, developer telemetry"),
    ).toBeVisible();
  });

  test("State 3 (Figma 513:5335): Batch Invitation with multiple rows, row removal, and slot counter", async ({
    page,
  }) => {
    const dialog = page.getByRole("dialog");

    await page.getByTestId("invite-batch-add-btn").click();
    await expect(dialog.getByText("BATCH INVITATION · 2 QUEUED")).toBeVisible();
    await expect(dialog.getByText("Invite Multiple Team Members")).toBeVisible();
    await expect(page.getByTestId("invite-email-input-1")).toBeVisible();
    await expect(page.getByTestId("invite-batch-add-btn")).toContainText(
      "Add another member (8 slots remaining)",
    );
    await expect(dialog.getByText("2 recipients · 48h TTL")).toBeVisible();
    await expect(dialog.getByTestId("invite-submit-btn")).toHaveText(
      "Send 2 Invitations →",
    );

    await page.getByTestId("invite-batch-add-btn").click();
    await expect(dialog.getByText("BATCH INVITATION · 3 QUEUED")).toBeVisible();

    await dialog.getByLabel("Remove recipient 3").click();
    await expect(dialog.getByText("BATCH INVITATION · 2 QUEUED")).toBeVisible();
  });

  test("State 4 (Figma 513:5831): Dispatched state displays honest delivery notice with disabled Copy Link button", async ({
    page,
  }) => {
    const dialog = page.getByRole("dialog");

    await page.getByTestId("invite-email-input").fill("v.dziubin@company.com");
    await page.getByTestId("invite-submit-btn").click();

    await expect(dialog.getByText("INVITATION DISPATCHED · 48H ACTIVE")).toBeVisible();
    await expect(dialog.getByText("Invitation Sent Successfully")).toBeVisible();
    await expect(dialog.getByText("Expires in 48h")).toBeVisible();
    await expect(
      dialog.getByText("Single-use activation link sent directly to recipient inbox."),
    ).toBeVisible();
    await expect(dialog.getByText(/tkn_01J9XK/)).not.toBeAttached();

    // Per disabled-button policy, Copy Link remains rendered and disabled
    const copyLinkBtn = dialog.getByRole("button", { name: "Copy Link" });
    await expect(copyLinkBtn).toBeVisible();
    await expect(copyLinkBtn).toBeDisabled();
    await expect(copyLinkBtn).toHaveAttribute("aria-disabled", "true");

    // Clicking Invite Another works cleanly (verifying isSubmitting resets in finally)
    await dialog.getByRole("button", { name: "Invite Another" }).click();
    await expect(page.getByTestId("invite-email-input")).toHaveValue("");
    await expect(page.getByTestId("invite-submit-btn")).toBeDisabled();
  });

  test("State 5 (Figma 513:6734): Validation Blocked checks real fetched workspace members and batch duplicates", async ({
    page,
  }) => {
    const dialog = page.getByRole("dialog");

    // Verify any valid email domain (including gmail.com) is NOT hardcoded-blocked
    await page.getByTestId("invite-email-input").fill("contractor@gmail.com");
    await expect(page.getByTestId("invite-submit-btn")).toBeEnabled();

    await page.getByTestId("invite-batch-add-btn").click();

    // Row 1: conflicts with real existing member returned by GET /api/v1/members
    await page.getByTestId("invite-email-input").fill("m.bielei@company.com");
    await page.getByTestId("invite-email-input").blur();

    // Row 2 & 3: batch duplicate
    await page.getByTestId("invite-email-input-1").fill("m.bielei@company.com");
    await page.getByTestId("invite-email-input-1").blur();

    await expect(dialog.getByText("VALIDATION BLOCKED · 2 ISSUES")).toBeVisible();
    await expect(dialog.getByTestId("invite-email-error")).toContainText(
      /m\.bielei@company\.com is already a member/i,
    );
    await expect(page.getByTestId("invite-submit-btn")).toBeDisabled();
  });

  test("State 6 (Figma 513:7200): Delivery Failed renders real backend HTTP error and recovers on retry", async ({
    page,
  }) => {
    const dialog = page.getByRole("dialog");

    let failDispatch = true;
    await page.route(
      (url) => url.pathname === "/api/v1/invitations",
      async (route) => {
        if (route.request().method() === "OPTIONS") {
          await fulfillCorsJson(route, 204);
          return;
        }
        if (failDispatch) {
          await fulfillCorsJson(route, 502, {
            detail: "SMTP relay gateway returned 502 Bad Gateway",
          });
          return;
        }
        await fulfillCorsJson(route, 201, { status: "sent" });
      },
    );

    await page.getByTestId("invite-email-input").fill("retry.user@company.com");
    await page.getByTestId("invite-submit-btn").click();

    await expect(dialog.getByText("DELIVERY FAILED", { exact: true })).toBeVisible();
    await expect(
      dialog.getByText("SMTP relay gateway returned 502 Bad Gateway"),
    ).toBeVisible();
    await expect(page.getByTestId("invite-email-input")).toHaveValue(
      "retry.user@company.com",
    );

    const retryBtn = dialog.getByRole("button", { name: "Retry Sending" });
    await expect(retryBtn).toBeVisible();

    failDispatch = false;
    await retryBtn.click();
    await expect(dialog.getByText("Invitation Sent Successfully")).toBeVisible();
  });
});
