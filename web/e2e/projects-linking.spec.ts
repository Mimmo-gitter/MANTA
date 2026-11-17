import { test, expect } from "@playwright/test";
import {
  fulfillCorsJson,
  makeMockProject,
  makeMockRepository,
  mockAuthenticatedSession,
  setupStatefulProjectsApi,
} from "./helpers/mock-auth";

const MOCK_GITHUB_INTEGRATION = {
  id: "int-gh-1",
  provider: "github",
  external_account_id: "acme-corp",
  is_active: true,
  created_at: "2026-11-10T10:00:00Z",
};

test.describe("Task 17.6: Project Creation, GitHub Repository Linking & Role Restrictions (US-17 / SCRUM-380)", () => {
  test.beforeEach(async () => {
    test.setTimeout(60000);
  });

  test("Journey: Create project via modal -> redirect to settings -> link GitHub repo -> verify persistence on reload", async ({
    page,
  }) => {
    // 1. Authenticate as Tenant Admin with an active GitHub App integration
    await mockAuthenticatedSession(page, {
      roles: ["TENANT_ADMIN"],
      name: "Pavlo Zhuk",
      email: "p.zhuk@company.com",
      tenantId: "11111111-1111-1111-1111-111111111111",
      tenantName: "Tenant Alpha Corp",
      integrations: [MOCK_GITHUB_INTEGRATION],
    });

    // Start with empty project list
    await setupStatefulProjectsApi(page, []);

    // 2. Navigate to Projects directory and verify empty state
    await page.goto("/projects");
    await expect(page.getByTestId("projects-empty-state")).toBeVisible();

    // 3. Open Create Project modal from the empty state
    await page.getByTestId("create-first-project-button").click();
    const createModal = page.getByRole("dialog");
    await expect(createModal).toBeVisible();
    await expect(createModal.getByRole("heading", { name: "Create New Project" })).toBeVisible();

    // 4. Fill project name and verify automatic slug generation
    const nameInput = page.getByTestId("create-project-name-input");
    const slugInput = page.getByTestId("create-project-slug-input");
    await nameInput.fill("Billing Engine API");
    await expect(slugInput).toHaveValue("billing-engine-api");

    // 5. Submit project creation
    await page.getByTestId("create-project-submit").click();

    // 6. Assert automatic redirection to Project Settings page
    await expect(page).toHaveURL("/projects/billing-engine-api/settings");
    await expect(
      page.getByRole("heading", { name: /Billing Engine API · Settings/i }),
    ).toBeVisible();
    await expect(page.getByTestId("project-tab-repositories")).toBeVisible();

    // 7. Verify empty linked repositories state in Project Settings
    const emptyLinkBtn = page.getByTestId("empty-link-repositories-button");
    await expect(emptyLinkBtn).toBeVisible();

    // 8. Open Link Repositories modal
    await emptyLinkBtn.click();
    const linkModal = page.getByRole("dialog");
    await expect(linkModal).toBeVisible();
    await expect(
      linkModal.getByRole("heading", { name: /Link Repositories to Billing Engine API/i }),
    ).toBeVisible();

    // 9. Enter GitHub repository URL/slug, default branch, and submit
    await page
      .getByTestId("link-repo-url-input")
      .fill("https://github.com/acme-corp/billing-engine");
    await expect(page.getByTestId("link-repo-branch-input")).toHaveValue("main");
    await expect(page.getByTestId("link-repo-private-checkbox")).toBeChecked();

    await page.getByTestId("link-repo-submit").click();
    await expect(linkModal).not.toBeVisible();

    // 10. Assert success toast and table row with Just Linked badge
    await expect(page.getByTestId("linked-repos-toast")).toBeVisible();
    const linkedRow = page.getByTestId("linked-repo-row-acme-corp/billing-engine");
    await expect(linkedRow).toBeVisible();
    await expect(
      page.getByTestId("just-linked-badge-acme-corp/billing-engine"),
    ).toBeVisible();
    await expect(linkedRow).toContainText("main");
    await expect(linkedRow).toContainText("Private");

    // 11. VERIFY PERSISTENCE ACROSS PAGE RELOAD
    await page.reload();
    await expect(page).toHaveURL("/projects/billing-engine-api/settings");
    await expect(
      page.getByRole("heading", { name: /Billing Engine API · Settings/i }),
    ).toBeVisible({ timeout: 15000 });

    // Table must still contain the linked repository after page reload
    const reloadedRow = page.getByTestId("linked-repo-row-acme-corp/billing-engine");
    await expect(reloadedRow).toBeVisible();
    await expect(reloadedRow).toContainText("main");
    await expect(reloadedRow).toContainText("Private");
    await expect(page.getByTestId("empty-link-repositories-button")).not.toBeVisible();

    // 12. Navigate back to /projects directory and assert project card reflects the linked repo
    await page.goto("/projects");
    const projectCard = page.getByTestId("project-card-billing-engine-api");
    await expect(projectCard).toBeVisible();
    await expect(projectCard).toContainText("Billing Engine API");
    await expect(projectCard).toContainText("1 repo · GitHub");
    await expect(projectCard).toContainText("Active");
  });

  test("Role restrictions: alpha_dev (DEVELOPER) cannot create projects, link repos, or unlink repos", async ({
    page,
  }) => {
    // Existing project owned by Tenant Alpha
    const existingProject = makeMockProject({
      id: "11111111-1111-1111-1111-000000000001",
      name: "Alpha Core Engine",
      slug: "alpha-core-engine",
      repositories: [
        makeMockRepository({
          id: "22222222-2222-2222-2222-000000000001",
          project_id: "11111111-1111-1111-1111-000000000001",
          full_name: "acme-corp/alpha-core-backend",
          default_branch: "main",
          is_private: true,
        }),
      ],
    });

    // 1. Authenticate as alpha_dev with DEVELOPER role
    await mockAuthenticatedSession(page, {
      sub: "alpha_dev",
      preferredUsername: "alpha_dev",
      name: "Alpha Developer",
      email: "alpha_dev@tenanta.com",
      tenantId: "11111111-1111-1111-1111-111111111111",
      tenantName: "Tenant Alpha Corp",
      roles: ["DEVELOPER"],
      integrations: [MOCK_GITHUB_INTEGRATION],
    });

    // Setup stateful API with DEVELOPER role enforcement (returns 403 on mutations)
    await setupStatefulProjectsApi(page, [existingProject], {
      currentRoles: ["DEVELOPER"],
    });

    // 2. Assert role badge on portal
    await page.goto("/projects");
    await expect(page.getByTestId("user-role-badge")).toHaveText("Developer");

    // 3. On /projects directory: Create Project button is rendered disabled
    const devCreateBtn = page.getByTestId("create-project-button");
    await expect(devCreateBtn).toBeVisible();
    await expect(devCreateBtn).toBeDisabled();
    await expect(devCreateBtn).toHaveAttribute("aria-disabled", "true");
    await expect(devCreateBtn).toHaveAttribute(
      "title",
      "Only Tenant Administrators can create projects",
    );

    // Clicking disabled button must not open the Create Project modal
    await devCreateBtn.click({ force: true });
    await expect(page.getByRole("dialog")).not.toBeVisible();

    // 4. On Project Settings (/projects/alpha-core-engine/settings):
    await page.goto("/projects/alpha-core-engine/settings");

    // Read-only banner is displayed with developer role indicators
    const readonlyBanner = page.getByTestId("repos-readonly-banner");
    await expect(readonlyBanner).toBeVisible();
    await expect(readonlyBanner).toContainText("Read-Only");
    await expect(readonlyBanner).toContainText("Role: Developer");
    await expect(readonlyBanner).toContainText(
      "Only Tenant Administrators can link or unlink GitHub repositories.",
    );

    // Header Link Repositories button is disabled
    const headerLinkBtn = page.getByTestId("header-link-repositories-button");
    await expect(headerLinkBtn).toBeVisible();
    await expect(headerLinkBtn).toBeDisabled();
    await expect(headerLinkBtn).toHaveAttribute("aria-disabled", "true");
    await expect(headerLinkBtn).toHaveAttribute(
      "title",
      "Only Tenant Administrators can link GitHub repositories",
    );

    // Unlink button for existing repository is disabled
    const unlinkBtn = page.getByTestId(
      "unlink-repo-button-acme-corp/alpha-core-backend",
    );
    await expect(unlinkBtn).toBeVisible();
    await expect(unlinkBtn).toBeDisabled();
    await expect(unlinkBtn).toHaveAttribute("aria-disabled", "true");
    await expect(unlinkBtn).toHaveAttribute(
      "title",
      "Only Tenant Administrators can unlink repositories",
    );
  });

  test("Role restrictions: alpha_dev sees disabled empty-state link button on project without repositories", async ({
    page,
  }) => {
    const emptyProject = makeMockProject({
      id: "11111111-1111-1111-1111-000000000002",
      name: "Empty Project",
      slug: "empty-project",
      repositories: [],
    });

    await mockAuthenticatedSession(page, {
      sub: "alpha_dev",
      preferredUsername: "alpha_dev",
      name: "Alpha Developer",
      roles: ["DEVELOPER"],
      integrations: [MOCK_GITHUB_INTEGRATION],
    });

    await setupStatefulProjectsApi(page, [emptyProject], {
      currentRoles: ["DEVELOPER"],
    });

    await page.goto("/projects/empty-project/settings");
    const emptyLinkBtn = page.getByTestId("empty-link-repositories-button");
    await expect(emptyLinkBtn).toBeVisible();
    await expect(emptyLinkBtn).toBeDisabled();
    await expect(emptyLinkBtn).toHaveAttribute("aria-disabled", "true");
    await expect(emptyLinkBtn).toHaveAttribute(
      "title",
      "Only Tenant Administrators can link repositories",
    );
  });

  test("API RBAC: Direct mutation requests from alpha_dev receive HTTP 403 Forbidden", async ({
    page,
  }) => {
    const existingProject = makeMockProject({
      id: "11111111-1111-1111-1111-000000000001",
      name: "Alpha Core Engine",
      slug: "alpha-core-engine",
      repositories: [
        makeMockRepository({
          id: "22222222-2222-2222-2222-000000000001",
          project_id: "11111111-1111-1111-1111-000000000001",
          full_name: "acme-corp/alpha-core-backend",
        }),
      ],
    });

    await mockAuthenticatedSession(page, {
      sub: "alpha_dev",
      preferredUsername: "alpha_dev",
      name: "Alpha Developer",
      roles: ["DEVELOPER"],
    });

    await setupStatefulProjectsApi(page, [existingProject], {
      currentRoles: ["DEVELOPER"],
    });

    await page.goto("/projects");

    // Attempt direct POST /api/v1/projects from browser context
    const createRes = await page.evaluate(async () => {
      const res = await fetch("/api/v1/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: "Rogue Project by Dev" }),
      });
      return { status: res.status, body: await res.json() };
    });
    expect(createRes.status).toBe(403);
    expect(createRes.body).toEqual({
      detail: "Forbidden: insufficient permissions",
    });

    // Attempt direct POST /api/v1/projects/{id}/repositories
    const linkRes = await page.evaluate(async (projectId) => {
      const res = await fetch(`/api/v1/projects/${projectId}/repositories`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ full_name: "acme-corp/rogue-dev-repo" }),
      });
      return { status: res.status, body: await res.json() };
    }, existingProject.id);
    expect(linkRes.status).toBe(403);
    expect(linkRes.body).toEqual({
      detail: "Forbidden: insufficient permissions",
    });

    // Attempt direct DELETE /api/v1/projects/{id}/repositories/{repoId}
    const unlinkRes = await page.evaluate(
      async ({ projectId, repoId }) => {
        const res = await fetch(
          `/api/v1/projects/${projectId}/repositories/${repoId}`,
          {
            method: "DELETE",
          },
        );
        return { status: res.status };
      },
      {
        projectId: existingProject.id,
        repoId: "22222222-2222-2222-2222-000000000001",
      },
    );
    expect(unlinkRes.status).toBe(403);
  });

  test("Validation: duplicate project slug (409 Conflict) and invalid repo URLs are cleanly handled", async ({
    page,
  }) => {
    const existing = makeMockProject({
      id: "11111111-1111-1111-1111-000000000001",
      name: "Existing Service",
      slug: "existing-service",
    });

    await mockAuthenticatedSession(page, {
      roles: ["TENANT_ADMIN"],
      integrations: [MOCK_GITHUB_INTEGRATION],
    });
    await setupStatefulProjectsApi(page, [existing]);

    await page.goto("/projects");
    await page.getByTestId("create-project-button").click();

    // 1. Duplicate slug detection
    await page.getByTestId("create-project-name-input").fill("Existing Service");
    await expect(page.getByTestId("create-project-slug-input")).toHaveValue(
      "existing-service",
    );
    await page.getByTestId("create-project-submit").click();

    await expect(page.getByTestId("create-project-error")).toContainText(
      /already exists/i,
    );

    // Cancel modal
    await page.getByRole("button", { name: /Cancel/i }).click();
    await expect(page.getByRole("dialog")).not.toBeVisible();

    // 2. Repository URL format validation in Project Settings
    await page.goto("/projects/existing-service/settings");
    await page.getByTestId("empty-link-repositories-button").click();
    const linkModal = page.getByRole("dialog");
    await expect(linkModal).toBeVisible();

    // Non-GitHub URL
    await page
      .getByTestId("link-repo-url-input")
      .fill("https://gitlab.com/acme-corp/gitlab-service");
    await page.getByTestId("link-repo-submit").click();

    await expect(page.getByTestId("link-repo-error")).toContainText(
      /Only GitHub repositories are supported/i,
    );
    await expect(page.getByTestId("link-repo-entry-error-0")).toBeVisible();

    // Fix to valid GitHub repository
    await page
      .getByTestId("link-repo-url-input")
      .fill("acme-corp/valid-github-repo");
    await page.getByTestId("link-repo-submit").click();
    await expect(linkModal).not.toBeVisible();

    await expect(
      page.getByTestId("linked-repo-row-acme-corp/valid-github-repo"),
    ).toBeVisible();
  });

  test("Lifecycle: Unlink repository -> confirm dialog -> verify removal persists across reload", async ({
    page,
  }) => {
    const projectWithRepo = makeMockProject({
      id: "11111111-1111-1111-1111-000000000001",
      name: "Orders Engine",
      slug: "orders-engine",
      repositories: [
        makeMockRepository({
          id: "22222222-2222-2222-2222-000000000001",
          project_id: "11111111-1111-1111-1111-000000000001",
          full_name: "acme-corp/orders-service",
          default_branch: "main",
          is_private: true,
        }),
      ],
    });

    await mockAuthenticatedSession(page, {
      roles: ["TENANT_ADMIN"],
      integrations: [MOCK_GITHUB_INTEGRATION],
    });
    await setupStatefulProjectsApi(page, [projectWithRepo]);

    await page.goto("/projects/orders-engine/settings");
    await expect(
      page.getByTestId("linked-repo-row-acme-corp/orders-service"),
    ).toBeVisible();

    // Open unlink modal
    await page
      .getByTestId("unlink-repo-button-acme-corp/orders-service")
      .click();
    const unlinkModal = page.getByRole("dialog");
    await expect(unlinkModal).toBeVisible();
    await expect(
      unlinkModal.getByRole("heading", {
        name: "Unlink Repository from Project?",
      }),
    ).toBeVisible();

    // Cancel preserves repository
    await unlinkModal.getByRole("button", { name: "Cancel" }).click();
    await expect(unlinkModal).not.toBeVisible();
    await expect(
      page.getByTestId("linked-repo-row-acme-corp/orders-service"),
    ).toBeVisible();

    // Reopen and confirm unlink
    await page
      .getByTestId("unlink-repo-button-acme-corp/orders-service")
      .click();
    await page.getByTestId("confirm-unlink-repository-button").click();
    await expect(unlinkModal).not.toBeVisible();

    // Repository is removed from the table and empty state is shown
    await expect(
      page.getByTestId("linked-repo-row-acme-corp/orders-service"),
    ).not.toBeVisible();
    await expect(
      page.getByTestId("empty-link-repositories-button"),
    ).toBeVisible();

    // Verify persistence across page reload
    await page.reload();
    await expect(
      page.getByTestId("linked-repo-row-acme-corp/orders-service"),
    ).not.toBeVisible();
    await expect(
      page.getByTestId("empty-link-repositories-button"),
    ).toBeVisible();
  });
});
