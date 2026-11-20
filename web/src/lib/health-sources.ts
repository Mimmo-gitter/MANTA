import type {
  HealthProbeConfig,
  HealthProbeSourceId,
  ProductionFailureSession,
} from "@/types/project";

export interface HealthProbeSourceMeta {
  id: HealthProbeSourceId;
  label: string;
  shortLabel: string;
  subtitle: string;
  payloadSource: string;
}

export const HEALTH_PROBE_SOURCES: readonly HealthProbeSourceMeta[] = [
  {
    id: "generic",
    label: "Generic Custom Probe",
    shortLabel: "Generic",
    subtitle: "Custom CI, synthetic cron or internal script",
    payloadSource: "generic",
  },
  {
    id: "prometheus",
    label: "Prometheus / Alertmanager",
    shortLabel: "Prometheus",
    subtitle: "Alertmanager webhook receiver",
    payloadSource: "prometheus",
  },
  {
    id: "datadog",
    label: "Datadog Monitor",
    shortLabel: "Datadog",
    subtitle: "Monitor alert state webhook",
    payloadSource: "datadog",
  },
  {
    id: "pagerduty",
    label: "PagerDuty Webhook",
    shortLabel: "PagerDuty",
    subtitle: "V3 incident trigger & resolve webhook",
    payloadSource: "pagerduty",
  },
] as const;

const UTC_MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
] as const;

export function normalizeProbeSource(
  raw?: string | null,
): HealthProbeSourceId {
  const clean = (raw ?? "").trim().toLowerCase();
  if (clean.includes("prometheus") || clean.includes("alertmanager")) {
    return "prometheus";
  }
  if (clean.includes("datadog")) {
    return "datadog";
  }
  if (clean.includes("pagerduty")) {
    return "pagerduty";
  }
  return "generic";
}

export function getProbeSourceMeta(
  raw?: string | null,
): HealthProbeSourceMeta {
  const normalized = normalizeProbeSource(raw);
  return (
    HEALTH_PROBE_SOURCES.find((source) => source.id === normalized) ??
    HEALTH_PROBE_SOURCES[0]
  );
}

export function hasConfiguredHealthSecret(
  config?: HealthProbeConfig | null,
): boolean {
  if (!config) return false;
  if (config.webhook_secret && config.webhook_secret.trim().length > 0) {
    return true;
  }
  const masked = (config.webhook_secret_masked ?? "").trim();
  return masked.length > 0 && masked !== "********";
}

export function extractEndpointPath(url: string): string {
  try {
    const parsed = new URL(url);
    return parsed.pathname;
  } catch {
    const match = url.match(/\/api\/.+$/);
    return match ? match[0] : url;
  }
}

export function formatHealthSessionTimestamp(isoDate: string): string {
  const date = new Date(isoDate);
  if (Number.isNaN(date.getTime())) return "Unknown time";
  const mon = UTC_MONTHS[date.getUTCMonth()];
  const day = date.getUTCDate();
  const hh = String(date.getUTCHours()).padStart(2, "0");
  const mm = String(date.getUTCMinutes()).padStart(2, "0");
  const ss = String(date.getUTCSeconds()).padStart(2, "0");
  return `${mon} ${day} · ${hh}:${mm}:${ss} UTC`;
}

export function formatUtcHourMinute(isoDate: string): string {
  const date = new Date(isoDate);
  if (Number.isNaN(date.getTime())) return "Unknown UTC";
  const hh = String(date.getUTCHours()).padStart(2, "0");
  const mm = String(date.getUTCMinutes()).padStart(2, "0");
  return `${hh}:${mm} UTC`;
}

export function formatRecoveryDuration(
  totalSeconds: number | null | undefined,
): string {
  if (totalSeconds === null || totalSeconds === undefined || Number.isNaN(totalSeconds)) {
    return "—";
  }
  const clamped = Math.max(0, Math.round(totalSeconds));
  const hours = Math.floor(clamped / 3600);
  const mins = Math.floor((clamped % 3600) / 60);
  const secs = clamped % 60;

  if (hours > 0) {
    return `${hours}h ${String(mins).padStart(2, "0")}m`;
  }
  if (mins > 0) {
    return `${mins}m ${String(secs).padStart(2, "0")}s`;
  }
  return `${secs}s`;
}

export function formatProbeRelativeAge(
  isoDate: string | null | undefined,
  nowMs: number,
): string {
  if (!isoDate) return "just now";
  const ts = Date.parse(isoDate);
  if (Number.isNaN(ts)) return "just now";
  const diffSec = Math.max(0, Math.floor((nowMs - ts) / 1000));
  if (diffSec < 60) return `${diffSec}s ago`;
  const diffMins = Math.floor(diffSec / 60);
  if (diffMins < 60) return `${diffMins}m ago`;
  const diffHours = Math.floor(diffMins / 60);
  if (diffHours < 24) return `${diffHours}h ago`;
  const diffDays = Math.floor(diffHours / 24);
  return `${diffDays}d ago`;
}

export function computeMeanRecoverySeconds30d(
  sessions: ProductionFailureSession[],
  nowMs: number,
): number | null {
  const thirtyDaysMs = 30 * 24 * 60 * 60 * 1000;
  const cutoff = nowMs - thirtyDaysMs;
  const resolvedValues: number[] = [];

  for (const session of sessions) {
    if (session.status !== "RESOLVED" || session.recovery_time_seconds === null) {
      continue;
    }
    const refTs = Date.parse(session.resolved_at ?? session.started_at);
    if (Number.isNaN(refTs) || refTs >= cutoff) {
      resolvedValues.push(Number(session.recovery_time_seconds));
    }
  }

  if (resolvedValues.length === 0) {
    return null;
  }

  const sum = resolvedValues.reduce((acc, value) => acc + value, 0);
  return sum / resolvedValues.length;
}
