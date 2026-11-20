/**
 * Pure helpers and constants for the Delivery Analytics (DORA) dashboard:
 * formatting, chart-series helpers, metric metadata, deployment-signal helpers,
 * empty view-model builder. No React in this file.
 */
import {
  DORA_METRIC_IDS,
  type DoraDashboardViewModel,
  type DoraDataSource,
  type DoraMetricId,
  type DoraMetricViewModel,
  type DoraTimeframe,
  type DoraTrendPoint,
  type DoraTrendViewModel,
  type DoraUnit,
} from "@/types/dora";

// ==========================================================================
// Metric metadata (definitions follow SRS section 3.2.3.3)
// ==========================================================================

export type ChartKind = "area" | "bar" | "line";
export type Accent = "emerald" | "cyan";

export interface DoraMetricMeta {
  title: string;
  chartTitle: string;
  /** Definitions follow SRS section 3.2.3.3. */
  description: string;
  defaultUnit: DoraUnit;
  trendUnit: DoraUnit;
  chartKind: ChartKind;
  accent: Accent;
}

export const DORA_METRIC_META: Record<DoraMetricId, DoraMetricMeta> = {
  lead_time: {
    title: "Lead Time for Changes",
    chartTitle: "Lead Time for Changes",
    description:
      "Time from the first feature-branch commit to verified production deployment.",
    defaultUnit: "hours",
    trendUnit: "hours",
    chartKind: "area",
    accent: "emerald",
  },
  deployment_frequency: {
    title: "Deployment Frequency",
    chartTitle: "Deployment Frequency",
    description:
      "Verified production deployments, excluding staging and failed deployments.",
    defaultUnit: "per_day",
    trendUnit: "count",
    chartKind: "bar",
    accent: "emerald",
  },
  change_failure_rate: {
    title: "Change Failure Rate",
    chartTitle: "Change Failure Rate",
    description:
      "Production deployments resulting in failure, rollback, or a confirmed Jira incident within 24 hours.",
    defaultUnit: "percent",
    trendUnit: "percent",
    chartKind: "area",
    accent: "cyan",
  },
  mttr: {
    title: "Mean Time to Recovery",
    chartTitle: "Mean Time to Recovery (MTTR)",
    description:
      "Time from incident detection to verified service restoration.",
    defaultUnit: "minutes",
    trendUnit: "minutes",
    chartKind: "line",
    accent: "cyan",
  },
};

export const ACCENT_COLORS: Record<Accent, string> = {
  emerald: "#00e599",
  cyan: "#22d3ee",
};

// ==========================================================================
// Formatting
// ==========================================================================

const formatterCache = new Map<number, Intl.NumberFormat>();

function numberFormat(maxFractionDigits: number): Intl.NumberFormat {
  let formatter = formatterCache.get(maxFractionDigits);
  if (!formatter) {
    formatter = new Intl.NumberFormat("en-US", {
      minimumFractionDigits: 0,
      maximumFractionDigits: maxFractionDigits,
    });
    formatterCache.set(maxFractionDigits, formatter);
  }
  return formatter;
}

export function formatNumber(value: number, maxFractionDigits = 1): string {
  return numberFormat(maxFractionDigits).format(value);
}

export function unitLabel(unit: DoraUnit): string {
  switch (unit) {
    case "hours":
      return "hours";
    case "minutes":
      return "minutes";
    case "per_day":
      return "deploys/day";
    case "percent":
      return "%";
    case "count":
      return "deploys";
  }
}

/** Compact suffix for axes and deltas. */
export function unitSuffix(unit: DoraUnit): string {
  switch (unit) {
    case "hours":
      return "h";
    case "minutes":
      return "m";
    case "per_day":
      return "/d";
    case "percent":
      return "%";
    case "count":
      return "";
  }
}

export function formatValueWithUnit(value: number, unit: DoraUnit): string {
  const n = formatNumber(value);
  switch (unit) {
    case "percent":
      return `${n}%`;
    case "count":
      return `${n} ${value === 1 ? "deploy" : "deploys"}`;
    case "per_day":
      return `${n} deploys/day`;
    default:
      return `${n} ${unitLabel(unit)}`;
  }
}

