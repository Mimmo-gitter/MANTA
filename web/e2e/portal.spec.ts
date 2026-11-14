import { test, expect } from "@playwright/test";
import { fulfillCorsJson, mockAuthenticatedSession } from "./helpers/mock-auth";

const ALL_SIDEBAR_DESTINATIONS = [
  { id: "overview", url: "/", title: "Engineering Overview" },
  { id: "projects", url: "/projects", title: "Projects" },
  {
    id: "delivery-analytics",
    url: "/delivery-analytics",
    title: "Delivery Analytics",
  },
  { id: "risks-alerts", url: "/risks-alerts", title: "Risks & Alerts" },
  { id: "sprint-wip", url: "/sprint-wip", title: "Sprint & WIP Monitoring" },
  { id: "scope-creep", url: "/scope-creep", title: "Scope Creep" },
  {
    id: "adr-knowledge-base",
    url: "/adr-knowledge-base",
    title: "ADR Knowledge Base",
  },
  { id: "semantic-search", url: "/semantic-search", title: "Semantic Search" },
  {
    id: "refactoring-verification",
    url: "/refactoring-verification",
    title: "Refactoring & Verification",
  },
  {
    id: "workspace-settings",
    url: "/workspace-settings",
    title: "Workspace Settings",
  },
  { id: "integrations", url: "/integrations", title: "Integrations" },
  { id: "access-roles", url: "/access-roles", title: "Members & Access" },
  {
    id: "privacy-erasure",
    url: "/privacy-erasure",
    title: "Privacy & Erasure",
  },
  { id: "adr-governance", url: "/adr-governance", title: "ADR Governance" },
];

const ADMIN_ROUTE_IDS = [
  "workspace-settings",
  "integrations",
  "access-roles",
  "privacy-erasure",
  "adr-governance",
];

const SAMPLE_PROJECTS = [
  {
    id: "11111111-1111-1111-1111-111111111101",
    name: "Core Platform API",
    slug: "core-platform-api",
    tenant_id: "11111111-1111-1111-1111-111111111111",
    created_at: "2026-11-10T10:00:00Z",
    repositories: [],
  },
];

