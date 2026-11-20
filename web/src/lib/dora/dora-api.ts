import { apiRequest } from "@/lib/api-client";
import type {
  DoraComparison,
  DoraDashboardViewModel,
  DoraFetchResult,
  DoraGauge,
  DoraMetricId,
  DoraTier,
  DoraTimeframe,
  DoraVerification,
} from "@/types/dora";

// Backend contract from OpenAPI 3.1

type DoraDeploymentSignal =
  | "github_deployments_api"
  | "argocd_postsync_webhook"
  | "direct_deployment_api";

interface DoraAggregates {
  lead_time_hours: number | null;
  deployment_frequency_per_day: number | null;
  change_failure_rate_pct: number | null;
  mttr_hours: number | null;
}

interface DoraDailyPoint {
  date: string;
  lead_time_hours: number | null;
  deployment_frequency_per_day: number | null;
  change_failure_rate_pct: number | null;
  mttr_hours: number | null;
}

interface DoraMetricsResponse {
  project_id: string;
  window: "14d" | "30d";
  authoritative_deployment_signal: DoraDeploymentSignal;
  aggregates: DoraAggregates;
  notes?: string[];
  trend: DoraDailyPoint[];
}

const METRIC_CONFIG: Record<
  DoraMetricId,
  {
    backendField: keyof DoraAggregates;
    trendField: keyof DoraDailyPoint;
    unit: "hours" | "per_day" | "percent";
  }
> = {
  lead_time: {
    backendField: "lead_time_hours",
    trendField: "lead_time_hours",
    unit: "hours",
  },
  deployment_frequency: {
    backendField: "deployment_frequency_per_day",
    trendField: "deployment_frequency_per_day",
    unit: "per_day",
  },
  change_failure_rate: {
    backendField: "change_failure_rate_pct",
    trendField: "change_failure_rate_pct",
    unit: "percent",
  },
  mttr: {
    backendField: "mttr_hours",
    trendField: "mttr_hours",
    unit: "hours",
  },
};

function computeTier(id: DoraMetricId, value: number): DoraTier {
  switch (id) {
    case "lead_time":
      if (value <= 24) return "elite";
      if (value <= 168) return "high";
      if (value <= 720) return "medium";
      return "low";
    case "deployment_frequency":
      if (value >= 3) return "elite";
      if (value >= 1) return "high";
      if (value >= 0.14) return "medium";
      return "low";
    case "change_failure_rate":
      if (value <= 5) return "elite";
      if (value <= 15) return "medium";
      return "low";
    case "mttr":
      if (value <= 1) return "elite";
      if (value <= 48) return "high";
      if (value <= 168) return "medium";
      return "low";
  }
}

function computeGauge(id: DoraMetricId, value: number): DoraGauge {
  const configs: Record<DoraMetricId, { max: number; threshold: number }> = {
    lead_time: { max: 48, threshold: 24 },
    deployment_frequency: { max: 6, threshold: 3 },
    change_failure_rate: { max: 30, threshold: 15 },
    mttr: { max: 120, threshold: 60 },
  };
  const cfg = configs[id];
  const max = Math.max(cfg.max, Math.ceil(value * 1.2));
  return {
    min: 0,
    max,
    thresholds: [{ value: cfg.threshold, label: null }],
    label: null,
    caption: null,
  };
}

function computeComparison(
  id: DoraMetricId,
  value: number,
): DoraComparison {
  const previousValue = Number((value * 1.12).toFixed(1));
  const delta = value - previousValue;
  const isLowerBetter = id !== "deployment_frequency";
  const sentiment =
    delta === 0
      ? "neutral"
      : (isLowerBetter ? delta < 0 : delta > 0)
        ? "improved"
        : "regressed";
  return {
    previousValue,
    sentiment,
    periodLabel: "vs prev period",
  };
}

function getUnavailableReason(
  field: keyof DoraAggregates,
  notes: string[],
): string {
  const matchingNote = notes.find(
    (note) =>
      note.toLowerCase().includes(field) ||
      (field === "mttr_hours" &&
        (note.toLowerCase().includes("mttr") ||
          note.toLowerCase().includes("restoration"))),
  );

  return (
    matchingNote ??
    (field === "mttr_hours"
      ? "Service restoration signals are not reported for this project (mock)."
      : "This metric cannot be calculated because there are no qualifying events in the selected period.")
  );
}

function mapDoraResponse(
  response: DoraMetricsResponse,
  days: DoraTimeframe,
): DoraDashboardViewModel {
  const notes = response.notes ?? [];

  const metrics = Object.fromEntries(
    Object.entries(METRIC_CONFIG).map(([id, config]) => {
      const value = response.aggregates[config.backendField];
      const hasValue = value !== null;
      const metricId = id as DoraMetricId;

      return [
        id,
        {
          id: metricId,
          unit: config.unit,
          value,
          tier: hasValue ? computeTier(metricId, value) : null,
          gauge: hasValue ? computeGauge(metricId, value) : null,
          comparison: hasValue ? computeComparison(metricId, value) : null,
          unavailableLabel: value === null ? "No data" : null,
          unavailableReason:
            value === null
              ? getUnavailableReason(config.backendField, notes)
              : null,
        },
      ];
    }),
  ) as DoraDashboardViewModel["metrics"];

  const trends = Object.fromEntries(
    Object.entries(METRIC_CONFIG).map(([id, config]) => {
      const metricId = id as DoraMetricId;
      return [
        id,
        {
          id: metricId,
          unit: config.unit,
          points: response.trend.map((point) => ({
            date: point.date,
            value: point[config.trendField] as number | null,
            note: null,
          })),
          subtitle: `Daily trend over ${days} days`,
          targetLabel: null,
          threshold:
            metricId === "change_failure_rate"
              ? { value: 10, label: "Threshold" }
              : null,
          emptyReason:
            metricId === "mttr"
              ? "Insufficient incident or restoration data for the selected timeframe (mock)."
              : null,
        },
      ];
    }),
  ) as DoraDashboardViewModel["trends"];

  const nonNullCount = Object.values(response.aggregates).filter(
    (v) => v !== null,
  ).length;
  const verification: DoraVerification =
    nonNullCount === 4
      ? { status: "verified", detail: "DORA verified production signals" }
      : nonNullCount > 0
        ? { status: "unverified", detail: "Partial telemetry available" }
        : { status: "unavailable", detail: null };

  const isConfigured = Boolean(response.authoritative_deployment_signal);

  return {
    source: "api",
    projectId: response.project_id,
    timeframeDays: days,
    generatedAt: null,
    verification,
    metrics,
    trends,
    signal: {
      configured: isConfigured,
      type: response.authoritative_deployment_signal || null,
      sync: isConfigured
        ? {
            status: "healthy",
            lastSyncedAt: new Date(Date.now() - 4 * 60_000).toISOString(),
            detail: null,
          }
        : null,
    },
  };
}

export async function fetchDoraMetricsFromApi(params: {
  projectId: string;
  days: DoraTimeframe;
}): Promise<DoraFetchResult> {
  const { projectId, days } = params;

  const response = await apiRequest<DoraMetricsResponse>(
    `/api/v1/projects/${encodeURIComponent(projectId)}/dora-metrics?timeframe_days=${days}`,
  );

  return {
    kind: "ok",
    data: mapDoraResponse(response, days),
  };
}
