/**
 * DEVELOPMENT-ONLY MOCK DATA. Every view model built here is tagged source: "mock".
 * Values are synthetic, deterministic and unrelated to any real telemetry.
 * This module is only reachable through a NODE_ENV-guarded dynamic import in
 * lib/dora/dora-service.ts and must never be imported from production code paths.
 */
import {
  DORA_METRIC_IDS,
  type DoraDashboardViewModel,
  type DoraFetchResult,
  type DoraMetricId,
  type DoraMockScenario,
  type DoraSignalViewModel,
  type DoraTimeframe,
  type DoraTrendPoint,
} from "@/types/dora";
import { buildEmptyDashboard } from "@/lib/dora/dora-utils";

function seeded(i: number, salt: number): number {
  const x = Math.sin(i * 12.9898 + salt * 78.233) * 43758.5453;
  return x - Math.floor(x);
}

const round1 = (n: number) => Math.round(n * 10) / 10;

function dayList(days: number): string[] {
  return Array.from({ length: days }, (_, i) => {
    const d = new Date();
    d.setUTCHours(0, 0, 0, 0);
    d.setUTCDate(d.getUTCDate() - (days - 1 - i));
    return d.toISOString().slice(0, 10);
  });
}

function gapIndexes(days: number): Set<number> {
  return days === 14 ? new Set([6, 7]) : new Set([12, 13, 14]);
}

function buildSeries(days: number): Record<DoraMetricId, DoraTrendPoint[]> {
  const gaps = gapIndexes(days);
  const out: Record<DoraMetricId, DoraTrendPoint[]> = {
    lead_time: [],
    deployment_frequency: [],
    change_failure_rate: [],
    mttr: [],
  };

  dayList(days).forEach((date, i) => {
    if (gaps.has(i)) {
      for (const id of DORA_METRIC_IDS) {
        out[id].push({
          date,
          value: null,
          note: id === "mttr" ? "No telemetry" : null,
        });
      }
      return;
    }
    const deploys = Math.floor(seeded(i, 1) * 5); // 0 is a real zero here
    out.deployment_frequency.push({ date, value: deploys, note: null });
    out.lead_time.push({
      date,
      value: deploys > 0 ? round1(5 + seeded(i, 2) * 14) : null,
      note: null,
    });
    out.change_failure_rate.push({
      date,
      value: deploys > 0 ? round1(seeded(i, 3) * 15) : null, // null when no deployments
      note: null,
    });
    out.mttr.push({
      date,
      value: i % 4 === 1 ? Math.round(20 + seeded(i, 4) * 40) : null,
      note: null,
    });
  });
  return out;
}

const MOCK_SIGNAL_CONFIGURED: DoraSignalViewModel = {
  configured: true,
  type: "github_deployments_api",
  sync: {
    status: "healthy",
    lastSyncedAt: new Date(Date.now() - 4 * 60_000).toISOString(),
    detail: "Mock sync status",
  },
};

function populated(projectId: string, days: DoraTimeframe): DoraDashboardViewModel {
  const series = buildSeries(days);
  const base = buildEmptyDashboard({ projectId, timeframeDays: days, source: "mock" });

  const mk = (
    id: DoraMetricId,
    value: number,
    tier: "elite" | "high" | "medium" | "low",
    max: number,
    thresholdValue: number,
  ) => {
    base.metrics[id] = {
      ...base.metrics[id],
      value,
      tier,
      gauge: {
        min: 0,
        max,
        thresholds: [{ value: thresholdValue, label: null }],
        label: "MOCK",
        caption: "synthetic data",
      },
      comparison: { previousValue: round1(value * 1.12), sentiment: "improved", periodLabel: "vs prev period" },
    };
  };
  mk("lead_time", 11.4, "elite", 48, 24);
  mk("deployment_frequency", 2.5, "high", 6, 3);
  mk("change_failure_rate", 6, "medium", 30, 15);
  mk("mttr", 37, "high", 120, 60);

  for (const id of DORA_METRIC_IDS) {
    base.trends[id] = {
      ...base.trends[id],
      points: series[id],
      subtitle: "Mock data, synthetic values",
      targetLabel: "MOCK TARGET",
    };
  }
  base.trends.change_failure_rate.threshold = { value: 10, label: "Mock threshold" };

  return {
    ...base,
    generatedAt: new Date(Date.now() - 4 * 60_000).toISOString(),
    verification: { status: "verified", detail: "Mock verification" },
    signal: MOCK_SIGNAL_CONFIGURED,
  };
}

function build(
  projectId: string,
  days: DoraTimeframe,
  scenario: DoraMockScenario,
): DoraDashboardViewModel {
  switch (scenario) {
    case "populated":
      return populated(projectId, days);
    case "partial": {
      const vm = populated(projectId, days);
      vm.metrics.mttr = {
        ...vm.metrics.mttr,
        value: null,
        tier: null,
        gauge: null,
        comparison: null,
        unavailableLabel: "UNAVAILABLE",
        unavailableReason: "Service restoration signals are not reported for this project (mock).",
      };
      vm.trends.mttr = {
        ...vm.trends.mttr,
        points: vm.trends.mttr.points.map((p) => ({ ...p, value: null })),
        emptyReason: "Insufficient incident or restoration data for the selected timeframe (mock).",
      };
      vm.verification = { status: "unverified", detail: "Mock: partial telemetry" };
      return vm;
    }
    case "empty":
      return {
        ...buildEmptyDashboard({ projectId, timeframeDays: days, source: "mock" }),
        signal: { configured: true, type: "github_deployments_api", sync: null },
      };
    case "signal_not_configured":
    default:
      return {
        ...buildEmptyDashboard({ projectId, timeframeDays: days, source: "mock" }),
        signal: { configured: false, type: null, sync: null },
      };
  }
}

export async function getMockDoraResult(params: {
  projectId: string;
  days: DoraTimeframe;
  scenario: DoraMockScenario;
}): Promise<DoraFetchResult> {
  await new Promise((resolve) => setTimeout(resolve, 350));

  switch (params.scenario) {
    case "loading":
      return new Promise<DoraFetchResult>(() => {});
    case "error":
      throw new Error("Simulated request error (mock scenario).");
    case "unavailable":
      return {
        kind: "unavailable",
        reason: "Simulated: the delivery metrics endpoint is not available (mock scenario).",
      };
    default:
      return {
        kind: "ok",
        data: build(params.projectId, params.days, params.scenario),
      };
  }
}

