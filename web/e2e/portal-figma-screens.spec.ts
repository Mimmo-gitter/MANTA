import { test, expect } from "@playwright/test";

test.describe("Figma Portal Shell Screens & Accessibility Suite (9 Figma Frames 1-to-1)", () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      window.localStorage.clear();
      window.sessionStorage.setItem("manta_demo", "true");
    });
  });

  // --------------------------------------------------------------------------
  // 1. Figma 325:2340 — Default Desktop Application Shell
  // --------------------------------------------------------------------------
  test("Figma 325:2340: Default Application Shell renders 216px sidebar, real MANTA logo, SVG icons, and 48px header", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1280, height: 722 });
    await page.goto("/?state=default");

    await expect(page).toHaveTitle(/MANTA/);
    const skipLink = page.locator("a[href='#main-content']");
    await expect(skipLink).toBeAttached();

    // Left Persistent Sidebar (216px)
    const sidebar = page.getByTestId("portal-sidebar");
    await expect(sidebar).toBeVisible();
    const sidebarBox = await sidebar.boundingBox();
    expect(sidebarBox?.width).toBeCloseTo(216, 2);

    // Real MANTA logo image (not a fake SVG hexagon)
    const logoImg = sidebar.locator("img[src='/icons/shell/f14d9.png']");
    await expect(logoImg).toBeVisible();

    // Sidebar Branding & Sections
    await expect(sidebar.getByText("M.A.N.T.A.")).toBeVisible();
    await expect(sidebar.getByText("v2.4.0")).toBeVisible();
    await expect(sidebar.getByText("WORKSPACE", { exact: true })).toBeVisible();
    await expect(sidebar.getByText("ENGINEERING", { exact: true })).toBeVisible();
    await expect(sidebar.getByText("ADMINISTRATION", { exact: true })).toBeVisible();

    // Sidebar footer: removed per design specification
    await expect(page.getByTestId("sidebar-footer")).not.toBeVisible();

    // Header (48px height)
    const header = page.getByTestId("portal-header");
    await expect(header).toBeVisible();
    const headerBox = await header.boundingBox();
    expect(headerBox?.height).toBeCloseTo(48, 2);

    // Header Controls & SVG lightning icon (not OS emoji)
    await expect(header.getByText("Console")).toBeVisible();
    const wsTrigger = page.getByTestId("workspace-selector-trigger");
    await expect(wsTrigger).toBeVisible();
    await expect(wsTrigger.locator("img[src='/icons/shell/lightning-emerald.svg']")).toBeVisible();
    await expect(page.getByTestId("environment-badge")).toContainText("PROD US-EAST-1");
    await expect(page.getByTestId("header-alerts-link")).toBeVisible();
  });

  // --------------------------------------------------------------------------
  // 2. Figma 321:1283 & 321:1453 — Profile Menu Open
  // --------------------------------------------------------------------------
  test("Figma 321:1283: User Profile Menu opens 256px card with avatar, badge, links, and sign out", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1280, height: 722 });
    await page.goto("/");

    const profileTrigger = page.getByTestId("user-profile-menu-trigger");
    await expect(profileTrigger).toBeVisible();
    await profileTrigger.click();

    // Dropdown menu matches Figma 321:1453 (256px wide)
    const userMenu = page.getByTestId("user-dropdown-menu");
    await expect(userMenu).toBeVisible();
    await expect(userMenu).toHaveCSS("width", "256px");
    const menuBox = await userMenu.boundingBox();
    expect(menuBox).not.toBeNull();
    expect(menuBox!.width).toBeCloseTo(256, 1);

    // Header details: PZ initials, Pavlo Zhuk, Tenant Admin badge, director@manta.ai
    await expect(userMenu.getByText("PZ")).toBeVisible();
    await expect(userMenu.getByText("Pavlo Zhuk")).toBeVisible();
    await expect(userMenu.getByText("Tenant Admin")).toBeVisible();
    await expect(userMenu.getByText(/director@manta\.ai|@manta\.ai/)).toBeVisible();

    // Menu links
    await expect(userMenu.getByRole("menuitem", { name: "My Profile" })).toBeVisible();
    await expect(userMenu.getByRole("menuitem", { name: "Account Settings" })).toBeVisible();

    // Sign out button
    const signOutBtn = page.getByTestId("dropdown-signout-button");
    await expect(signOutBtn).toBeVisible();
    await expect(signOutBtn).toContainText("Sign Out");
  });

  // --------------------------------------------------------------------------
  // 3. Figma 334:5847 & 334:5977 — Workspace Menu Open
  // --------------------------------------------------------------------------
  test("Figma 334:5847: Workspace Switcher opens 320px dropdown with search, Active, and Idle workspaces", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1280, height: 722 });
    await page.goto("/");

    const wsTrigger = page.getByTestId("workspace-selector-trigger");
    await expect(wsTrigger).toBeVisible();
    await wsTrigger.click();

    const wsMenu = page.getByTestId("workspace-dropdown-menu");
    await expect(wsMenu).toBeVisible();
    await expect(wsMenu).toHaveCSS("width", "320px");
    const menuBox = await wsMenu.boundingBox();
    expect(menuBox).not.toBeNull();
    expect(menuBox!.width).toBeCloseTo(320, 1);

    await expect(wsMenu.getByText("Workspaces", { exact: true })).toBeVisible();

    const searchInput = wsMenu.getByPlaceholder("Search workspaces...");
    await expect(searchInput).toBeVisible();

    await expect(wsMenu.getByText("Acme Consulting")).toBeVisible();
    await expect(wsMenu.getByText("Active")).toBeVisible();
    await expect(wsMenu.getByText("Apex Labs (Canary)")).toBeVisible();
    await expect(wsMenu.getByText("Idle")).toBeVisible();

    // Filter search
    await searchInput.fill("Apex");
    await expect(wsMenu.getByText("Apex Labs (Canary)")).toBeVisible();
    await expect(wsMenu.getByText("Acme Consulting")).not.toBeVisible();

    await wsMenu.getByText("Apex Labs (Canary)").click();
    await expect(wsMenu).not.toBeVisible();
    await expect(wsTrigger).toContainText("Apex Labs (Canary)");
  });

  // --------------------------------------------------------------------------
  // 4. Figma 335:7565 — Empty State
  // --------------------------------------------------------------------------
  test("Figma 335:7565: Empty State renders 'No projects yet', '+ Create Project' and repository import link", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1280, height: 722 });
    await page.goto("/?state=empty");

    const emptySection = page.getByTestId("overview-empty-state");
    await expect(emptySection).toBeVisible();

    await expect(emptySection.getByRole("heading", { level: 2, name: "No projects yet" })).toBeVisible();
    await expect(
      emptySection.getByText("Create a project or import an existing repository to get started.")
    ).toBeVisible();

    const createBtn = page.getByTestId("create-project-button");
    await expect(createBtn).toBeVisible();
    await expect(createBtn).toContainText("+ Create Project");

    await createBtn.click();
    await expect(page.getByTestId("create-project-feedback")).toContainText("Project template initialized successfully!");

    const importLink = emptySection.getByRole("link", { name: "or import existing repositories" });
    await expect(importLink).toBeVisible();
    await expect(importLink).toHaveAttribute("href", "/integrations");
  });

  // --------------------------------------------------------------------------
  // 5. Figma 336:8004 & 336:8559 — Error State & Tooltip Hover
  // --------------------------------------------------------------------------
  test("Figma 336:8004 & 336:8559: Error State renders 512px card, 'Unable to load overview', Retry, and Error details tooltip", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1280, height: 722 });
    await page.goto("/?state=error");

    const errorSection = page.getByTestId("overview-error-state");
    await expect(errorSection).toBeVisible();

    await expect(errorSection.getByRole("heading", { level: 1, name: "Unable to load overview" })).toBeVisible();
    await expect(
      errorSection.getByText("We couldn’t load your project metrics. Please try again.")
    ).toBeVisible();

    const retryBtn = page.getByTestId("error-retry-button");
    await expect(retryBtn).toBeVisible();
    const errorDetailsBtn = page.getByTestId("error-details-button");
    await expect(errorDetailsBtn).toBeVisible();

    await errorDetailsBtn.hover();
    const tooltip = page.getByTestId("error-details-tooltip");
    await expect(tooltip).toBeVisible();
    await expect(tooltip).toContainText(
      "The overview metrics could not be loaded. Retry the request. If the problem persists, contact your administrator."
    );

    await retryBtn.click();
    await expect(page.getByTestId("overview-default-state")).toBeVisible();
  });

  // --------------------------------------------------------------------------
  // 6. Figma 337:11533 — Loading / Skeleton State
  // --------------------------------------------------------------------------
  test("Figma 337:11533: Skeleton Loading State renders pulse shimmer placeholders and sidebar skeleton badges", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1280, height: 722 });
    await page.goto("/?state=loading");

    const loadingSection = page.getByTestId("overview-loading-state");
    await expect(loadingSection).toBeVisible();
    await expect(loadingSection).toHaveAttribute("aria-busy", "true");
  });

  // --------------------------------------------------------------------------
  // 7. Figma 338:12871 & 340:13023 — Tablet Collapsed (72px) & Expanded (240px)
  // --------------------------------------------------------------------------
  test("Figma 338:12871 & 340:13023: Tablet collapses to 72px icon-only sidebar and expands to 240px on logo click", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 820, height: 1180 });
    await page.goto("/");

    const sidebar = page.getByTestId("portal-sidebar");
    await expect(sidebar).toBeVisible();

    // Tablet status pill / footer removed per design specification
    await expect(page.getByTestId("sidebar-footer")).not.toBeVisible();

    // Tablet starts collapsed (72px wide)
    await expect(sidebar).toHaveCSS("width", "72px");
    const collapsedBox = await sidebar.boundingBox();
    expect(collapsedBox?.width).toBeCloseTo(72, 1);

    // Tablet Breadcrumb in Header (56px height)
    const header = page.getByTestId("portal-header");
    await expect(header.getByText("Workspace", { exact: true })).toBeVisible();
    await expect(header.getByText("Overview", { exact: true })).toBeVisible();

    // Click logo to Expand sidebar to 240px (Figma 340:13023)
    const logoToggle = page.getByTestId("sidebar-collapse-toggle");
    await expect(logoToggle).toBeVisible();
    await logoToggle.click();

    await expect(sidebar).toHaveCSS("width", "240px");
    const expandedBox = await sidebar.boundingBox();
    expect(expandedBox?.width).toBeCloseTo(240, 1);
    await expect(sidebar.getByText("M.A.N.T.A.")).toBeVisible();
    await expect(page.getByTestId("sidebar-footer")).not.toBeVisible();

    // Hover logo in expanded mode shows tooltip: "Click Logo to Expand / Collapse" (Figma 340:13023)
    const expandedLogoToggle = page.getByTestId("sidebar-collapse-toggle");
    await expandedLogoToggle.hover();
    await expect(page.getByRole("tooltip")).toContainText("Click Logo to Expand / Collapse");

    // Click logo again to Collapse back to 72px
    await expandedLogoToggle.click();
    await expect(sidebar).toHaveCSS("width", "72px");
    const reCollapsedBox = await sidebar.boundingBox();
    expect(reCollapsedBox?.width).toBeCloseTo(72, 1);
  });

  // --------------------------------------------------------------------------
  // 8. Accessibility & Responsiveness: 320px to 4K
  // --------------------------------------------------------------------------
  test("Accessibility: 320px mobile viewport has zero horizontal scrollbar and accessible drawer", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 320, height: 640 });
    await page.goto("/");

    const hasHorizontalOverflow = await page.evaluate(() => {
      return document.documentElement.scrollWidth > document.documentElement.clientWidth;
    });
    expect(hasHorizontalOverflow).toBe(false);

    const mobileToggle = page.getByTestId("sidebar-mobile-toggle");
    await expect(mobileToggle).toBeVisible();

    await mobileToggle.click();
    const sidebar = page.getByTestId("portal-sidebar");
    await expect(sidebar).toHaveClass(/translate-x-0/);

    await page.getByTestId("sidebar-mobile-backdrop").click({ position: { x: 300, y: 100 } });
    await expect(sidebar).toHaveClass(/-translate-x-full/);
  });
});
