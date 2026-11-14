import { test, expect } from "@playwright/test";
import { fulfillCorsJson, mockAuthenticatedSession } from "./helpers/mock-auth";

const SAMPLE_PROJECT = {
  id: "11111111-1111-1111-1111-111111111101",
  name: "Core Platform API",
  slug: "core-platform-api",
  tenant_id: "11111111-1111-1111-1111-111111111111",
  created_at: "2026-11-10T10:00:00Z",
  repositories: [],
};

test.describe("Figma Portal Shell Screens & Accessibility Suite", () => {
  // 1. Figma 325:2340 — Default Desktop Application Shell
  test("Figma 325:2340: Default Application Shell renders 216px sidebar, real MANTA logo, SVG icons, and 48px header", async ({
    page,
  }) => {
    await mockAuthenticatedSession(page, {
      roles: ["TENANT_ADMIN"],
      tenantName: "Alpha Workspace",
      projects: [SAMPLE_PROJECT],
    });

    await page.setViewportSize({ width: 1280, height: 722 });
    await page.goto("/");

    await expect(page).toHaveTitle(/MANTA/);
    const skipLink = page.locator("a[href='#main-content']");
    await expect(skipLink).toBeAttached();

    await expect(page.getByTestId("overview-default-state")).toBeVisible();
    await expect(page.getByTestId("overview-state-bar")).not.toBeAttached();

    const sidebar = page.getByTestId("portal-sidebar");
    await expect(sidebar).toBeVisible();
    const sidebarBox = await sidebar.boundingBox();
    expect(sidebarBox?.width).toBeCloseTo(216, 2);

    const logoImg = sidebar.locator("img[src='/icons/shell/f14d9.png']");
    await expect(logoImg).toBeVisible();

    await expect(sidebar.getByText("M.A.N.T.A.")).toBeVisible();
    await expect(sidebar.getByText("v2.4.0")).toBeVisible();
    await expect(sidebar.getByText("WORKSPACE", { exact: true })).toBeVisible();
    await expect(sidebar.getByText("ENGINEERING", { exact: true })).toBeVisible();
    await expect(sidebar.getByText("ADMINISTRATION", { exact: true })).toBeVisible();
    await expect(page.getByTestId("sidebar-footer")).not.toBeVisible();

    const header = page.getByTestId("portal-header");
    await expect(header).toBeVisible();
    const headerBox = await header.boundingBox();
    expect(headerBox?.height).toBeCloseTo(48, 2);

    await expect(header.getByText("Console")).toBeVisible();
    const wsTrigger = page.getByTestId("workspace-selector-trigger");
    await expect(wsTrigger).toBeVisible();
    await expect(wsTrigger).toContainText("Alpha Workspace");
    await expect(
      wsTrigger.locator("img[src='/icons/shell/lightning-emerald.svg']"),
    ).toBeVisible();
    await expect(page.getByTestId("environment-badge")).toContainText(
      "PRODUCTION",
    );
    await expect(page.getByTestId("header-alerts-link")).toBeVisible();
  });

  // 2. Figma 321:1283 & 321:1453 — Profile Menu Open & Header Search
  test("Figma 321:1283: User Profile Menu opens 256px card with real claims and role badge", async ({
    page,
  }) => {
    await mockAuthenticatedSession(page, {
      roles: ["SOLUTION_ARCHITECT"],
      name: "Olena Koval",
      email: "olena.koval@company.com",
      tenantName: "Alpha Workspace",
      projects: [SAMPLE_PROJECT],
    });

    await page.setViewportSize({ width: 1280, height: 722 });
    await page.goto("/");

    await expect(page.getByTestId("user-role-badge")).toHaveText(
      "Solution Architect",
    );

    const profileTrigger = page.getByTestId("user-profile-menu-trigger");
    await expect(profileTrigger).toBeVisible();
    await profileTrigger.click();

    const userMenu = page.getByTestId("user-dropdown-menu");
    await expect(userMenu).toBeVisible();
    await expect(userMenu).toHaveCSS("width", "256px");
    const menuBox = await userMenu.boundingBox();
    expect(menuBox).not.toBeNull();
    expect(menuBox!.width).toBeCloseTo(256, 1);

    await expect(userMenu.getByText("OK")).toBeVisible();
    await expect(userMenu.getByText("Olena Koval")).toBeVisible();
    await expect(userMenu.getByText("Solution Architect")).toBeVisible();
    await expect(userMenu.getByText("olena.koval@company.com")).toBeVisible();

    await expect(
      userMenu.getByRole("menuitem", { name: "My Profile" }),
    ).toBeVisible();
    await expect(
      userMenu.getByRole("menuitem", { name: "Account Settings" }),
    ).toBeVisible();

    const signOutBtn = page.getByTestId("dropdown-signout-button");
    await expect(signOutBtn).toBeVisible();
    await expect(signOutBtn).toContainText("Sign Out");

    // Header search submits to /projects?q=...
    await page.keyboard.press("Escape");
    const searchForm = page.locator("header form[role='search']").first();
    const searchInput = searchForm.getByPlaceholder("Search projects...");
    await searchInput.fill("core");
    await searchInput.press("Enter");
    await expect(page).toHaveURL(/\/projects\?q=core/);
  });

  // 3. Figma 334:5847 & 334:5977 — Active Workspace Menu (No fake workspace switcher)
  test("Figma 334:5847: Workspace Menu displays current authenticated tenant without mock switchers", async ({
    page,
  }) => {
    await mockAuthenticatedSession(page, {
      roles: ["TENANT_ADMIN"],
      tenantName: "Vanguard Systems",
      projects: [SAMPLE_PROJECT],
    });

    await page.setViewportSize({ width: 1280, height: 722 });
    await page.goto("/");

    const wsTrigger = page.getByTestId("workspace-selector-trigger");
    await expect(wsTrigger).toBeVisible();
    await wsTrigger.click();

    const wsMenu = page.getByTestId("workspace-dropdown-menu");
    await expect(wsMenu).toBeVisible();
    await expect(wsMenu).toHaveCSS("width", "320px");
    await expect(wsMenu.getByText("Active Workspace")).toBeVisible();
    await expect(wsMenu.getByText("Vanguard Systems")).toBeVisible();
    await expect(wsMenu.getByText("Acme Consulting")).not.toBeAttached();
    await expect(wsMenu.getByText("Apex Labs (Canary)")).not.toBeAttached();
  });

  // 4. Figma 335:7565 — Empty State (real [] from /api/v1/projects)
  test("Figma 335:7565: Empty State opens real Create Project modal for admin and disables CTA for developer", async ({
    page,
  }) => {
    await mockAuthenticatedSession(page, {
      roles: ["TENANT_ADMIN"],
      projects: [],
    });

    await page.setViewportSize({ width: 1280, height: 722 });
    await page.goto("/");

    const emptySection = page.getByTestId("overview-empty-state");
    await expect(emptySection).toBeVisible();
    await expect(
      emptySection.getByRole("heading", { level: 1, name: "No projects yet" }),
    ).toBeVisible();

    const createBtn = page.getByTestId("create-project-button");
    await expect(createBtn).toBeEnabled();
    await createBtn.click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Create New Project" }),
    ).toBeVisible();
    await page.keyboard.press("Escape");

    const importLink = emptySection.getByRole("link", {
      name: "or import existing repositories",
    });
    await expect(importLink).toHaveAttribute("href", "/integrations");

    // Verify Developer sees + Create Project disabled (not deleted)
    await mockAuthenticatedSession(page, {
      roles: ["DEVELOPER"],
      email: "dev@company.com",
      projects: [],
    });
    await page.goto("/");
    const disabledCreateBtn = page.getByTestId("create-project-button");
    await expect(disabledCreateBtn).toBeVisible();
    await expect(disabledCreateBtn).toBeDisabled();
    await expect(disabledCreateBtn).toHaveAttribute("aria-disabled", "true");
  });

  // 5. Figma 336:8004 & 336:8559 — Error State & Recovery
  test("Figma 336:8004 & 336:8559: Error State renders real error, Retry recovers when backend responds 200", async ({
    page,
  }) => {
    await mockAuthenticatedSession(page, { roles: ["TENANT_ADMIN"] });

    let shouldFail = true;
    await page.route(
      (url) => url.pathname === "/api/v1/projects",
      async (route) => {
        if (route.request().method() === "OPTIONS") {
          await fulfillCorsJson(route, 204);
          return;
        }
        if (shouldFail) {
          await fulfillCorsJson(route, 500, { detail: "Backend database unreachable" });
          return;
        }
        await fulfillCorsJson(route, 200, [SAMPLE_PROJECT]);
      },
    );

    await page.setViewportSize({ width: 1280, height: 722 });
    await page.goto("/");

    const errorSection = page.getByTestId("overview-error-state");
    await expect(errorSection).toBeVisible({ timeout: 10000 });
    await expect(
      errorSection.getByRole("heading", { level: 1, name: "Unable to load overview" }),
    ).toBeVisible();

    const errorDetailsBtn = page.getByTestId("error-details-button");
    await errorDetailsBtn.hover();
    await expect(page.getByTestId("error-details-tooltip")).toContainText(
      "Backend database unreachable",
    );

    shouldFail = false;
    await page.getByTestId("error-retry-button").click();
    await expect(page.getByTestId("overview-default-state")).toBeVisible();
  });

  // 6. Figma 337:11533 — Loading / Skeleton State
  test("Figma 337:11533: Skeleton Loading State renders while /api/v1/projects is in flight", async ({
    page,
  }) => {
    await mockAuthenticatedSession(page, { roles: ["TENANT_ADMIN"] });

    let releaseProjects!: () => void;
    const gate = new Promise<void>((resolve) => {
      releaseProjects = resolve;
    });

    await page.route(
      (url) => url.pathname === "/api/v1/projects",
      async (route) => {
        if (route.request().method() === "OPTIONS") {
          await fulfillCorsJson(route, 204);
          return;
        }
        await gate;
        await fulfillCorsJson(route, 200, [SAMPLE_PROJECT]);
      },
    );

    await page.setViewportSize({ width: 1280, height: 722 });
    await page.goto("/");

    const loadingSection = page.getByTestId("overview-loading-state");
    await expect(loadingSection).toBeVisible();
    await expect(loadingSection).toHaveAttribute("aria-busy", "true");

    releaseProjects();
    await expect(page.getByTestId("overview-default-state")).toBeVisible();
  });

  // 7. Figma 338:12871 & 340:13023 — Tablet Collapsed & Expanded
  test("Figma 338:12871 & 340:13023: Tablet collapses to 72px icon-only sidebar and expands to 240px on logo click", async ({
    page,
  }) => {
    await mockAuthenticatedSession(page, {
      roles: ["TENANT_ADMIN"],
      projects: [SAMPLE_PROJECT],
    });

    await page.setViewportSize({ width: 820, height: 1180 });
    await page.goto("/");

    const sidebar = page.getByTestId("portal-sidebar");
    await expect(sidebar).toBeVisible();
    await expect(page.getByTestId("sidebar-footer")).not.toBeVisible();

    await expect(sidebar).toHaveCSS("width", "72px");
    const header = page.getByTestId("portal-header");
    await expect(header.getByText("Workspace", { exact: true })).toBeVisible();
    await expect(header.getByText("Overview", { exact: true })).toBeVisible();

    const logoToggle = page.getByTestId("sidebar-collapse-toggle");
    await logoToggle.click();

    await expect(sidebar).toHaveCSS("width", "240px");
    await expect(sidebar.getByText("M.A.N.T.A.")).toBeVisible();

    await logoToggle.click();
    await expect(sidebar).toHaveCSS("width", "72px");
  });

  // 8. Accessibility & Responsiveness: 320px Mobile
  test("Accessibility: 320px mobile viewport has zero horizontal scrollbar and accessible drawer", async ({
    page,
  }) => {
    await mockAuthenticatedSession(page, {
      roles: ["TENANT_ADMIN"],
      projects: [SAMPLE_PROJECT],
    });

    await page.setViewportSize({ width: 320, height: 640 });
    await page.goto("/");

    const hasHorizontalOverflow = await page.evaluate(() => {
      return (
        document.documentElement.scrollWidth >
        document.documentElement.clientWidth
      );
    });
    expect(hasHorizontalOverflow).toBe(false);

    const mobileToggle = page.getByTestId("sidebar-mobile-toggle");
    await expect(mobileToggle).toBeVisible();

    await mobileToggle.click();
    const sidebar = page.getByTestId("portal-sidebar");
    await expect(sidebar).toHaveClass(/translate-x-0/);

    await page
      .getByTestId("sidebar-mobile-backdrop")
      .click({ position: { x: 300, y: 100 } });
    await expect(sidebar).toHaveClass(/-translate-x-full/);
  });
});
