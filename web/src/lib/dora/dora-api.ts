import { apiRequest } from "@/lib/api-client";
import type {
  DoraDashboardViewModel,
  DoraFetchResult,
  DoraMetricId,
  DoraTimeframe,
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

function getUnavailableReason(
  field: keyof DoraAggregates,
  notes: string[],
): string {
  const matchingNote = notes.find((note) => note.toLowerCase().includes(field));

  return (
    matchingNote ??
    "This metric cannot be calculated because there are no qualifying events in the selected period."
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

      return [
        id,
        {
          id,
          unit: config.unit,
          value,
          tier: null,
          gauge: null,
          comparison: null,
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
    Object.entries(METRIC_CONFIG).map(([id, config]) => [
      id,
      {
        id,
        unit: config.unit,
        points: response.trend.map((point) => ({
          date: point.date,
          value: point[config.trendField] as number | null,
          note: null,
        })),
        subtitle: `Daily trend over ${days} days`,
        targetLabel: null,
        threshold: null,
        emptyReason: null,
      },
    ]),
  ) as DoraDashboardViewModel["trends"];

  return {
    source: "api",
    projectId: response.project_id,
    timeframeDays: days,
    generatedAt: null,
    verification: {
      status: "unavailable",
      detail: null,
    },
    metrics,
    trends,
    signal: {
      configured: Boolean(response.authoritative_deployment_signal),
      type: response.authoritative_deployment_signal,
      sync: null,
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
