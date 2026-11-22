import { expect, test, type Page } from "@playwright/test";
import {
  fulfillCorsJson,
  makeMockProject,
  mockAuthenticatedSession,
} from "./helpers/mock-auth";
import type { FlowMetricsResponse, ProjectSprint } from "../src/types/sprint-wip";

const TEST_PROJECT_ID = "11111111-1111-1111-1111-000000000001";
const SPRINT_42_ID = "sprint-uuid-42";
const SPRINT_41_ID = "sprint-uuid-41";

const mockSprint42: ProjectSprint = {
  id: SPRINT_42_ID,
  external_sprint_id: "jira-sprint-42",
  name: "Sprint 42 (Manta Delivery)",
  state: "active",
  start_date: "2026-11-01T00:00:00Z",
  end_date: "2026-11-10T23:59:59Z",
  complete_date: null,
  goal: "Deliver payment flows, burndown trajectory, and WIP limits",
};

const mockSprint41: ProjectSprint = {
  id: SPRINT_41_ID,
  external_sprint_id: "jira-sprint-41",
  name: "Sprint 41 (Alpha Foundation)",
  state: "closed",
  start_date: "2026-10-20T00:00:00Z",
  end_date: "2026-10-31T23:59:59Z",
  complete_date: "2026-10-31T18:00:00Z",
  goal: "Foundation architecture and CI pipeline",
};

/**
 * 10-issue / 40-SP burndown fixture (US-76 / Task 76.5 & Task 76.6):
 * - Sprint span: 10 days (2026-11-01 to 2026-11-10)
 * - 40 SP committed
 * - Day 1 & 2: 40 SP remaining
 * - Day 3: 8 SP completed -> 32 SP remaining
 * - Day 4 & 5: 32 SP remaining
 * - Day 6: 12 SP completed -> 20 SP remaining
 * - Days 7-10: future days (remaining_sp: null)
 * - Ideal burndown line descends linearly from 40.0 SP to 0.0 SP
 * - WIP: 4 issues in 'In Progress' with limit 3 (over limit)
 * - WIP by Developer: Developer 3A1F9C8B7E with 3 items against limit 2 (over limit)
 */
const mockFlowMetricsSprint42: FlowMetricsResponse = {
  sprint: {
    id: SPRINT_42_ID,
    name: "Sprint 42 (Manta Delivery)",
    state: "active",
  },
  burndown: [
    { date: "2026-11-01", remaining_sp: 40.0, ideal_sp: 40.0 },
    { date: "2026-11-02", remaining_sp: 40.0, ideal_sp: 35.56 },
    { date: "2026-11-03", remaining_sp: 32.0, ideal_sp: 31.11 }, // 8 SP done
    { date: "2026-11-04", remaining_sp: 32.0, ideal_sp: 26.67 },
    { date: "2026-11-05", remaining_sp: 32.0, ideal_sp: 22.22 },
    { date: "2026-11-06", remaining_sp: 20.0, ideal_sp: 17.78 }, // 12 SP done
    { date: "2026-11-07", remaining_sp: null, ideal_sp: 13.33 },
    { date: "2026-11-08", remaining_sp: null, ideal_sp: 8.89 },
    { date: "2026-11-09", remaining_sp: null, ideal_sp: 4.44 },
    { date: "2026-11-10", remaining_sp: null, ideal_sp: 0.0 },
  ],
  wip: [
    {
      status: "In Progress",
      count: 4,
      limit: 3,
      over_limit: true,
    },
    {
      status: "To Do",
      count: 2,
      limit: 6,
      over_limit: false,
    },
    {
      status: "Code Review",
      count: 1,
      limit: 2,
      over_limit: false,
    },
    {
      status: "Done",
      count: 3,
      limit: 6,
      over_limit: false,
    },
  ],
  wip_by_developer: {
    limit_per_developer: 2,
    developers: [
      {
        developer_label: "Developer 3A1F9C8B7E",
        count: 3,
        limit: 2,
        over_limit: true,
      },
      {
        developer_label: "Developer 7E4B2C1A9D",
        count: 1,
        limit: 2,
        over_limit: false,
      },
    ],
    unassigned_count: 0,
  },
};

const mockFlowMetricsSprint41: FlowMetricsResponse = {
  sprint: {
    id: SPRINT_41_ID,
    name: "Sprint 41 (Alpha Foundation)",
    state: "closed",
  },
  burndown: [
    { date: "2026-10-20", remaining_sp: 30.0, ideal_sp: 30.0 },
    { date: "2026-10-25", remaining_sp: 15.0, ideal_sp: 15.0 },
    { date: "2026-10-31", remaining_sp: 0.0, ideal_sp: 0.0 },
  ],
  wip: [
    {
      status: "Done",
      count: 8,
      limit: 10,
      over_limit: false,
    },
  ],
  wip_by_developer: {
    limit_per_developer: 2,
    developers: [],
    unassigned_count: 0,
  },
};

