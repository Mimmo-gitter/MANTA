import { test, expect } from "@playwright/test";

test.describe("Portal Layout & Navigation (SCRUM-266 & SCRUM-267)", () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      window.sessionStorage.setItem("manta_demo", "true");
    });
  });

  test("renders 216px persistent sidebar and 48px header on the main portal shell", async ({
    page,
  }) => {
    await page.goto("/");

    // Verify Title
    await expect(page).toHaveTitle(/MANTA/);

    // Verify Persistent Sidebar (216px)
    const sidebar = page.getByTestId("portal-sidebar");
    await expect(sidebar).toBeVisible();
    const sidebarBox = await sidebar.boundingBox();
    expect(sidebarBox).not.toBeNull();
    expect(sidebarBox?.width).toBeCloseTo(216, 1);

    // Verify Brand elements in Sidebar
    await expect(sidebar.getByText("M.A.N.T.A.")).toBeVisible();
    await expect(sidebar.getByText("v2.4.0")).toBeVisible();
    await expect(sidebar.getByText("WORKSPACE", { exact: true })).toBeVisible();
    await expect(sidebar.getByText("ENGINEERING", { exact: true })).toBeVisible();
    await expect(sidebar.getByText("ADMINISTRATION", { exact: true })).toBeVisible();
    await expect(page.getByTestId("sidebar-footer")).toBeVisible();

    // Verify Header (48px)
    const header = page.getByTestId("portal-header");
    await expect(header).toBeVisible();
    const headerBox = await header.boundingBox();
    expect(headerBox).not.toBeNull();
    expect(headerBox?.height).toBeCloseTo(48, 1);

    // Verify Header Controls
    await expect(header.getByText("Console")).toBeVisible();
    await expect(page.getByTestId("workspace-selector-trigger")).toBeVisible();
    await expect(page.getByTestId("environment-badge")).toBeVisible();
  });

  test("sidebar highlights active link with aria-current='page'", async ({
    page,
  }) => {
    await page.goto("/");
    const overviewLink = page.getByTestId("nav-link-overview");
    await expect(overviewLink).toBeVisible();
    await expect(overviewLink).toHaveAttribute("aria-current", "page");

    // Click Delivery Analytics
    await page.getByTestId("nav-link-delivery-analytics").click();
    await expect(page).toHaveURL("/delivery-analytics");

    const analyticsLink = page.getByTestId("nav-link-delivery-analytics");
    await expect(analyticsLink).toHaveAttribute("aria-current", "page");
    await expect(overviewLink).not.toHaveAttribute("aria-current", "page");
  });

  test("navigates across all 14 Figma sidebar destinations smoothly", async ({
    page,
  }) => {
    await page.goto("/");

    const destinations = [
      { id: "overview", url: "/", titleText: "Engineering Overview" },
      { id: "projects", url: "/projects", titleText: "Projects" },
      { id: "delivery-analytics", url: "/delivery-analytics", titleText: "Delivery Analytics" },
      { id: "risks-alerts", url: "/risks-alerts", titleText: "Risks & Alerts" },
      { id: "sprint-wip", url: "/sprint-wip", titleText: "Sprint & WIP Monitoring" },
      { id: "scope-creep", url: "/scope-creep", titleText: "Scope Creep" },
      { id: "adr-knowledge-base", url: "/adr-knowledge-base", titleText: "ADR Knowledge Base" },
      { id: "semantic-search", url: "/semantic-search", titleText: "Semantic Search" },
      { id: "refactoring-verification", url: "/refactoring-verification", titleText: "Refactoring & Verification" },
      { id: "workspace-settings", url: "/workspace-settings", titleText: "Workspace Settings" },
      { id: "integrations", url: "/integrations", titleText: "Integrations" },
      { id: "access-roles", url: "/access-roles", titleText: "Access & Roles" },
      { id: "privacy-erasure", url: "/privacy-erasure", titleText: "Privacy & Erasure" },
      { id: "adr-governance", url: "/adr-governance", titleText: "ADR Governance" },
    ];

    for (const dest of destinations) {
      await page.getByTestId(`nav-link-${dest.id}`).click();
      await expect(page).toHaveURL(dest.url);
      if (dest.id !== "overview") {
        await expect(page.getByRole("heading", { level: 1, name: dest.titleText })).toBeVisible();
      }
      await expect(page.getByTestId("portal-sidebar")).toBeVisible();
      await expect(page.getByTestId("portal-header")).toBeVisible();
    }
  });

  test("placeholder pages render back-to-overview action", async ({ page }) => {
    await page.goto("/integrations");
    await expect(page.getByRole("heading", { level: 1, name: "Integrations" })).toBeVisible();

    const backBtn = page.getByTestId("back-to-overview-button");
    await expect(backBtn).toBeVisible();
    await backBtn.click();

    await expect(page).toHaveURL("/");
    await expect(page.getByTestId("overview-default-state")).toBeVisible();
  });

  test("deep link reload (F5) maintains layout and active state", async ({
    page,
  }) => {
    await page.goto("/adr-governance");
    await expect(page.getByRole("heading", { level: 1, name: "ADR Governance" })).toBeVisible();
    await expect(page.getByTestId("nav-link-adr-governance")).toHaveAttribute("aria-current", "page");

    // Reload page
    await page.reload();
    await expect(page).toHaveURL("/adr-governance");
    await expect(page.getByRole("heading", { level: 1, name: "ADR Governance" })).toBeVisible();
    await expect(page.getByTestId("nav-link-adr-governance")).toHaveAttribute("aria-current", "page");
    await expect(page.getByTestId("portal-sidebar")).toBeVisible();
  });

  test("unknown URL shows custom 404 page with return action", async ({ page }) => {
    await page.goto("/unresolved-random-path-404");
    await expect(page.getByText("Page Not Found")).toBeVisible();
    await expect(page.getByText("404 NOT FOUND")).toBeVisible();

    const backHome = page.getByTestId("not-found-back-home");
    await expect(backHome).toBeVisible();
    await backHome.click();

    await expect(page).toHaveURL("/");
    await expect(page.getByTestId("portal-sidebar")).toBeVisible();
  });

  test("login page remains full-screen isolated without sidebar", async ({
    page,
  }) => {
    await page.goto("/login");
    await expect(page.getByTestId("portal-sidebar")).not.toBeVisible();
    await expect(page.getByTestId("portal-header")).not.toBeVisible();
    await expect(page.getByRole("button", { name: /Continue with Company SSO/i })).toBeVisible();
  });

  test("signed-out page remains isolated without sidebar", async ({ page }) => {
    await page.goto("/signed-out");
    await expect(page.getByTestId("portal-sidebar")).not.toBeVisible();
    await expect(page.getByTestId("portal-header")).not.toBeVisible();
    await expect(page.getByTestId("session-terminated-badge")).toBeVisible();
  });

  test("mobile viewport toggles sidebar drawer via hamburger menu", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto("/");

    const hamburger = page.getByTestId("sidebar-mobile-toggle");
    await expect(hamburger).toBeVisible();

    // Initially sidebar is off-screen on mobile
    const sidebar = page.getByTestId("portal-sidebar");
    await expect(sidebar).toHaveClass(/-translate-x-full/);

    // Open drawer
    await hamburger.click();
    await expect(sidebar).toHaveClass(/translate-x-0/);
    await expect(page.getByTestId("sidebar-mobile-backdrop")).toBeVisible();

    // Close via backdrop click
    await page.getByTestId("sidebar-mobile-backdrop").click({ position: { x: 300, y: 100 } });
    await expect(sidebar).toHaveClass(/-translate-x-full/);
  });
});
