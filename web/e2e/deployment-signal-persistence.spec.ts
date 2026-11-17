import { test, expect } from "@playwright/test";
import {
  makeMockProject,
  mockAuthenticatedSession,
  setupStatefulProjectsApi,
} from "./helpers/mock-auth";

test.describe("Deployment Signal Selection & Page Reload Persistence (SCRUM-385 / Task 18.5)", () => {
  test("Task 18.5 / TC-SIGNAL-PERSIST-005A: selecting argocd_postsync_webhook and saving persists across full browser reload", async ({
    page,
  }) => {
    // Arrange: Create project with default authoritative signal = 'github_deployments_api'
    const proj = makeMockProject({
      id: "11111111-1111-1111-1111-000000000001",
      name: "Core Payments",
      slug: "core-payments",
      authoritative_deployment_signal: "github_deployments_api",
    });

    await mockAuthenticatedSession(page, { roles: ["TENANT_ADMIN"] });
    await setupStatefulProjectsApi(page, [proj]);

    // Navigate to Project Settings -> Deployment Signal tab
    await page.goto("/projects/core-payments/settings");
    await page.getByTestId("project-tab-deployment-signal").click();
    await expect(page).toHaveURL(
      /\/projects\/core-payments\/settings\?tab=deployment-signal/,
    );

    // Initial state: GitHub Deployments API is checked, ArgoCD is unchecked, save is disabled
    const githubRadio = page.getByTestId("signal-option-github_deployments_api");
    const argocdRadio = page.getByTestId("signal-option-argocd_postsync_webhook");
    const directRadio = page.getByTestId("signal-option-direct_deployment_api");
    const saveButton = page.getByTestId("deployment-signal-save-button");

    await expect(githubRadio).toBeChecked();
    await expect(argocdRadio).not.toBeChecked();
    await expect(directRadio).not.toBeChecked();
    await expect(saveButton).toBeDisabled();

    // Select argocd_postsync_webhook
    await argocdRadio.click();
    await expect(argocdRadio).toBeChecked();
    await expect(githubRadio).not.toBeChecked();
    await expect(saveButton).toBeEnabled();

    // Verify ArgoCD configuration panel and webhook endpoint details are revealed
    await expect(page.getByText(/\/api\/v1\/webhooks\/gitops/i).first()).toBeVisible();
    await expect(page.getByText(/PostSync/i).first()).toBeVisible();

    // Save changes
    await saveButton.click();

    // Verify success toast notification
    const toast = page.getByTestId("deployment-signal-toast");
    await expect(toast).toBeVisible();
    await expect(toast).toContainText(/saved/i);

    // Full browser reload (F5 / hard reload)
    await page.reload();

    // Assert: URL tab persists
    await expect(page).toHaveURL(
      /\/projects\/core-payments\/settings\?tab=deployment-signal/,
    );

    // Assert: argocd_postsync_webhook remains checked and active across reload
    await expect(argocdRadio).toBeChecked();
    await expect(githubRadio).not.toBeChecked();
    await expect(directRadio).not.toBeChecked();

    // Assert: DOM attribute verification
    const isArgoChecked = await argocdRadio.evaluate(
      (node: HTMLInputElement) => node.checked,
    );
    expect(isArgoChecked).toBe(true);

    const isGithubChecked = await githubRadio.evaluate(
      (node: HTMLInputElement) => node.checked,
    );
    expect(isGithubChecked).toBe(false);

    // Assert: ArgoCD webhook details remain visible
    await expect(page.getByText(/\/api\/v1\/webhooks\/gitops/i).first()).toBeVisible();

    // Assert: Save button is disabled (no unsaved changes after reload)
    await expect(page.getByTestId("deployment-signal-save-button")).toBeDisabled();
  });

  test("TC-SIGNAL-PERSIST-005B: signal selection persists across in-app navigation and direct tab URL loads", async ({
    page,
  }) => {
    const proj = makeMockProject({
      id: "11111111-1111-1111-1111-000000000002",
      name: "Cart Service",
      slug: "cart-service",
      authoritative_deployment_signal: "github_deployments_api",
    });

    await mockAuthenticatedSession(page, { roles: ["TENANT_ADMIN"] });
    await setupStatefulProjectsApi(page, [proj]);

    // Direct navigation with ?tab=deployment-signal query parameter
    await page.goto("/projects/cart-service/settings?tab=deployment-signal");

    const argocdRadio = page.getByTestId("signal-option-argocd_postsync_webhook");
    const saveButton = page.getByTestId("deployment-signal-save-button");

    await argocdRadio.click();
    await saveButton.click();
    await expect(page.getByTestId("deployment-signal-toast")).toBeVisible();

    // In-app navigation to Projects directory
    await page.goto("/projects");
    await expect(page.getByTestId("project-card-cart-service")).toBeVisible();

    // Navigate back to project settings tab
    await page.goto("/projects/cart-service/settings?tab=deployment-signal");

    // Signal remains active
    await expect(
      page.getByTestId("signal-option-argocd_postsync_webhook"),
    ).toBeChecked();
    await expect(
      page.getByTestId("signal-option-github_deployments_api"),
    ).not.toBeChecked();
  });

  test("tri-signal persistence lifecycle: cyclic switching (github -> argocd -> direct -> github) persists across page reloads", async ({
    page,
  }) => {
    const proj = makeMockProject({
      id: "11111111-1111-1111-1111-000000000003",
      name: "Billing Engine",
      slug: "billing-engine",
      authoritative_deployment_signal: "github_deployments_api",
    });

    await mockAuthenticatedSession(page, { roles: ["TENANT_ADMIN"] });
    await setupStatefulProjectsApi(page, [proj]);

    await page.goto("/projects/billing-engine/settings?tab=deployment-signal");

    // 1. Switch to Direct Deployment Ingestion API and save
    await page.getByTestId("signal-option-direct_deployment_api").click();
    await expect(
      page
        .getByText(
          /\/api\/v1\/projects\/11111111-1111-1111-1111-000000000003\/deployments/i,
        )
        .first(),
    ).toBeVisible();
    await page.getByTestId("deployment-signal-save-button").click();
    await expect(page.getByTestId("deployment-signal-toast")).toBeVisible();

    // Reload page and assert Direct API is active
    await page.reload();
    await expect(
      page.getByTestId("signal-option-direct_deployment_api"),
    ).toBeChecked();
    await expect(
      page.getByTestId("signal-option-github_deployments_api"),
    ).not.toBeChecked();
    await expect(
      page.getByTestId("signal-option-argocd_postsync_webhook"),
    ).not.toBeChecked();
    await expect(
      page
        .getByText(
          /\/api\/v1\/projects\/11111111-1111-1111-1111-000000000003\/deployments/i,
        )
        .first(),
    ).toBeVisible();

    // 2. Switch back to GitHub Deployments API and save
    await page.getByTestId("signal-option-github_deployments_api").click();
    await page.getByTestId("deployment-signal-save-button").click();
    await expect(page.getByTestId("deployment-signal-toast")).toBeVisible();

    // Reload page and assert GitHub API is active
    await page.reload();
    await expect(
      page.getByTestId("signal-option-github_deployments_api"),
    ).toBeChecked();
    await expect(
      page.getByTestId("signal-option-direct_deployment_api"),
    ).not.toBeChecked();
    await expect(
      page.getByTestId("signal-option-argocd_postsync_webhook"),
    ).not.toBeChecked();
  });
});