async function setupSprintWipMocks(
  page: Page,
  options: {
    sprints?: ProjectSprint[];
    flowMetricsBySprint?: Record<string, FlowMetricsResponse>;
    defaultFlowMetrics?: FlowMetricsResponse | null;
  } = {},
) {
  const sprints = options.sprints ?? [mockSprint42, mockSprint41];
  const metricsMap = options.flowMetricsBySprint ?? {
    [SPRINT_42_ID]: mockFlowMetricsSprint42,
    [SPRINT_41_ID]: mockFlowMetricsSprint41,
  };
  const defaultMetrics =
    options.defaultFlowMetrics !== undefined
      ? options.defaultFlowMetrics
      : mockFlowMetricsSprint42;

  await page.route("**/api/v1/projects/*/sprints*", async (route) => {
    if (route.request().method() === "OPTIONS") {
      await fulfillCorsJson(route, 204);
      return;
    }
    await fulfillCorsJson(route, 200, sprints);
  });

  await page.route("**/api/v1/projects/*/flow-metrics*", async (route) => {
    if (route.request().method() === "OPTIONS") {
      await fulfillCorsJson(route, 204);
      return;
    }
    const url = new URL(route.request().url());
    const sprintId = url.searchParams.get("sprint_id");

    if (sprintId && metricsMap[sprintId]) {
      await fulfillCorsJson(route, 200, metricsMap[sprintId]);
      return;
    }

    if (defaultMetrics) {
      await fulfillCorsJson(route, 200, defaultMetrics);
      return;
    }

    await fulfillCorsJson(route, 200, {
      sprint: null,
      burndown: [],
      wip: [],
      wip_by_developer: {
        limit_per_developer: 2,
        developers: [],
        unassigned_count: 0,
      },
    });
  });
}

