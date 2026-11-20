import { expect, test } from "@playwright/test";
import {
  fulfillCorsJson,
  makeMockProject,
  mockAuthenticatedSession,
  setupStatefulProjectsApi,
} from "./helpers/mock-auth";

const TEST_PROJECT_ID = "11111111-1111-1111-1111-000000000001";

interface TrendPointOptions {
  gapLeadTime?: boolean;
  nullMttr?: boolean;
}

function generateTrendPoints(days: number, opts: TrendPointOptions = {}) {
  const points = [];
  const baseDate = new Date("2026-11-20T00:00:00Z");

  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(baseDate.getTime() - i * 86400000);
    const dateStr = d.toISOString().slice(0, 10);
    // Gap across 3 consecutive days for lead_time (day indexes 12, 13, 14)
    const isGap = Boolean(opts.gapLeadTime && i >= 12 && i <= 14);

    points.push({
      date: dateStr,
      lead_time_hours: isGap ? null : 11.4,
      deployment_frequency_per_day: 2.5,
      change_failure_rate_pct: 6.0,
      mttr_hours: opts.nullMttr ? null : 37.0,
    });
  }

  return points;
}

function buildDoraApiResponse(params: {
  projectId: string;
  windowDays: number;
  aggregates?: {
    lead_time_hours: number | null;
    deployment_frequency_per_day: number | null;
    change_failure_rate_pct: number | null;
    mttr_hours: number | null;
  };
  notes?: string[];
  trendOpts?: TrendPointOptions;
}) {
  const { projectId, windowDays, aggregates, notes, trendOpts } = params;
  const defaultAggregates = {
    lead_time_hours: 11.4,
    deployment_frequency_per_day: 2.5,
    change_failure_rate_pct: 6.0,
    mttr_hours: 37.0,
  };

  return {
    project_id: projectId,
    window: `${windowDays}d` as "14d" | "30d",
    authoritative_deployment_signal: "github_deployments_api",
    aggregates: aggregates ?? defaultAggregates,
    notes: notes ?? [],
    trend: generateTrendPoints(windowDays, trendOpts),
  };
}

