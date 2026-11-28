import { test, expect } from "@playwright/test";
import {
  fulfillCorsJson,
  mockAuthenticatedSession,
  setupStatefulTenantSettingsApi,
} from "./helpers/mock-auth";

test.describe("Workspace Settings — AI Compliance, Live Re-Index & Retention (TASK-313-2 / SCRUM-561)", () => {
  test("1. Replaces placeholder with live settings, switches cloud_zdr -> local_only via PATCH, and polls live re-index banner through completion", async ({
    page,
  }) => {
    await mockAuthenticatedSession(page, {
      roles: ["TENANT_ADMIN"],
      tenantName: "Acme Core",
    });

    const api = await setupStatefulTenantSettingsApi(
      page,
      {
        name: "Acme Core",
        slug: "acme-core",
        allowed_domain: "acme.com",
        industry: "FinTech",
        ai_processing_mode: "cloud_zdr",
        retention_days: 90,
      },
      {
        reindexProgression: [
          {
            reindexing: true,
            reindex_status: "running",
            reindexed_count: 16,
            reindex_total_count: 32,
          },
          {
            reindexing: false,
            reindex_status: "completed",
            reindexed_count: 32,
            reindex_total_count: 32,
          },
        ],
      },
    );

    await page.goto("/workspace-settings");

    // Verify placeholder is gone and real Workspace Settings view renders
    await expect(
      page.getByRole("heading", { level: 1, name: "Workspace Settings" }),
    ).toBeVisible();
    await expect(page.getByText(/Coming soon/i)).not.toBeVisible();

    await expect(page.getByTestId("active-ai-mode-pill")).toContainText(
      "cloud_zdr (1536d)",
    );
    await expect(page.getByTestId("active-llm-model")).toHaveText("gpt-4o-mini");
    await expect(page.getByTestId("active-embedding-model")).toHaveText(
      "text-embedding-3-small",
    );
    await expect(page.getByTestId("ai-mode-option-cloud_zdr")).toBeChecked();
    await expect(page.getByTestId("retention-days-input")).toHaveValue("90");
    await expect(page.getByTestId("reindex-status-banner")).not.toBeVisible();

    const saveButton = page.getByTestId("workspace-settings-save-button");
    await expect(saveButton).toBeDisabled();

    // Select local_only and verify inline impact warning appears before save
    await page.getByTestId("ai-mode-card-local_only").click();
    await expect(page.getByTestId("ai-mode-option-local_only")).toBeChecked();
    await expect(page.getByTestId("ai-mode-switch-warning")).toBeVisible();
    await expect(saveButton).toBeEnabled();

    await saveButton.click();

    // Verify PATCH payload sent strictly changed ai_processing_mode
    await expect.poll(() => api.getPatchPayloads().length).toBe(1);
    expect(api.getPatchPayloads()[0]).toEqual({
      ai_processing_mode: "local_only",
    });

    // Verify queued re-index banner renders immediately alongside updated 384d metadata
    const banner = page.getByTestId("reindex-status-banner");
    await expect(banner).toBeVisible();
    await expect(banner).toHaveAttribute("data-reindex-status", "queued");
    await expect(page.getByTestId("active-ai-mode-pill")).toContainText(
      "local_only (384d)",
    );
    await expect(page.getByTestId("active-embedding-model")).toHaveText(
      "bge-small-en-v1.5",
    );
    await expect(page.getByTestId("workspace-settings-toast")).toContainText(
      "AI compliance mode updated",
    );

    // Begin editing retention_days while polling runs to confirm polling never clobbers unsaved edits
    await page.getByTestId("retention-preset-180").click();
    await expect(page.getByTestId("retention-days-input")).toHaveValue("180");

    // Live polling advances queued -> running (16/32) -> completed (32/32)
    await expect(banner).toHaveAttribute("data-reindex-status", "running", {
      timeout: 6000,
    });
    await expect(page.getByTestId("reindex-progress-summary")).toContainText(
      "16 / 32 approved ADRs re-embedded (50%)",
    );
    await expect(page.getByTestId("retention-days-input")).toHaveValue("180");

    await expect(banner).toHaveAttribute("data-reindex-status", "completed", {
      timeout: 6000,
    });
    await expect(page.getByTestId("reindex-progress-summary")).toContainText(
      "32 / 32 approved ADRs indexed",
    );
    await expect(page.getByTestId("retention-days-input")).toHaveValue("180");

    // Dismiss completed banner cleanly
    await page.getByTestId("reindex-dismiss-button").click();
    await expect(banner).not.toBeVisible();
  });

  test("2. Updates retention controls and organization profile via diff-only PATCH without triggering re-index, and validates 7..3650 bounds", async ({
    page,
  }) => {
    await mockAuthenticatedSession(page, {
      roles: ["TENANT_ADMIN"],
      tenantName: "Acme Core",
    });

    const api = await setupStatefulTenantSettingsApi(page, {
      name: "Acme Core",
      allowed_domain: "acme.com",
      industry: "FinTech",
      ai_processing_mode: "cloud_zdr",
      retention_days: 90,
    });

    await page.goto("/workspace-settings");

    const retentionInput = page.getByTestId("retention-days-input");
    const saveButton = page.getByTestId("workspace-settings-save-button");

    // Out-of-bounds retention triggers accessible client validation & focus routing without PATCH
    await retentionInput.fill("3");
    await saveButton.click();

    await expect(page.getByTestId("retention-days-error")).toContainText(
      "between 7 and 3650 days",
    );
    await expect(retentionInput).toHaveAttribute("aria-invalid", "true");
    await expect(retentionInput).toBeFocused();
    expect(api.getPatchPayloads()).toHaveLength(0);

    // Select 365-day compliance preset, update org name, and clear industry
    await page.getByTestId("retention-preset-365").click();
    await expect(retentionInput).toHaveValue("365");
    await expect(retentionInput).toHaveAttribute("aria-invalid", "false");
    await expect(page.getByTestId("retention-duration-preview")).toContainText(
      "365 days (~1 year)",
    );

    await page.getByTestId("workspace-name-input").fill("Acme Core Holdings");
    await page.getByTestId("workspace-industry-input").fill("");

    await saveButton.click();

    await expect.poll(() => api.getPatchPayloads().length).toBe(1);
    expect(api.getPatchPayloads()[0]).toEqual({
      name: "Acme Core Holdings",
      industry: null,
      retention_days: 365,
    });

    // No re-index banner should appear when AI mode did not change
    await expect(page.getByTestId("reindex-status-banner")).not.toBeVisible();
    await expect(page.getByTestId("workspace-settings-toast")).toContainText(
      "Workspace settings saved",
    );

    // Reload and confirm persistence from backend state
    await page.reload();
    await expect(page.getByTestId("retention-days-input")).toHaveValue("365");
    await expect(page.getByTestId("workspace-name-input")).toHaveValue(
      "Acme Core Holdings",
    );
    await expect(page.getByTestId("workspace-industry-input")).toHaveValue("");
  });

  test("3. Surfaces backend 400 fail-closed local_only policy violation and failed re-index alerts cleanly", async ({
    page,
  }) => {
    await mockAuthenticatedSession(page, {
      roles: ["TENANT_ADMIN"],
      tenantName: "Acme Core",
    });

    await setupStatefulTenantSettingsApi(
      page,
      {
        ai_processing_mode: "cloud_zdr",
        reindexing: false,
        reindex_status: "failed",
        reindex_job_id: "job-failed-99",
        reindex_error: "Local Ollama endpoint connection refused on port 11434",
      },
      { failLocalOnlyWithWanError: true },
    );

    await page.goto("/workspace-settings");

    // Failed re-index alert banner is displayed with backend error details
    const banner = page.getByTestId("reindex-status-banner");
    await expect(banner).toBeVisible();
    await expect(banner).toHaveAttribute("data-reindex-status", "failed");
    await expect(page.getByTestId("reindex-error-detail")).toContainText(
      "Local Ollama endpoint connection refused",
    );

    // Attempting local_only switch surfaces 400 fail-closed WAN error
    await page.getByTestId("ai-mode-card-local_only").click();
    await page.getByTestId("workspace-settings-save-button").click();

    await expect(page.getByTestId("workspace-settings-submit-error")).toContainText(
      "Outbound WAN endpoint 'api.openai.com' is strictly forbidden",
    );
    // Active mode stays cloud_zdr
    await expect(page.getByTestId("active-ai-mode-pill")).toContainText(
      "cloud_zdr (1536d)",
    );
  });

  test("4. Unauthorized roles are blocked from accessing or mutating workspace settings", async ({
    page,
  }) => {
    // 4A. Non-admin (DEVELOPER) cannot see sidebar link and is redirected from /workspace-settings
    await mockAuthenticatedSession(page, {
      roles: ["DEVELOPER"],
      name: "Dev User",
      email: "dev@acme.com",
    });

    const devSettingsRequests: string[] = [];
    await page.route(
      (url) => url.pathname === "/api/v1/tenants/settings",
      async (route) => {
        devSettingsRequests.push(route.request().method());
        await fulfillCorsJson(route, 403, {
          detail: "Forbidden: insufficient permissions",
        });
      },
    );

    await page.goto("/workspace-settings");
    await expect(page).toHaveURL("/");
    await expect(
      page.getByTestId("nav-link-workspace-settings"),
    ).not.toBeVisible();
    await expect(
      page.getByTestId("workspace-settings-save-button"),
    ).not.toBeVisible();
    expect(devSettingsRequests).toEqual([]);
  });

  test("5. Renders HTTP 403 Access Restricted fallback with zero mutation controls when backend denies access", async ({
    page,
  }) => {
    // Simulates an admin whose role was revoked server-side before page fetch completes
    await mockAuthenticatedSession(page, {
      roles: ["TENANT_ADMIN"],
      name: "Revoked Admin",
      email: "revoked@acme.com",
    });

    await page.route(
      (url) => url.pathname === "/api/v1/tenants/settings",
      async (route) => {
        if (route.request().method() === "OPTIONS") {
          await fulfillCorsJson(route, 204);
          return;
        }
        await fulfillCorsJson(route, 403, {
          detail: "Forbidden: insufficient permissions",
        });
      },
    );

    await page.goto("/workspace-settings");

    await expect(
      page.getByTestId("workspace-settings-forbidden-state"),
    ).toBeVisible();
    await expect(
      page.getByText("HTTP 403 · ACCESS RESTRICTED"),
    ).toBeVisible();
    await expect(
      page.getByTestId("workspace-settings-save-button"),
    ).not.toBeVisible();
    await expect(
      page.getByTestId("workspace-settings-form"),
    ).not.toBeVisible();
  });

  test("6. Navigating via Administration sidebar link opens Workspace Settings cleanly with zero horizontal overflow", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await mockAuthenticatedSession(page, {
      roles: ["TENANT_ADMIN"],
      tenantName: "Acme Core",
    });

    await setupStatefulTenantSettingsApi(page, {
      name: "Acme Core",
      slug: "acme-core",
      ai_processing_mode: "cloud_zdr",
      retention_days: 90,
    });

    await page.goto("/");

    const navLink = page.getByTestId("nav-link-workspace-settings");
    await expect(navLink).toBeVisible();
    await navLink.click();

    await expect(page).toHaveURL("/workspace-settings");
    await expect(navLink).toHaveAttribute("aria-current", "page");
    await expect(
      page.getByRole("heading", { level: 1, name: "Workspace Settings" }),
    ).toBeVisible();

    const overflow = await page.evaluate(
      () =>
        document.documentElement.scrollWidth -
        document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);
  });
});
