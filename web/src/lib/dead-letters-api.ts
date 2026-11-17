import { apiRequest } from "@/lib/api-client";
import type {
  DeadLetterItem,
  DeadLetterListResponse,
  RedriveResponse,
} from "@/types/dead-letter";

export type NormalizedDeadLetterProvider =
  | "github"
  | "jira"
  | "slack"
  | "argocd"
  | "generic";

export interface DeadLetterMeta {
  normalizedProvider: NormalizedDeadLetterProvider;
  providerLabel: string;
  providerWebhookLabel: string;
  eventType: string;
  deliveryShortId: string;
  contextTarget: string;
  shortErrorClass: string;
  errorTone: "danger" | "warning";
  errorSummaryMessage: string;
  stackFrameCount: number;
  payloadJson: string;
  payloadBytes: number;
}

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

function asNonEmptyString(value: unknown): string | null {
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed.length > 0 ? trimmed : null;
  }
  return null;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return null;
}

export function normalizeDeadLetterProvider(
  provider: string,
): NormalizedDeadLetterProvider {
  const clean = provider.trim().toLowerCase();
  if (clean === "github") return "github";
  if (clean === "jira") return "jira";
  if (clean === "slack") return "slack";
  if (clean === "argocd") return "argocd";
  return "generic";
}

export function formatProviderLabel(provider: string): string {
  const clean = provider.trim().toLowerCase();
  if (clean === "github") return "GitHub";
  if (clean === "jira") return "Jira";
  if (clean === "slack") return "Slack";
  if (clean === "argocd") return "ArgoCD";
  if (clean === "generic" || !clean) return "Webhook";
  return clean.charAt(0).toUpperCase() + clean.slice(1);
}

export function formatProviderWebhookLabel(provider: string): string {
  const clean = provider.trim().toLowerCase();
  if (clean === "github") return "GitHub Webhook";
  if (clean === "jira") return "Jira Webhook";
  if (clean === "slack") return "Slack Event";
  if (clean === "argocd") return "ArgoCD Webhook";
  return `${formatProviderLabel(provider)} Webhook`;
}

export function formatRelativeAge(isoDate: string): string {
  const ts = Date.parse(isoDate);
  if (Number.isNaN(ts)) return "just now";
  const diffSec = Math.max(0, Math.floor((Date.now() - ts) / 1000));
  if (diffSec < 60) return "just now";
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHours = Math.floor(diffMin / 60);
  if (diffHours < 24) return `${diffHours}h ago`;
  const diffDays = Math.floor(diffHours / 24);
  return `${diffDays}d ago`;
}

export function formatQuarantinedTableTime(isoDate: string): string {
  const date = new Date(isoDate);
  if (Number.isNaN(date.getTime())) return "unknown time";
  const hh = String(date.getUTCHours()).padStart(2, "0");
  const mm = String(date.getUTCMinutes()).padStart(2, "0");
  return `${formatRelativeAge(isoDate)} · ${hh}:${mm} UTC`;
}

export function formatQuarantinedDrawerTime(isoDate: string): string {
  const date = new Date(isoDate);
  if (Number.isNaN(date.getTime())) return "Unknown timestamp";
  const mon = UTC_MONTHS[date.getUTCMonth()];
  const day = date.getUTCDate();
  const year = date.getUTCFullYear();
  const hh = String(date.getUTCHours()).padStart(2, "0");
  const mm = String(date.getUTCMinutes()).padStart(2, "0");
  const ss = String(date.getUTCSeconds()).padStart(2, "0");
  return `${mon} ${day}, ${year} · ${hh}:${mm}:${ss} UTC`;
}

export function isSameUtcDay(isoDate: string | null | undefined): boolean {
  if (!isoDate) return false;
  const parsed = new Date(isoDate);
  if (Number.isNaN(parsed.getTime())) return false;
  const now = new Date();
  return (
    parsed.getUTCFullYear() === now.getUTCFullYear() &&
    parsed.getUTCMonth() === now.getUTCMonth() &&
    parsed.getUTCDate() === now.getUTCDate()
  );
}