function parseUtcDate(iso: string): Date | null {
  const date = new Date(`${iso}T00:00:00Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatShortDate(iso: string): string {
  const date = parseUtcDate(iso);
  if (!date) return iso;
  return date.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

export function formatLongDate(iso: string): string {
  const date = parseUtcDate(iso);
  if (!date) return iso;
  return date.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  });
}

export function formatRelativeTime(iso: string, nowMs: number): string | null {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return null;
  const seconds = Math.max(0, Math.round((nowMs - then) / 1000));
  if (seconds < 45) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

export function formatClockTime(ms: number): string {
  return new Date(ms).toLocaleTimeString("en-US", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

// ==========================================================================
// Chart series helpers
// ==========================================================================

export interface ChartRow {
  date: string;
  /** null is preserved so charts render gaps instead of zeros. */
  value: number | null;
  note: string | null;
}

export function toChartRows(points: DoraTrendPoint[]): ChartRow[] {
  return points.map((p) => ({ date: p.date, value: p.value, note: p.note }));
}

export function hasAnyValue(points: DoraTrendPoint[]): boolean {
  return points.some((p) => p.value !== null);
}

export interface GapRun {
  startIndex: number;
  endIndex: number;
}

/** Consecutive runs of null values. Zeros are real values and never form gaps. */
export function findGapRuns(points: DoraTrendPoint[]): GapRun[] {
  const runs: GapRun[] = [];
  let start = -1;
  points.forEach((point, index) => {
    if (point.value === null) {
      if (start === -1) start = index;
    } else if (start !== -1) {
      runs.push({ startIndex: start, endIndex: index - 1 });
      start = -1;
    }
  });
  if (start !== -1) runs.push({ startIndex: start, endIndex: points.length - 1 });
  return runs;
}

/**
 * x1/x2 for a ReferenceArea. Line/area charts use a point scale, so the band is
 * widened to the neighbouring points; bar charts use a band scale and take the
 * exact days.
 */
export function gapBand(
  rows: ChartRow[],
  run: GapRun,
  scale: "point" | "band",
): { x1: string; x2: string } {
  if (scale === "band") {
    return { x1: rows[run.startIndex].date, x2: rows[run.endIndex].date };
  }
  const left = Math.max(0, run.startIndex - 1);
  const right = Math.min(rows.length - 1, run.endIndex + 1);
  return { x1: rows[left].date, x2: rows[right].date };
}

export type TelemetryClass = "populated" | "partial" | "empty";

export function classifyTelemetry(vm: DoraDashboardViewModel): TelemetryClass {
  const metricsWithValue = DORA_METRIC_IDS.filter(
    (id) => vm.metrics[id].value !== null,
  ).length;
  const trendsWithData = DORA_METRIC_IDS.filter((id) =>
    hasAnyValue(vm.trends[id].points),
  ).length;

  if (metricsWithValue === 0 && trendsWithData === 0) return "empty";
  if (
    metricsWithValue === DORA_METRIC_IDS.length &&
    trendsWithData === DORA_METRIC_IDS.length
  ) {
    return "populated";
  }
  return "partial";
}

// ==========================================================================
// Deployment signal helpers
// ==========================================================================

const SIGNAL_LABELS: Record<string, string> = {
  github_deployments_api: "GitHub Deployments API",
  argocd_postsync_webhook: "ArgoCD PostSync Webhook",
  direct_deployment_api: "Direct Deployment API",
};

export function signalLabel(type: string | null): string {
  if (!type) return "Deployment signal";
  return SIGNAL_LABELS[type] ?? type;
}

/**
 * Reads `authoritative_deployment_signal` from the existing deployment-signal
 * query result (GET /api/v1/projects/{id}/deployment-signal, API reference 7.6).
 * Accepts `unknown` so it compiles regardless of the exact DeploymentSignal type.
 */
export function readSignalType(data: unknown): string | null {
  if (typeof data !== "object" || data === null) return null;
  const value = (data as Record<string, unknown>).authoritative_deployment_signal;
  return typeof value === "string" && value.trim().length > 0 ? value : null;
}

// ==========================================================================
// Empty view model
// ==========================================================================

/** A view model with no values at all. Contains no telemetry of any kind. */
export function buildEmptyDashboard(params: {
  projectId: string;
  timeframeDays: DoraTimeframe;
  source: DoraDataSource;
}): DoraDashboardViewModel {
  const metrics = {} as Record<DoraMetricId, DoraMetricViewModel>;
  const trends = {} as Record<DoraMetricId, DoraTrendViewModel>;

  for (const id of DORA_METRIC_IDS) {
    const meta = DORA_METRIC_META[id];
    metrics[id] = {
      id,
      unit: meta.defaultUnit,
      value: null,
      tier: null,
      gauge: null,
      comparison: null,
      unavailableLabel: null,
      unavailableReason: null,
    };
    trends[id] = {
      id,
      unit: meta.trendUnit,
      points: [],
      subtitle: null,
      targetLabel: null,
      threshold: null,
      emptyReason: null,
    };
  }

  return {
    source: params.source,
    projectId: params.projectId,
    timeframeDays: params.timeframeDays,
    generatedAt: null,
    verification: { status: "unavailable", detail: null },
    metrics,
    trends,
    signal: null,
  };
}