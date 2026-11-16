/**
 * View-model types for the Delivery Analytics (DORA) dashboard.
 *
 * These are UI contracts, NOT backend wire types. The production adapter
 * (lib/dora/dora-api.ts) is responsible for mapping whatever the backend
 * eventually returns into these shapes. See docs/dora-backend-requirements.md.
 */

export type DoraMetricId =
  | "lead_time"
  | "deployment_frequency"
  | "change_failure_rate"
  | "mttr";

export const DORA_METRIC_IDS: readonly DoraMetricId[] = [
  "lead_time",
  "deployment_frequency",
  "change_failure_rate",
  "mttr",
];

export type DoraTimeframe = 14 | 30;
export const DORA_TIMEFRAMES: readonly DoraTimeframe[] = [14, 30];
export const DEFAULT_DORA_TIMEFRAME: DoraTimeframe = 30;

/** Performance tier. Always backend-provided; the frontend never derives it. */
export type DoraTier = "elite" | "high" | "medium" | "low";

export type DoraUnit = "hours" | "minutes" | "per_day" | "percent" | "count";

/** "mock" view models must never be rendered outside non-production mock mode. */
export type DoraDataSource = "api" | "mock";

/** Backend-provided verification status. "unavailable" = backend does not report it. */
export type DoraVerificationStatus = "verified" | "unverified" | "unavailable";

export interface DoraVerification {
  status: DoraVerificationStatus;
  detail: string | null;
}

export type DoraSyncStatus = "healthy" | "delayed" | "failing" | "never_synced";

export interface DoraSyncInfo {
  status: DoraSyncStatus | null;
  lastSyncedAt: string | null;
  detail: string | null;
}

export interface DoraSignalViewModel {
  configured: boolean;
  /** Raw backend identifier, e.g. "github_deployments_api". */
  type: string | null;
  /** Only present when the backend reports it. */
  sync: DoraSyncInfo | null;
}

export type DoraSignalState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; signal: DoraSignalViewModel };

export interface DoraGaugeThreshold {
  value: number;
  label: string | null;
}

/** Backend-provided gauge range. The gauge component never invents a scale. */
export interface DoraGauge {
  min: number;
  max: number;
  thresholds: DoraGaugeThreshold[];
  label: string | null;
  caption: string | null;
}

export interface DoraComparison {
  previousValue: number;
  sentiment: "improved" | "regressed" | "neutral";
  periodLabel: string | null;
}

export interface DoraMetricViewModel {
  id: DoraMetricId;
  unit: DoraUnit;
  /** null = cannot be established. Never coerced to 0. */
  value: number | null;
  tier: DoraTier | null;
  gauge: DoraGauge | null;
  comparison: DoraComparison | null;
  unavailableLabel: string | null;
  unavailableReason: string | null;
}

export interface DoraTrendPoint {
  /** ISO date (YYYY-MM-DD, UTC). */
  date: string;
  /** null = no data; 0 = telemetry available and nothing occurred. */
  value: number | null;
  note: string | null;
}

export interface DoraTrendThreshold {
  value: number;
  label: string | null;
}

export interface DoraTrendViewModel {
  id: DoraMetricId;
  unit: DoraUnit;
  points: DoraTrendPoint[];
  subtitle: string | null;
  targetLabel: string | null;
  threshold: DoraTrendThreshold | null;
  emptyReason: string | null;
}

export interface DoraDashboardViewModel {
  source: DoraDataSource;
  projectId: string;
  timeframeDays: DoraTimeframe;
  generatedAt: string | null;
  verification: DoraVerification;
  metrics: Record<DoraMetricId, DoraMetricViewModel>;
  trends: Record<DoraMetricId, DoraTrendViewModel>;
  /** Signal details reported alongside metrics (mock mode, or sync info from the API). */
  signal: DoraSignalViewModel | null;
}

export type DoraFetchResult =
  | { kind: "ok"; data: DoraDashboardViewModel }
  | { kind: "unavailable"; reason: string };

export type DoraMockScenario =
  | "populated"
  | "partial"
  | "empty"
  | "signal_not_configured"
  | "unavailable"
  | "error"
  | "loading";

export type DoraEmptyVariant = "not_configured" | "awaiting_telemetry" | "no_data";