export function extractDeadLetterMeta(item: DeadLetterItem): DeadLetterMeta {
  const payload = asRecord(item.payload) ?? {};
  const nestedEvent = asRecord(payload.event);
  const repository = asRecord(payload.repository);
  const issue = asRecord(payload.issue);
  const issueFields = asRecord(issue?.fields);
  const issueProject = asRecord(issueFields?.project);
  const deployment = asRecord(payload.deployment);

  const normalizedProvider = normalizeDeadLetterProvider(item.provider);
  const providerLabel = formatProviderLabel(item.provider);
  const providerWebhookLabel = formatProviderWebhookLabel(item.provider);

  const eventType =
    asNonEmptyString(payload.event_type) ??
    asNonEmptyString(payload.webhookEvent) ??
    asNonEmptyString(payload.event) ??
    asNonEmptyString(nestedEvent?.type) ??
    asNonEmptyString(payload.hookType) ??
    asNonEmptyString(payload.type) ??
    asNonEmptyString(payload.action) ??
    "webhook_delivery";

  const rawDeliveryId =
    asNonEmptyString(payload.delivery_id) ??
    asNonEmptyString(payload.x_github_delivery) ??
    asNonEmptyString(payload.provider_delivery_id);

  const deliveryShortId =
    rawDeliveryId ?? `dlv_${item.original_event_id.replace(/-/g, "").slice(0, 8)}`;

  const issueKey = asNonEmptyString(issue?.key);
  const issueProjectName =
    asNonEmptyString(issueProject?.name) ?? asNonEmptyString(payload.project);
  const jiraTarget =
    issueKey && issueProjectName
      ? `${issueKey} · ${issueProjectName}`
      : issueKey ?? issueProjectName;

  const contextTarget =
    asNonEmptyString(repository?.full_name) ??
    jiraTarget ??
    asNonEmptyString(payload.channel_name) ??
    asNonEmptyString(payload.channel) ??
    asNonEmptyString(nestedEvent?.channel) ??
    asNonEmptyString(deployment?.environment) ??
    asNonEmptyString(payload.app) ??
    `evt_${item.original_event_id.replace(/-/g, "").slice(0, 8)}`;

  const shortErrorClass =
    item.error_class.split(":")[0]?.split(".").pop()?.trim() ||
    item.error_class ||
    "WebhookError";

  const warningErrorPattern =
    /schema|validation|rate.?limit|value.?error|payload/i;
  const errorTone: "danger" | "warning" = warningErrorPattern.test(shortErrorClass)
    ? "warning"
    : "danger";

  const traceLines = (item.stack_trace || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.length > 0);

  let errorSummaryMessage = "";
  for (let i = traceLines.length - 1; i >= 0; i -= 1) {
    const line = traceLines[i];
    if (line.startsWith("Retry history:")) continue;
    const match = /^[A-Za-z0-9_.]+(?:Error|Exception|Warning)?:\s*(.+)$/.exec(line);
    if (match?.[1]) {
      errorSummaryMessage = match[1].trim();
      break;
    }
  }
  if (!errorSummaryMessage) {
    errorSummaryMessage =
      traceLines.find(
        (line) =>
          !line.startsWith("Traceback") &&
          !line.startsWith("File ") &&
          !line.startsWith("Retry history:"),
      ) ?? `${shortErrorClass} encountered during webhook processing.`;
  }

  const fileMatches = (item.stack_trace || "").match(/\bFile\s+"[^"]+"/g);
  const stackFrameCount =
    fileMatches && fileMatches.length > 0
      ? fileMatches.length
      : Math.max(1, traceLines.length);

  let payloadJson = "{}";
  try {
    payloadJson = JSON.stringify(item.payload ?? {}, null, 2);
  } catch {
    payloadJson = "{}";
  }
  const payloadBytes = new TextEncoder().encode(payloadJson).length;

  return {
    normalizedProvider,
    providerLabel,
    providerWebhookLabel,
    eventType,
    deliveryShortId,
    contextTarget,
    shortErrorClass,
    errorTone,
    errorSummaryMessage,
    stackFrameCount,
    payloadJson,
    payloadBytes,
  };
}

export async function listDeadLetters(params?: {
  status?: "unresolved" | "all";
  limit?: number;
  offset?: number;
}): Promise<DeadLetterListResponse> {
  const query = new URLSearchParams();
  query.set("status", params?.status ?? "all");
  query.set("limit", String(params?.limit ?? 200));
  query.set("offset", String(params?.offset ?? 0));
  return apiRequest<DeadLetterListResponse>(
    `/api/v1/dead-letters?${query.toString()}`,
  );
}

export async function redriveDeadLetter(id: string): Promise<RedriveResponse> {
  return apiRequest<RedriveResponse>(`/api/v1/dead-letters/${id}/redrive`, {
    method: "POST",
  });
}
