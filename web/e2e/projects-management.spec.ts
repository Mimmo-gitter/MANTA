import { test, expect, type Page } from "@playwright/test";
import type { ProjectItem } from "../src/types/project";
import {
  fulfillCorsJson,
  makeMockProject,
  makeMockRepository,
  mockAuthenticatedSession,
} from "./helpers/mock-auth";

async function setupStatefulProjectsApi(
  page: Page,
  initialProjects: ProjectItem[],
) {
  const projects = initialProjects.map((p) => ({
    ...p,
    repositories: [...p.repositories],
  }));

  await page.route(
    (url) => url.pathname === "/api/v1/projects",
    async (route) => {
      const method = route.request().method();
      if (method === "OPTIONS") {
        await fulfillCorsJson(route, 204);
        return;
      }
      if (method === "GET") {
        await fulfillCorsJson(route, 200, projects);
        return;
      }
      if (method === "POST") {
        const body = route.request().postDataJSON() as {
          name: string;
          slug?: string;
          tier_sla_hours?: number;
          wip_limit_per_dev?: number;
          pr_stale_threshold_hours?: number;
          authoritative_deployment_signal?: string;
        };
        const slug = body.slug || body.name.toLowerCase().replace(/[^a-z0-9]+/g, "-");
        if (projects.some((p) => p.slug === slug)) {
          await fulfillCorsJson(route, 409, {
            detail: `Project with slug '${slug}' already exists in this tenant.`,
          });
          return;
        }
        const created = makeMockProject({
          id: `11111111-1111-1111-1111-00000000000${projects.length + 1}`,
          name: body.name,
          slug,
          tier_sla_hours: body.tier_sla_hours ?? 24,
          wip_limit_per_dev: body.wip_limit_per_dev ?? 3,
          pr_stale_threshold_hours: body.pr_stale_threshold_hours ?? 48,
          authoritative_deployment_signal:
            body.authoritative_deployment_signal ?? "github_deployments_api",
          repositories: [],
        });
        projects.push(created);
        await fulfillCorsJson(route, 201, created);
        return;
      }
      await route.fallback();
    },
  );

  await page.route(
    (url) => /^\/api\/v1\/projects\/[^/]+$/.test(url.pathname),
    async (route) => {
      const method = route.request().method();
      if (method === "OPTIONS") {
        await fulfillCorsJson(route, 204);
        return;
      }
      const match = new URL(route.request().url()).pathname.match(
        /^\/api\/v1\/projects\/([^/]+)$/,
      );
      const key = match?.[1] ?? "";
      const project = projects.find((p) => p.id === key || p.slug === key);
      if (!project) {
        await fulfillCorsJson(route, 404, { detail: "Project not found" });
        return;
      }
      if (method === "GET") {
        await fulfillCorsJson(route, 200, project);
        return;
      }
      if (method === "DELETE") {
        project.is_active = false;
        project.updated_at = new Date().toISOString();
        await fulfillCorsJson(route, 204);
        return;
      }
      await route.fallback();
    },
  );

  await page.route(
    (url) => /^\/api\/v1\/projects\/[^/]+\/repositories$/.test(url.pathname),
    async (route) => {
      const method = route.request().method();
      if (method === "OPTIONS") {
        await fulfillCorsJson(route, 204);
        return;
      }
      const match = new URL(route.request().url()).pathname.match(
        /^\/api\/v1\/projects\/([^/]+)\/repositories$/,
      );
      const projectId = match?.[1] ?? "";
      const project = projects.find((p) => p.id === projectId);
      if (!project) {
        await fulfillCorsJson(route, 404, { detail: "Project not found" });
        return;
      }
      if (method === "POST") {
        const body = route.request().postDataJSON() as {
          repository_url?: string;
          full_name?: string;
          default_branch?: string;
          is_private?: boolean;
          integration_id?: string;
        };
        const fullName =
          body.full_name ||
          (body.repository_url || "").replace(/^https?:\/\/github\.com\//, "");
        const repo = makeMockRepository({
          id: `22222222-2222-2222-2222-00000000000${project.repositories.length + 1}`,
          project_id: project.id,
          integration_id: body.integration_id ?? "int-gh-1",
          full_name: fullName,
          default_branch: body.default_branch || "main",
          is_private: body.is_private ?? true,
        });
        project.repositories.push(repo);
        await fulfillCorsJson(route, 201, repo);
        return;
      }
      await route.fallback();
    },
  );

  await page.route(
    (url) =>
      /^\/api\/v1\/projects\/[^/]+\/repositories\/[^/]+$/.test(url.pathname),
    async (route) => {
      const method = route.request().method();
      if (method === "OPTIONS") {
        await fulfillCorsJson(route, 204);
        return;
      }
      const match = new URL(route.request().url()).pathname.match(
        /^\/api\/v1\/projects\/([^/]+)\/repositories\/([^/]+)$/,
      );
      const projectId = match?.[1] ?? "";
      const repoId = match?.[2] ?? "";
      const project = projects.find((p) => p.id === projectId);
      if (!project) {
        await fulfillCorsJson(route, 404, { detail: "Project not found" });
        return;
      }
      if (method === "DELETE") {
        project.repositories = project.repositories.filter((r) => r.id !== repoId);
        await fulfillCorsJson(route, 204);
        return;
      }
      await route.fallback();
    },
  );

  await page.route(
    (url) => /^\/api\/v1\/projects\/[^/]+\/deployment-signal$/.test(url.pathname),
    async (route) => {
      const method = route.request().method();
      if (method === "OPTIONS") {
        await fulfillCorsJson(route, 204);
        return;
      }
      const match = new URL(route.request().url()).pathname.match(
        /^\/api\/v1\/projects\/([^/]+)\/deployment-signal$/,
      );
      const projectId = match?.[1] ?? "";
      const project = projects.find((p) => p.id === projectId);
      if (!project) {
        await fulfillCorsJson(route, 404, { detail: "Project not found" });
        return;
      }
      if (method === "GET") {
        await fulfillCorsJson(route, 200, {
          project_id: project.id,
          tenant_id: project.tenant_id,
          authoritative_deployment_signal:
            project.authoritative_deployment_signal ?? "github_deployments_api",
          updated_at: project.updated_at,
        });
        return;
      }
      if (method === "PATCH") {
        const body = route.request().postDataJSON() as {
          authoritative_deployment_signal: string;
        };
        project.authoritative_deployment_signal =
          body.authoritative_deployment_signal;
        project.authoritative_cd_signal = body.authoritative_deployment_signal;
        project.updated_at = new Date().toISOString();
        await fulfillCorsJson(route, 200, {
          project_id: project.id,
          tenant_id: project.tenant_id,
          authoritative_deployment_signal: project.authoritative_deployment_signal,
          updated_at: project.updated_at,
        });
        return;
      }
      await route.fallback();
    },
  );

  return projects;
}

test.describe("Projects Directory, Repository Linking & Deployment Signal (SCRUM-376 / Figma 17.1 & 18.1)", () => {
  test("Empty projects directory -> Create Project modal + 409 conflict -> redirects to project settings", async ({
    page,
  }) => {
    await mockAuthenticatedSession(page, { roles: ["TENANT_ADMIN"] });
    const existing = makeMockProject({
      name: "Legacy Monolith",
      slug: "legacy-monolith",
    });
    await setupStatefulProjectsApi(page, []);

    await page.goto("/projects");
    await expect(page.getByTestId("projects-empty-state")).toBeVisible();

    await page.getByTestId("create-first-project-button").click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();

    // Auto-slugification
    await page.getByTestId("create-project-name-input").fill("Checkout Core");
    await expect(page.getByTestId("create-project-slug-input")).toHaveValue(
      "checkout-core",
    );

    await page.getByTestId("create-project-submit").click();
    await expect(page).toHaveURL("/projects/checkout-core/settings");
    await expect(
      page.getByRole("heading", { name: /Checkout Core · Settings/i }),
    ).toBeVisible();

    // Verify 409 conflict feedback when creating a duplicate slug
    await mockAuthenticatedSession(page, { roles: ["TENANT_ADMIN"] });
    await setupStatefulProjectsApi(page, [existing]);
    await page.goto("/projects");
    await page.getByTestId("create-project-button").click();
    await page.getByTestId("create-project-name-input").fill("Legacy Monolith");
    await page.getByTestId("create-project-submit").click();
    await expect(page.getByTestId("create-project-error")).toContainText(
      /already exists/i,
    );
  });

  test("Directory filter, URL ?q search, error/retry, and disabled actions for non-admin & missing archive endpoint", async ({
    page,
  }) => {
    const projA = makeMockProject({
      id: "11111111-1111-1111-1111-000000000001",
      name: "Core Payments",
      slug: "core-payments",
      repositories: [makeMockRepository({ full_name: "org-a/payments" })],
    });
    const projB = makeMockProject({
      id: "11111111-1111-1111-1111-000000000002",
      name: "Search Indexer",
      slug: "search-indexer",
      is_active: false,
      repositories: [],
    });

    await mockAuthenticatedSession(page, { roles: ["TENANT_ADMIN"] });
    await setupStatefulProjectsApi(page, [projA, projB]);

    await page.goto("/projects?q=Payments");
    await expect(page.getByTestId("project-card-core-payments")).toBeVisible();
    await expect(page.getByTestId("project-card-search-indexer")).not.toBeVisible();

    await page.getByTestId("projects-filter-input").fill("");
    await expect(page.getByTestId("project-card-search-indexer")).toBeVisible();

    // Verify outer spacing between header/sidebar matches Access & Roles (px-[24px] py-[14px])
    const spacingCheck = await page.evaluate(() => {
      const main = document.querySelector("main");
      const summary = document.querySelector('[data-testid="projects-summary-strip"]');
      const nav = document.querySelector('nav[aria-label="Page Breadcrumb"]');
      if (!main || !summary || !nav) return null;
      const mainRect = main.getBoundingClientRect();
      const summaryRect = summary.getBoundingClientRect();
      const navRect = nav.getBoundingClientRect();
      return {
        leftInset: Math.round(summaryRect.left - mainRect.left),
        rightInset: Math.round(mainRect.right - summaryRect.right),
        topInset: Math.round(navRect.top - mainRect.top),
      };
    });
    expect(spacingCheck).not.toBeNull();
    expect(spacingCheck!.leftInset).toBeGreaterThanOrEqual(20);
    expect(spacingCheck!.rightInset).toBeGreaterThanOrEqual(20);
    expect(spacingCheck!.topInset).toBeGreaterThanOrEqual(12);

    // Custom dark filter popovers (Photo 3 style) filter projects and close on Escape
    await page.getByTestId("projects-status-filter-trigger").click();
    const statusPopover = page.getByTestId("projects-status-filter-popover");
    await expect(statusPopover).toBeVisible();
    await statusPopover.getByRole("option", { name: /Inactive/i }).click();
    await expect(statusPopover).not.toBeVisible();
    await expect(page.getByTestId("project-card-search-indexer")).toBeVisible();
    await expect(page.getByTestId("project-card-core-payments")).not.toBeVisible();

    await page.getByTestId("projects-status-filter-trigger").click();
    await expect(page.getByTestId("projects-status-filter-popover")).toBeVisible();
    await page.getByTestId("projects-status-filter-popover").getByRole("option", { name: /All statuses/i }).click();
    await expect(page.getByTestId("project-card-core-payments")).toBeVisible();

    await page.getByTestId("projects-repo-filter-trigger").click();
    const repoPopover = page.getByTestId("projects-repo-filter-popover");
    await expect(repoPopover).toBeVisible();
    await repoPopover.getByRole("option", { name: /With linked repos/i }).click();
    await expect(page.getByTestId("project-card-core-payments")).toBeVisible();
    await expect(page.getByTestId("project-card-search-indexer")).not.toBeVisible();

    await page.getByTestId("projects-repo-filter-trigger").click();
    await expect(page.getByTestId("projects-repo-filter-popover")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("projects-repo-filter-popover")).not.toBeVisible();

    await page.getByTestId("projects-repo-filter-trigger").click();
    await page.getByTestId("projects-repo-filter-popover").getByRole("option", { name: /All repos/i }).click();
    await expect(page.getByTestId("project-card-search-indexer")).toBeVisible();

    // Archive button opens confirmation modal (Cancel keeps active, Confirm archives)
    await page.getByTestId("project-card-menu-search-indexer").click();
    const archivedItemBtn = page.getByTestId("archive-project-action-search-indexer");
    await expect(archivedItemBtn).toBeVisible();
    await expect(archivedItemBtn).toBeDisabled();
    await page.keyboard.press("Escape");

    await page.getByTestId("project-card-menu-core-payments").click();
    const archiveBtn = page.getByTestId("archive-project-action-core-payments");
    await expect(archiveBtn).toBeVisible();
    await expect(archiveBtn).toBeEnabled();
    await archiveBtn.click();

    const archiveModal = page.getByTestId("archive-project-modal");
    await expect(archiveModal).toBeVisible();
    await page.getByTestId("archive-project-cancel-button").click();
    await expect(archiveModal).not.toBeVisible();

    await page.getByTestId("project-card-menu-core-payments").click();
    await page.getByTestId("archive-project-action-core-payments").click();
    await expect(archiveModal).toBeVisible();
    await page.getByTestId("archive-project-confirm-button").click();
    await expect(archiveModal).not.toBeVisible();

    await page.getByTestId("project-card-menu-core-payments").click();
    await expect(page.getByTestId("archive-project-action-core-payments")).toBeDisabled();
    await page.keyboard.press("Escape");

    // Developer sees Create Project rendered disabled (not removed)
    await mockAuthenticatedSession(page, {
      roles: ["DEVELOPER"],
      email: "dev@company.com",
    });
    await setupStatefulProjectsApi(page, [projA]);
    await page.goto("/projects");
    const devCreateBtn = page.getByTestId("create-project-button");
    await expect(devCreateBtn).toBeVisible();
    await expect(devCreateBtn).toBeDisabled();
    await expect(devCreateBtn).toHaveAttribute("aria-disabled", "true");
  });

  test("Project Settings -> Linked Repositories: No GitHub CTA (3F), URL validation, Link & Unlink repository", async ({
    page,
  }) => {
    const proj = makeMockProject({
      id: "11111111-1111-1111-1111-000000000001",
      name: "Core Payments",
      slug: "core-payments",
      repositories: [],
    });

    // 1. Admin without GitHub integration sees Frame 3F CTA -> /integrations
    await mockAuthenticatedSession(page, {
      roles: ["TENANT_ADMIN"],
      integrations: [],
    });
    await setupStatefulProjectsApi(page, [proj]);
    await page.goto("/projects/core-payments/settings");

    const connectGithubCta = page.getByTestId("connect-github-integration-cta");
    await expect(connectGithubCta).toBeVisible();
    await expect(connectGithubCta).toHaveAttribute("href", "/integrations");

    // 2. Admin WITH active GitHub integration can link, validate against gitlab URL, and unlink
    await mockAuthenticatedSession(page, {
      roles: ["TENANT_ADMIN"],
      integrations: [
        {
          id: "int-gh-1",
          provider: "github",
          external_account_id: "org-alpha",
          is_active: true,
          created_at: "2026-11-10T10:00:00Z",
        },
      ],
    });
    await setupStatefulProjectsApi(page, [proj]);
    await page.goto("/projects/core-payments/settings");

    await page.getByTestId("empty-link-repositories-button").click();
    const modal = page.getByRole("dialog");
    await expect(modal).toBeVisible();

    // Multi-entry validation (Frame 3E): rejects invalid entries simultaneously and allows row removal
    await page
      .getByTestId("link-repo-url-input")
      .fill("https://gitlab.com/org-alpha/service");
    await page.getByTestId("add-repo-entry-button").click();
    await page.getByTestId("link-repo-url-input-1").fill("not a valid repo");
    await page.getByTestId("link-repo-submit").click();

    await expect(page.getByTestId("link-repo-error")).toContainText(/GitHub/i);
    await expect(page.getByTestId("link-repo-entry-error-0")).toContainText(/GitHub/i);
    await expect(page.getByTestId("link-repo-entry-error-1")).toBeVisible();
    await page.getByTestId("remove-repo-entry-1").click();

    // Accepts valid owner/repo input and displays Frame 3C floating toast + highlighted row
    await page.getByTestId("link-repo-url-input").fill("org-alpha/payments-api");
    await page.getByTestId("link-repo-submit").click();
    await expect(modal).not.toBeVisible();

    await expect(
      page.getByTestId("linked-repo-row-org-alpha/payments-api"),
    ).toBeVisible();
    await expect(
      page.getByTestId("just-linked-badge-org-alpha/payments-api"),
    ).toBeVisible();

    const linkedToast = page.getByTestId("linked-repos-toast");
    await expect(linkedToast).toBeVisible();

    // Unlink repository
    await page.getByTestId("unlink-repo-button-org-alpha/payments-api").click();
    await page.getByTestId("confirm-unlink-repository-button").click();
    await expect(
      page.getByTestId("linked-repo-row-org-alpha/payments-api"),
    ).not.toBeVisible();
  });

  test("Project Settings -> Deployment Signal: Admin saves + Undo; Developer sees read-only disabled controls", async ({
    page,
  }) => {
    const proj = makeMockProject({
      id: "11111111-1111-1111-1111-000000000001",
      name: "Core Payments",
      slug: "core-payments",
      authoritative_deployment_signal: "github_deployments_api",
    });

    // 1. Admin switches GitHub -> ArgoCD -> Direct API, saves, and Undoes
    await mockAuthenticatedSession(page, { roles: ["TENANT_ADMIN"] });
    await setupStatefulProjectsApi(page, [proj]);

    await page.goto("/projects/core-payments/settings");
    await page.getByTestId("project-tab-deployment-signal").click();
    await expect(page).toHaveURL(
      /\/projects\/core-payments\/settings\?tab=deployment-signal/,
    );

    await expect(
      page.getByTestId("signal-option-github_deployments_api"),
    ).toBeChecked();
    await expect(page.getByTestId("deployment-signal-save-button")).toBeDisabled();

    // Frame 4B ArgoCD reveals webhook panel
    await page.getByTestId("signal-option-argocd_postsync_webhook").click();
    await expect(page.getByText(/\/api\/v1\/webhooks\/gitops/i).first()).toBeVisible();

    // Frame 4C Direct Deployment API reveals cURL panel using project UUID
    await page.getByTestId("signal-option-direct_deployment_api").click();
    await expect(
      page
        .getByText(
          /\/api\/v1\/projects\/11111111-1111-1111-1111-000000000001\/deployments/i,
        )
        .first(),
    ).toBeVisible();

    await page.getByTestId("deployment-signal-save-button").click();
    const toast = page.getByTestId("deployment-signal-toast");
    await expect(toast).toBeVisible();

    // Undo restores previous selection
    await page.getByTestId("deployment-signal-undo-button").click();
    await expect(
      page.getByTestId("signal-option-github_deployments_api"),
    ).toBeChecked();

    // Task 18.2: Save new signal and verify real page.reload() + URL ?tab= persistence
    await page.getByTestId("signal-option-argocd_postsync_webhook").click();
    await page.getByTestId("deployment-signal-save-button").click();
    await expect(page.getByTestId("deployment-signal-toast")).toBeVisible();

    await page.reload();
    await expect(page).toHaveURL(
      /\/projects\/core-payments\/settings\?tab=deployment-signal/,
    );
    await expect(
      page.getByTestId("signal-option-argocd_postsync_webhook"),
    ).toBeChecked();

    // 2. Non-admin Developer sees Read-Only Banner, disabled controls, and hidden action buttons (Frame 4F)
    await mockAuthenticatedSession(page, {
      roles: ["DEVELOPER"],
      email: "dev@company.com",
    });
    await setupStatefulProjectsApi(page, [proj]);

    await page.goto("/projects/core-payments/settings?tab=deployment-signal");

    const readonlyBanner = page.getByTestId("deployment-signal-readonly-banner");
    await expect(readonlyBanner).toBeVisible();
    await expect(readonlyBanner).toContainText("Read-Only");
    await expect(readonlyBanner).toContainText("Role: Developer");

    await expect(
      page.getByTestId("signal-option-argocd_postsync_webhook"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("deployment-signal-cancel-button"),
    ).not.toBeVisible();
    await expect(
      page.getByTestId("deployment-signal-save-button"),
    ).not.toBeVisible();
  });

  test("Figma 1D/1E & 2C/2D: filter zero-match chips, error trace + retry, invalid/duplicate slug recovery", async ({
    page,
  }) => {
    const existing = makeMockProject({
      id: "11111111-1111-1111-1111-000000000001",
      name: "Core Payments",
      slug: "core-payments",
    });

    await mockAuthenticatedSession(page, { roles: ["TENANT_ADMIN"] });
    await setupStatefulProjectsApi(page, [existing]);

    let failCount = 0;
    await page.route(
      (url) => url.pathname === "/api/v1/projects",
      async (route) => {
        if (route.request().method() === "OPTIONS") {
          await fulfillCorsJson(route, 204);
          return;
        }
        if (route.request().method() === "GET" && failCount < 2) {
          failCount += 1;
          await route.fulfill({
            status: 500,
            contentType: "application/json",
            headers: {
              "access-control-allow-origin": "http://localhost:3000",
              "access-control-allow-credentials": "true",
              "access-control-expose-headers": "x-request-id",
              "x-request-id": "trace-e2e-500-99",
            },
            body: JSON.stringify({ detail: "Registry timeout" }),
          });
          return;
        }
        await route.fallback();
      },
    );

    // First load triggers Frame 1E diagnostic trace expanded by default; toggle + Retry recovers
    await page.goto("/projects");
    await expect(page.getByText(/Project Registry Error · HTTP 500/i)).toBeVisible();
    await expect(page.getByTestId("projects-diagnostic-trace")).toContainText(
      "TRACE ID: trace-e2e-500-99",
    );
    await page.getByTestId("projects-error-details-toggle").click();
    await expect(page.getByTestId("projects-diagnostic-trace")).not.toBeVisible();
    await page.getByTestId("projects-error-details-toggle").click();
    await expect(page.getByTestId("projects-diagnostic-trace")).toContainText(
      "TRACE ID:",
    );
    await page.getByTestId("projects-retry-button").click();
    await expect(page.getByTestId("project-card-core-payments")).toBeVisible();

    // Frame 1D zero-match filter bar + prefilled Create CTA
    await page.getByTestId("projects-filter-input").fill("Nonexistent Service");
    await expect(page.getByTestId("projects-filtered-zero-badge")).toHaveText(
      "0 of 1",
    );
    await expect(page.getByTestId("filter-chip-search")).toBeVisible();
    await page.getByTestId("projects-create-from-search-button").click();
    await expect(page.getByTestId("create-project-name-input")).toHaveValue(
      "Nonexistent Service",
    );

    // Frame 2D invalid slug characters & Frame 2C duplicate slug recovery
    await page.getByTestId("create-project-slug-input").fill("Bad_Slug!");
    await expect(page.getByTestId("create-project-error")).toContainText(
      /invalid characters/i,
    );

    await page.getByTestId("create-project-slug-input").fill("core-payments");
    await expect(page.getByTestId("create-project-error")).toContainText(
      /already/i,
    );
    await page.getByRole("button", { name: /Use ‘core-payments-v2’/i }).click();
    await expect(page.getByTestId("create-project-slug-input")).toHaveValue(
      "core-payments-v2",
    );
    await page.getByTestId("create-project-submit").click();
    await expect(page).toHaveURL("/projects/core-payments-v2/settings");
  });
});