test.describe("Portal Shell & Navigation (US-3 / SCRUM-270)", () => {
  test.describe("Admin User Navigation & Layout", () => {
    test.beforeEach(async ({ page }) => {
      await mockAuthenticatedSession(page, {
        roles: ["TENANT_ADMIN"],
        projects: SAMPLE_PROJECTS,
      });

      await page.route(
        (url) => url.pathname === "/api/v1/members",
        async (route) => {
          await fulfillCorsJson(route, 200, [
            { id: "m1", email: "p.zhuk@company.com", role: "TENANT_ADMIN" },
          ]);
        },
      );
    });

    test("1. Renders portal shell with persistent 216px sidebar and 48px header on desktop", async ({
      page,
    }) => {
      await page.setViewportSize({ width: 1280, height: 800 });
      await page.goto("/");

      await expect(page).toHaveTitle(/MANTA/);

      const sidebar = page.getByTestId("portal-sidebar");
      await expect(sidebar).toBeVisible();
      const sidebarBox = await sidebar.boundingBox();
      expect(sidebarBox).not.toBeNull();
      expect(sidebarBox?.width).toBeCloseTo(216, 2);

      await expect(sidebar.getByText("M.A.N.T.A.")).toBeVisible();
      await expect(sidebar.getByText("v2.4.0")).toBeVisible();
      await expect(sidebar.getByText("WORKSPACE", { exact: true })).toBeVisible();
      await expect(sidebar.getByText("ENGINEERING", { exact: true })).toBeVisible();
      await expect(sidebar.getByText("ADMINISTRATION", { exact: true })).toBeVisible();

      // Dynamic projects count badge from /api/v1/projects
      await expect(page.getByTestId("nav-link-projects")).toContainText("1");

      const header = page.getByTestId("portal-header");
      await expect(header).toBeVisible();
      const headerBox = await header.boundingBox();
      expect(headerBox).not.toBeNull();
      expect(headerBox?.height).toBeCloseTo(48, 2);
      await expect(header.getByText("Console")).toBeVisible();
      await expect(page.getByTestId("environment-badge")).toContainText(
        "PRODUCTION",
      );
    });

    test("2. Navigates between all 14 sidebar entries and updates active state", async ({
      page,
    }) => {
      await page.setViewportSize({ width: 1280, height: 800 });
      await page.goto("/");

      for (const dest of ALL_SIDEBAR_DESTINATIONS) {
        const link = page.getByTestId(`nav-link-${dest.id}`);
        await expect(link).toBeVisible();
        await link.click();

        await expect(page).toHaveURL(dest.url);
        await expect(link).toHaveAttribute("aria-current", "page");

        await expect(
          page.getByRole("heading", { level: 1, name: dest.title }),
        ).toBeVisible();

        if (dest.id === "overview") {
          await expect(page.getByTestId("overview-default-state")).toBeVisible();
        } else if (dest.id === "projects") {
          await expect(page.getByTestId("projects-summary-strip")).toBeVisible();
        } else if (dest.id === "integrations") {
          await expect(page.getByTestId("integrations-state-bar")).not.toBeAttached();
        } else if (dest.id === "access-roles") {
          await expect(page.getByTestId("members-kpi-strip")).toBeVisible();
        } else {
          await expect(page.getByTestId("placeholder-page")).toBeVisible();
        }

        await expect(page.getByTestId("portal-sidebar")).toBeVisible();
        await expect(page.getByTestId("portal-header")).toBeVisible();
      }
    });

    test("3. Navigates between sidebar entries and custom 404 not-found page with recovery", async ({
      page,
    }) => {
      await page.setViewportSize({ width: 1280, height: 800 });
      await page.goto("/projects");
      await expect(page).toHaveURL("/projects");
      await expect(
        page.getByRole("heading", { level: 1, name: "Projects" }),
      ).toBeVisible();

      await page.goto("/broken-route-404-simulation");
      await expect(page.getByText("404 NOT FOUND")).toBeVisible();
      await expect(
        page.getByRole("heading", { level: 1, name: "Page Not Found" }),
      ).toBeVisible();
      await expect(
        page.getByText(
          "The route you navigated to does not exist in this workspace",
        ),
      ).toBeVisible();

      const backHomeBtn = page.getByTestId("not-found-back-home");
      await expect(backHomeBtn).toBeVisible();
      await backHomeBtn.click();

      await expect(page).toHaveURL("/");
      await expect(page.getByTestId("nav-link-overview")).toHaveAttribute(
        "aria-current",
        "page",
      );
      await expect(page.getByTestId("portal-sidebar")).toBeVisible();
      await expect(page.getByTestId("portal-header")).toBeVisible();
    });

    test("4. Layout check: Desktop viewport (1280x800) has 216px persistent sidebar and zero overflow", async ({
      page,
    }) => {
      await page.setViewportSize({ width: 1280, height: 800 });
      await page.goto("/");

      const sidebar = page.getByTestId("portal-sidebar");
      await expect(sidebar).toBeVisible();
      const sidebarBox = await sidebar.boundingBox();
      expect(sidebarBox?.width).toBeCloseTo(216, 2);

      for (const section of ["WORKSPACE", "ENGINEERING", "ADMINISTRATION"]) {
        await expect(sidebar.getByText(section, { exact: true })).toBeVisible();
      }

      const hasHorizontalOverflow = await page.evaluate(() => {
        return (
          document.documentElement.scrollWidth >
          document.documentElement.clientWidth
        );
      });
      expect(hasHorizontalOverflow).toBe(false);
    });

    test("5. Layout check: Tablet viewport (820x1180) defaults to 72px collapsed and toggles to 240px expanded", async ({
      page,
    }) => {
      await page.setViewportSize({ width: 820, height: 1180 });
      await page.goto("/");

      const sidebar = page.getByTestId("portal-sidebar");
      await expect(sidebar).toBeVisible();

      await expect(sidebar).toHaveCSS("width", "72px");
      const collapsedBox = await sidebar.boundingBox();
      expect(collapsedBox?.width).toBeCloseTo(72, 1);

      await expect(sidebar.getByText("WORKSPACE", { exact: true })).not.toBeVisible();
      await expect(sidebar.getByText("ENGINEERING", { exact: true })).not.toBeVisible();
      await expect(sidebar.getByText("ADMINISTRATION", { exact: true })).not.toBeVisible();

      const header = page.getByTestId("portal-header");
      await expect(header.getByText("Workspace", { exact: true })).toBeVisible();
      await expect(header.getByText("Overview", { exact: true })).toBeVisible();

      const toggleBtn = page.getByTestId("sidebar-collapse-toggle");
      await expect(toggleBtn).toBeVisible();
      await toggleBtn.click();

      await expect(sidebar).toHaveCSS("width", "240px");
      const expandedBox = await sidebar.boundingBox();
      expect(expandedBox?.width).toBeCloseTo(240, 1);

      await expect(sidebar.getByText("WORKSPACE", { exact: true })).toBeVisible();
      await expect(sidebar.getByText("ENGINEERING", { exact: true })).toBeVisible();
      await expect(sidebar.getByText("ADMINISTRATION", { exact: true })).toBeVisible();
      await expect(sidebar.getByText("Sprint & WIP", { exact: true })).toBeVisible();

      await toggleBtn.click();
      await expect(sidebar).toHaveCSS("width", "72px");
      const reCollapsedBox = await sidebar.boundingBox();
      expect(reCollapsedBox?.width).toBeCloseTo(72, 1);

      await page.getByTestId("nav-link-delivery-analytics").click();
      await expect(page).toHaveURL("/delivery-analytics");
      await expect(
        header.getByText("Delivery Analytics", { exact: true }),
      ).toBeVisible();
    });
  });

  test.describe("Non-Admin User Role Filtering & Route Protection", () => {
    test.beforeEach(async ({ page }) => {
      await mockAuthenticatedSession(page, {
        roles: ["DEVELOPER"],
        name: "Dev Member",
        email: "dev@company.com",
      });
    });

    test("6. Hides administration section and all admin links for non-admin on desktop", async ({
      page,
    }) => {
      await page.setViewportSize({ width: 1280, height: 800 });
      await page.goto("/");

      const sidebar = page.getByTestId("portal-sidebar");
      await expect(sidebar).toBeVisible();

      await expect(
        sidebar.getByText("ADMINISTRATION", { exact: true }),
      ).not.toBeVisible();

      for (const adminId of ADMIN_ROUTE_IDS) {
        await expect(page.getByTestId(`nav-link-${adminId}`)).not.toBeVisible();
      }

      await expect(sidebar.getByText("WORKSPACE", { exact: true })).toBeVisible();
      await expect(sidebar.getByText("ENGINEERING", { exact: true })).toBeVisible();
      await expect(page.getByTestId("nav-link-overview")).toBeVisible();
      await expect(page.getByTestId("nav-link-projects")).toBeVisible();
      await expect(page.getByTestId("nav-link-sprint-wip")).toBeVisible();
    });

    test("7. Hides administration entries for non-admin on tablet viewport (both collapsed and expanded)", async ({
      page,
    }) => {
      await page.setViewportSize({ width: 820, height: 1180 });
      await page.goto("/");

      const sidebar = page.getByTestId("portal-sidebar");
      await expect(sidebar).toBeVisible();
      await expect(sidebar).toHaveCSS("width", "72px");

      for (const adminId of ADMIN_ROUTE_IDS) {
        await expect(page.getByTestId(`nav-link-${adminId}`)).not.toBeVisible();
      }

      const toggleBtn = page.getByTestId("sidebar-collapse-toggle");
      await toggleBtn.click();
      await expect(sidebar).toHaveCSS("width", "240px");

      await expect(
        sidebar.getByText("ADMINISTRATION", { exact: true }),
      ).not.toBeVisible();
      for (const adminId of ADMIN_ROUTE_IDS) {
        await expect(page.getByTestId(`nav-link-${adminId}`)).not.toBeVisible();
      }
    });

    test("8. Enforces route protection: Direct navigation to any restricted route redirects to /", async ({
      page,
    }) => {
      const restrictedRoutes = [
        "/workspace-settings",
        "/integrations",
        "/access-roles",
        "/privacy-erasure",
        "/adr-governance",
      ];

      for (const route of restrictedRoutes) {
        await page.goto(route);
        await expect(page).toHaveURL("/");
        await expect(page.getByTestId("portal-sidebar")).toBeVisible();
      }
    });
  });
});