test.describe("Sprint Burndown & WIP Flow Metrics E2E Verification (US-76 / SCRUM-473)", () => {
  test.beforeEach(async ({ page }) => {
    page.on("pageerror", (err) => console.log("[PAGE_ERROR]", err.message));
    page.on("console", (msg) => {
      if (msg.type() === "error") {
        console.log("[CONSOLE_ERROR]", msg.text());
      }
    });
  });

  test("TC-BURNDOWN-SERIES-001: Authenticate as alpha_scrum and verify 40 -> 32 -> 20 SP burndown trajectory against ideal line", async ({
    page,
  }) => {
    const project = makeMockProject({
      id: TEST_PROJECT_ID,
      name: "Core Payments",
      slug: "core-payments",
      wip_limit_per_dev: 2,
    });

    await mockAuthenticatedSession(page, {
      preferredUsername: "alpha_scrum",
      name: "Alpha Scrum Master",
      email: "alpha_scrum@manta.local",
      roles: ["SCRUM_MASTER"],
      projects: [project],
    });
    await setupSprintWipMocks(page);

    await page.goto("/sprint-wip", { waitUntil: "domcontentloaded" });

    // 1. Verify main container and page header
    const pageContainer = page.getByTestId("sprint-wip-page");
    await expect(pageContainer).toBeVisible({ timeout: 15000 });
    await expect(
      page.getByRole("heading", { name: "Sprint & WIP Monitoring" }),
    ).toBeVisible();

    // 2. Verify selected project and active sprint metadata
    await expect(
      page.getByRole("button", { name: /Core Payments/ }),
    ).toBeVisible();
    await expect(page.getByText("Sprint 42 (Manta Delivery)")).toBeVisible();
    await expect(page.getByText("Nov 1, 2026 — Nov 10, 2026")).toBeVisible();
    await expect(
      page.getByText("Day 6 of 10 (60% elapsed)"),
    ).toBeVisible();
    await expect(page.getByText("ACTIVE", { exact: true })).toBeVisible();
    await expect(page.getByText("BEHIND", { exact: true })).toBeVisible();

    // 3. Verify Sprint Burndown header, 40 SP total badge, and target velocity
    await expect(
      page.getByRole("heading", { name: "Sprint Burndown" }),
    ).toBeVisible();
    await expect(page.getByText("40 SP TOTAL")).toBeVisible();
    await expect(
      page.getByText("Target Velocity: 4.4 SP / day"),
    ).toBeVisible();
    await expect(
      page.getByText("Actual Remaining Story Points", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText("Ideal Burndown", { exact: true }),
    ).toBeVisible();

    // 4. Verify Recharts burndown chart container and SVG paths
    const burndownChart = page.locator(
      'div[role="img"][aria-label="Sprint burndown chart comparing actual and ideal remaining Story Points"]',
    );
    await expect(burndownChart).toBeVisible();
    const rechartsSurface = burndownChart.locator("svg.recharts-surface");
    await expect(rechartsSurface).toBeVisible();
    const lines = burndownChart.locator(".recharts-line");
    await expect(lines).toHaveCount(2); // Ideal line and Actual series

    // 5. Verify Backlog Delta variance (+2.2 SP) and sprint status recommendation
    await expect(page.getByText("+2.2 SP", { exact: true })).toBeVisible();
    await expect(
      page.getByText("Current burndown variance is +2.2 SP."),
    ).toBeVisible();
  });

  test("TC-WIP-CONGESTION-002: Verify 4-issue 'In Progress' WIP congestion rendering, limit badge, and bottleneck diagnostics", async ({
    page,
  }) => {
    const project = makeMockProject({
      id: TEST_PROJECT_ID,
      name: "Core Payments",
      slug: "core-payments",
      wip_limit_per_dev: 2,
    });

    await mockAuthenticatedSession(page, {
      preferredUsername: "alpha_scrum",
      name: "Alpha Scrum Master",
      email: "alpha_scrum@manta.local",
      roles: ["SCRUM_MASTER"],
      projects: [project],
    });
    await setupSprintWipMocks(page);

    await page.goto("/sprint-wip", { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("sprint-wip-page")).toBeVisible({
      timeout: 15000,
    });

    // 1. Verify WIP Congestion section heading and overall status badge
    const wipSection = page
      .locator("section")
      .filter({ hasText: "WIP Congestion" });
    await expect(wipSection).toBeVisible();
    await expect(
      wipSection.getByRole("heading", { name: "WIP Congestion" }),
    ).toBeVisible();
    await expect(wipSection.getByText(/LIMIT\s*EXCEEDED/)).toBeVisible();

    // 2. Verify 4-issue 'In Progress' stage exceeds limit 3
    const inProgressRow = wipSection
      .locator("div")
      .filter({ hasText: "In Progress" })
      .filter({ hasText: "4 / 3" })
      .first();
    await expect(inProgressRow).toBeVisible();
    await expect(inProgressRow.getByText("4 / 3")).toBeVisible();
    await expect(inProgressRow.getByText("1 over limit")).toBeVisible();
    await expect(inProgressRow.getByText("Limit exceeded")).toBeVisible();

    // 3. Verify other workflow stages (To Do, Code Review, Done)
    await expect(wipSection.getByText("2 / 6")).toBeVisible(); // To Do
    await expect(wipSection.getByText("1 / 2")).toBeVisible(); // Code Review
    await expect(wipSection.getByText("3 / 6")).toBeVisible(); // Done

    // 4. Verify diagnostic summary callout
    await expect(
      wipSection.getByText(
        "One or more workflow stages have exceeded their configured WIP limits. Consider redistributing assignments to reduce bottlenecks.",
      ),
    ).toBeVisible();
  });

  test("TC-WIP-DEVELOPER-003: Verify developer capacity breakdown, pseudonyms, and individual limit violation", async ({
    page,
  }) => {
    const project = makeMockProject({
      id: TEST_PROJECT_ID,
      name: "Core Payments",
      slug: "core-payments",
      wip_limit_per_dev: 2,
    });

    await mockAuthenticatedSession(page, {
      preferredUsername: "alpha_scrum",
      name: "Alpha Scrum Master",
      email: "alpha_scrum@manta.local",
      roles: ["SCRUM_MASTER"],
      projects: [project],
    });
    await setupSprintWipMocks(page);

    await page.goto("/sprint-wip", { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("sprint-wip-page")).toBeVisible({
      timeout: 15000,
    });

    // 1. Verify WIP per Developer section
    const devSection = page
      .locator("section")
      .filter({ hasText: "WIP per Developer" });
    await expect(devSection).toBeVisible();
    await expect(
      devSection.getByRole("heading", { name: "WIP per Developer" }),
    ).toBeVisible();
    await expect(devSection.getByText("LIMIT EXCEEDED")).toBeVisible();

    // 2. Verify developer with 3 assigned issues against limit 2 (over limit)
    const overLimitDevCard = devSection
      .locator("div.rounded-lg")
      .filter({ hasText: "Developer 3A1F9C8B7E" });
    await expect(overLimitDevCard).toBeVisible();
    await expect(overLimitDevCard.getByText("3 / 2")).toBeVisible();
    await expect(overLimitDevCard.getByText("1 over limit")).toBeVisible();
    await expect(overLimitDevCard.getByText("150% capacity")).toBeVisible();

    // 3. Verify developer within limit (1 / 2)
    const compliantDevCard = devSection
      .locator("div.rounded-lg")
      .filter({ hasText: "Developer 7E4B2C1A9D" });
    await expect(compliantDevCard).toBeVisible();
    await expect(compliantDevCard.getByText("1 / 2")).toBeVisible();
    await expect(
      compliantDevCard.getByText("50% capacity", { exact: true }),
    ).toBeVisible();
    await expect(compliantDevCard.getByText(/1 slots open/i)).toBeVisible();
  });

  test("TC-SPRINT-SELECTOR-004: Interactive sprint selector dropdown switches between sprints", async ({
    page,
  }) => {
    const project = makeMockProject({
      id: TEST_PROJECT_ID,
      name: "Core Payments",
      slug: "core-payments",
    });

    await mockAuthenticatedSession(page, {
      preferredUsername: "alpha_scrum",
      name: "Alpha Scrum Master",
      email: "alpha_scrum@manta.local",
      roles: ["SCRUM_MASTER"],
      projects: [project],
    });
    await setupSprintWipMocks(page);

    await page.goto("/sprint-wip", { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("sprint-wip-page")).toBeVisible({
      timeout: 15000,
    });

    // 1. Verify initial active sprint
    await expect(page.getByText("Sprint 42 (Manta Delivery)")).toBeVisible();

    // 2. Click sprint selector to open dropdown listbox
    const sprintTrigger = page
      .locator("button")
      .filter({ hasText: "Active Sprint" });
    await sprintTrigger.click();

    const listbox = page.getByRole("listbox", { name: "Available sprints" });
    await expect(listbox).toBeVisible();
    await expect(
      listbox.getByRole("option", { name: /Sprint 42/ }),
    ).toBeVisible();
    await expect(
      listbox.getByRole("option", { name: /Sprint 41/ }),
    ).toBeVisible();

    // 3. Select Sprint 41
    await listbox.getByRole("option", { name: /Sprint 41/ }).click();

    // 4. Verify URL query parameter and sprint change
    await expect(page).toHaveURL(new RegExp(`sprint=${SPRINT_41_ID}`));
    await expect(page.getByText("Sprint 41 (Alpha Foundation)")).toBeVisible();
  });

  test("TC-SPRINT-RBAC-005: Developer without VIEW_SPRINT_BURNDOWN_WIP permission receives access restricted view", async ({
    page,
  }) => {
    const project = makeMockProject({
      id: TEST_PROJECT_ID,
      name: "Core Payments",
      slug: "core-payments",
    });

    // Role DEVELOPER does not have VIEW_SPRINT_BURNDOWN_WIP in the role matrix
    await mockAuthenticatedSession(page, {
      preferredUsername: "beta_dev",
      name: "Beta Developer",
      email: "beta_dev@manta.local",
      roles: ["DEVELOPER"],
      projects: [project],
    });

    await page.goto("/sprint-wip", { waitUntil: "domcontentloaded" });

    // Verify access denied message
    await expect(
      page.getByText("You don’t have access to Sprint & WIP Monitoring"),
    ).toBeVisible({ timeout: 15000 });
    await expect(
      page.getByText(
        "Your role does not include permission to view sprint burndown and WIP metrics. Contact a workspace administrator if you need access.",
      ),
    ).toBeVisible();

    // Verify metrics and charts are hidden
    await expect(page.getByTestId("sprint-wip-page")).not.toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Sprint Burndown" }),
    ).not.toBeVisible();
  });

  test("TC-SPRINT-EMPTY-006: Graceful empty state when project has no active sprint data", async ({
    page,
  }) => {
    const project = makeMockProject({
      id: TEST_PROJECT_ID,
      name: "Core Payments",
      slug: "core-payments",
    });

    await mockAuthenticatedSession(page, {
      preferredUsername: "alpha_scrum",
      name: "Alpha Scrum Master",
      email: "alpha_scrum@manta.local",
      roles: ["SCRUM_MASTER"],
      projects: [project],
    });
    await setupSprintWipMocks(page, {
      sprints: [],
      defaultFlowMetrics: null,
    });

    await page.goto("/sprint-wip", { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("sprint-wip-page")).toBeVisible({
      timeout: 15000,
    });

    // Verify empty state container and action links
    await expect(
      page.getByText("NO ACTIVE SPRINT DATA", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Your next sprint starts here" }),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "Connect Jira" }),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "Explore projects" }),
    ).toBeVisible();
  });
});