test.describe("Delivery Analytics DORA Dashboard UI Verification (US-31 / Task 31.6)", () => {
  test.beforeEach(async ({ page }) => {
    page.on("pageerror", (err) => console.log("[PAGE_ERROR]", err.message));
    page.on("console", (msg) => {
      if (msg.type() === "error") {
        console.log("[CONSOLE_ERROR]", msg.text());
      }
    });
  });
  test("TC-DORA-GAUGE-001: Render all 4 DORA gauges with seeded values", async ({
    page,
  }) => {
    const project = makeMockProject({
      id: TEST_PROJECT_ID,
      name: "Core Payments",
      slug: "core-payments",
      authoritative_deployment_signal: "github_deployments_api",
    });

    await mockAuthenticatedSession(page, {
      roles: ["TENANT_ADMIN"],
      projects: [project],
    });
    await setupStatefulProjectsApi(page, [project]);

    await page.route("**/api/v1/projects/*/dora-metrics*", async (route) => {
      const url = new URL(route.request().url());
      const days = Number(url.searchParams.get("timeframe_days") ?? 30);
      const data = buildDoraApiResponse({
        projectId: TEST_PROJECT_ID,
        windowDays: days,
      });
      await fulfillCorsJson(route, 200, data);
    });

    await page.goto(
      "/delivery-analytics?mockScenario=populated&timeframe=30",
    );
    await expect(page.getByTestId("dora-page")).toBeVisible({ timeout: 15000 });

    const metrics = [
      { id: "lead_time", value: "11.4", tier: "elite" },
      { id: "deployment_frequency", value: "2.5", tier: "high" },
      { id: "change_failure_rate", value: "6", tier: "medium" },
      { id: "mttr", value: "37", tier: "high" },
    ];

    for (const m of metrics) {
      const card = page.getByTestId(`dora-metric-card-${m.id}`);
      await expect(card).toBeVisible();
      await expect(card.getByTestId("dora-metric-value")).toHaveText(m.value);
      await expect(card.getByTestId("dora-tier-badge")).toHaveText(m.tier);
      await expect(card.getByTestId("dora-gauge")).toBeVisible();
      await expect(card.getByTestId("dora-comparison")).toBeVisible();
    }
  });

  test("TC-DORA-NODATA-001: Graceful 'No data' rendering when MTTR is null", async ({
    page,
  }) => {
    const project = makeMockProject({
      id: TEST_PROJECT_ID,
      name: "Core Payments",
      slug: "core-payments",
      authoritative_deployment_signal: "github_deployments_api",
    });

    await mockAuthenticatedSession(page, {
      roles: ["TENANT_ADMIN"],
      projects: [project],
    });
    await setupStatefulProjectsApi(page, [project]);

    await page.route("**/api/v1/projects/*/dora-metrics*", async (route) => {
      const url = new URL(route.request().url());
      const days = Number(url.searchParams.get("timeframe_days") ?? 30);
      const data = buildDoraApiResponse({
        projectId: TEST_PROJECT_ID,
        windowDays: days,
        aggregates: {
          lead_time_hours: 11.4,
          deployment_frequency_per_day: 2.5,
          change_failure_rate_pct: 6.0,
          mttr_hours: null,
        },
        notes: [
          "mttr_hours is null: Service restoration signals are not reported for this project (mock).",
        ],
        trendOpts: { nullMttr: true },
      });
      await fulfillCorsJson(route, 200, data);
    });

    await page.goto("/delivery-analytics?mockScenario=partial&timeframe=30");
    await expect(page.getByTestId("dora-page")).toBeVisible({ timeout: 15000 });

    const mttrCard = page.getByTestId("dora-metric-card-mttr");
    await expect(mttrCard).toBeVisible();

    // Assert absence of numeric value, tier badge, and circular gauge
    await expect(mttrCard.getByTestId("dora-metric-value")).toHaveCount(0);
    await expect(mttrCard.getByTestId("dora-gauge")).toHaveCount(0);
    await expect(mttrCard.getByTestId("dora-tier-badge")).toHaveCount(0);

    // Assert prominent 'No data' block and explanation
    const noDataBlock = mttrCard.getByTestId("dora-metric-no-data");
    await expect(noDataBlock).toBeVisible();
    await expect(noDataBlock.getByText("No data")).toBeVisible();
    await expect(
      noDataBlock.getByText(
        "Service restoration signals are not reported for this project (mock).",
      ),
    ).toBeVisible();

    // Assert empty trend state
    const mttrTrend = page.getByTestId("dora-trend-card-mttr");
    await expect(mttrTrend.getByTestId("dora-trend-empty")).toBeVisible();
    await expect(
      mttrTrend.getByText("Insufficient incident or restoration data"),
    ).toBeVisible();
  });

  test("TC-DORA-GAPS-001: Missing-day gaps rendered as shaded ReferenceArea with 'No data'", async ({
    page,
  }) => {
    const project = makeMockProject({
      id: TEST_PROJECT_ID,
      name: "Core Payments",
      slug: "core-payments",
      authoritative_deployment_signal: "github_deployments_api",
    });

    await mockAuthenticatedSession(page, {
      roles: ["TENANT_ADMIN"],
      projects: [project],
    });
    await setupStatefulProjectsApi(page, [project]);

    await page.route("**/api/v1/projects/*/dora-metrics*", async (route) => {
      const url = new URL(route.request().url());
      const days = Number(url.searchParams.get("timeframe_days") ?? 30);
      const data = buildDoraApiResponse({
        projectId: TEST_PROJECT_ID,
        windowDays: days,
        trendOpts: { gapLeadTime: true },
      });
      await fulfillCorsJson(route, 200, data);
    });

    await page.goto(
      "/delivery-analytics?mockScenario=populated&timeframe=30",
    );
    await expect(page.getByTestId("dora-page")).toBeVisible({ timeout: 15000 });

    const trendCard = page.getByTestId("dora-trend-card-lead_time");
    await expect(trendCard).toBeVisible();

    // ReferenceArea elements in Recharts are rendered within .recharts-reference-area
    const referenceAreas = trendCard.locator(".recharts-reference-area");
    await expect(referenceAreas.first()).toBeVisible();

    // Multi-day gap runs render the text 'No data' inside the shaded area
    await expect(trendCard.locator("text=No data").first()).toBeVisible();
  });

  test("TC-DORA-TIMEFRAME-001: Reactive 14d <-> 30d timeframe toggle switching", async ({
    page,
  }) => {
    const project = makeMockProject({
      id: TEST_PROJECT_ID,
      name: "Core Payments",
      slug: "core-payments",
      authoritative_deployment_signal: "github_deployments_api",
    });

    await mockAuthenticatedSession(page, {
      roles: ["TENANT_ADMIN"],
      projects: [project],
    });
    await setupStatefulProjectsApi(page, [project]);

    await page.route("**/api/v1/projects/*/dora-metrics*", async (route) => {
      const url = new URL(route.request().url());
      const days = Number(url.searchParams.get("timeframe_days") ?? 30);
      const data = buildDoraApiResponse({
        projectId: TEST_PROJECT_ID,
        windowDays: days,
      });
      await fulfillCorsJson(route, 200, data);
    });

    await page.goto(
      "/delivery-analytics?mockScenario=populated&timeframe=30",
    );
    await expect(page.getByTestId("dora-page")).toBeVisible({ timeout: 15000 });

    const btn14d = page.getByTestId("dora-timeframe-14d");
    const btn30d = page.getByTestId("dora-timeframe-30d");

    // Initial 30d state
    await expect(btn30d).toHaveAttribute("aria-pressed", "true");
    await expect(btn14d).toHaveAttribute("aria-pressed", "false");
    await expect(
      page.getByText("Daily telemetry for the last 30 days."),
    ).toBeVisible();

    // Switch to 14d
    await btn14d.click();
    await expect(page).toHaveURL(/(window=14d|timeframe=14)/);
    await expect(btn14d).toHaveAttribute("aria-pressed", "true");
    await expect(btn30d).toHaveAttribute("aria-pressed", "false");
    await expect(
      page.getByText("Daily telemetry for the last 14 days."),
    ).toBeVisible();

    // Switch back to 30d
    await btn30d.click();
    await expect(page).toHaveURL(/(window=30d|timeframe=30)/);
    await expect(btn30d).toHaveAttribute("aria-pressed", "true");
    await expect(btn14d).toHaveAttribute("aria-pressed", "false");
    await expect(
      page.getByText("Daily telemetry for the last 30 days."),
    ).toBeVisible();
  });

  test("TC-DORA-SIGNAL-001: Configured deployment signal card and sync health", async ({
    page,
  }) => {
    const project = makeMockProject({
      id: TEST_PROJECT_ID,
      name: "Core Payments",
      slug: "core-payments",
      authoritative_deployment_signal: "github_deployments_api",
    });

    await mockAuthenticatedSession(page, {
      roles: ["TENANT_ADMIN"],
      projects: [project],
    });
    await setupStatefulProjectsApi(page, [project]);

    await page.route("**/api/v1/projects/*/dora-metrics*", async (route) => {
      const url = new URL(route.request().url());
      const days = Number(url.searchParams.get("timeframe_days") ?? 30);
      const data = buildDoraApiResponse({
        projectId: TEST_PROJECT_ID,
        windowDays: days,
      });
      await fulfillCorsJson(route, 200, data);
    });

    await page.goto(
      "/delivery-analytics?mockScenario=populated&timeframe=30",
    );
    await expect(page.getByTestId("dora-page")).toBeVisible({ timeout: 15000 });

    const signalCard = page.getByTestId("dora-signal-card");
    await expect(signalCard).toBeVisible();
    await expect(signalCard).toHaveAttribute("data-configured", "true");
    await expect(
      signalCard.getByText("GitHub Deployments API - Configured"),
    ).toBeVisible();
    await expect(
      signalCard.getByText("Configured", { exact: true }),
    ).toBeVisible();
    await expect(
      signalCard.getByText(
        "Production deployments are verified using the project's authoritative deployment signal.",
      ),
    ).toBeVisible();

    const syncBlock = signalCard.getByTestId("dora-signal-sync");
    await expect(syncBlock.getByText("Sync healthy")).toBeVisible();
    await expect(syncBlock.getByText(/Last sync/)).toBeVisible();
  });

  test("TC-DORA-COLD-001: Safe dashboard degradation in unconfigured cold state", async ({
    page,
  }) => {
    // Project with unconfigured deployment signal
    const project = makeMockProject({
      id: TEST_PROJECT_ID,
      name: "Unconfigured Project",
      slug: "unconfigured-project",
      authoritative_deployment_signal: "",
      authoritative_cd_signal: "",
    });

    await mockAuthenticatedSession(page, {
      roles: ["TENANT_ADMIN"],
      projects: [project],
    });
    await setupStatefulProjectsApi(page, [project]);

    await page.route("**/api/v1/projects/*/deployment-signal", async (route) => {
      await fulfillCorsJson(route, 200, {
        project_id: TEST_PROJECT_ID,
        tenant_id: "11111111-1111-1111-1111-111111111111",
        authoritative_deployment_signal: "",
        updated_at: new Date().toISOString(),
      });
    });

    await page.route("**/api/v1/projects/*/dora-metrics*", async (route) => {
      const url = new URL(route.request().url());
      const days = Number(url.searchParams.get("timeframe_days") ?? 30);
      await fulfillCorsJson(route, 200, {
        project_id: TEST_PROJECT_ID,
        window: `${days}d`,
        authoritative_deployment_signal: "",
        aggregates: {
          lead_time_hours: null,
          deployment_frequency_per_day: null,
          change_failure_rate_pct: null,
          mttr_hours: null,
        },
        notes: [],
        trend: [],
      });
    });

    await page.goto(
      "/delivery-analytics?mockScenario=signal_not_configured&timeframe=30",
    );
    await expect(page.getByTestId("dora-page")).toBeVisible({ timeout: 15000 });

    const pageRoot = page.getByTestId("dora-page");
    await expect(pageRoot).toHaveAttribute("data-cold-state", "true");
    await expect(page.getByTestId("dora-telemetry-offline")).toBeVisible();

    // Metric cards should show 'No data'
    for (const id of [
      "lead_time",
      "deployment_frequency",
      "change_failure_rate",
      "mttr",
    ]) {
      const card = page.getByTestId(`dora-metric-card-${id}`);
      await expect(card.getByTestId("dora-metric-no-data")).toBeVisible();
    }

    // Trend cards should render empty state
    for (const id of [
      "lead_time",
      "deployment_frequency",
      "change_failure_rate",
      "mttr",
    ]) {
      const card = page.getByTestId(`dora-trend-card-${id}`);
      await expect(card.getByTestId("dora-trend-empty")).toBeVisible();
    }

    // Signal card should indicate pending configuration
    const signalCard = page.getByTestId("dora-signal-card");
    await expect(signalCard).toHaveAttribute("data-configured", "false");
    await expect(
      signalCard.getByText("Awaiting Deployment Integration"),
    ).toBeVisible();
    await expect(
      signalCard.getByText("Pending configuration"),
    ).toBeVisible();
  });
});
