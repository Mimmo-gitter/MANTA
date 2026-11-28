import { apiRequest } from "@/lib/api-client";
import type {
  AIProcessingMode,
  TenantSettingsDraft,
  TenantSettingsResponse,
  TenantSettingsUpdatePayload,
  TenantSettingsValidationErrors,
} from "@/types/tenant-settings";

export const TENANT_RETENTION_MIN_DAYS = 7;
export const TENANT_RETENTION_MAX_DAYS = 3650;

export interface RetentionPresetOption {
  days: number;
  shortLabel: string;
  label: string;
}

export const TENANT_RETENTION_PRESETS: readonly RetentionPresetOption[] = [
  { days: 30, shortLabel: "30d", label: "30 days" },
  { days: 90, shortLabel: "90d", label: "90 days (Default)" },
  { days: 180, shortLabel: "180d", label: "180 days" },
  { days: 365, shortLabel: "1y", label: "365 days" },
  { days: 730, shortLabel: "2y", label: "730 days" },
] as const;

export const REINDEX_POLL_INTERVAL_MS = 2_500;

const ALLOWED_DOMAIN_PATTERN =
  /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$/;

export async function getTenantSettings(): Promise<TenantSettingsResponse> {
  return apiRequest<TenantSettingsResponse>("/api/v1/tenants/settings", {
    method: "GET",
  });
}

export async function updateTenantSettings(
  payload: TenantSettingsUpdatePayload,
): Promise<TenantSettingsResponse> {
  return apiRequest<TenantSettingsResponse>("/api/v1/tenants/settings", {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function normalizeAllowedDomain(raw?: string | null): string | null {
  if (typeof raw !== "string") return null;
  const cleaned = raw.trim().toLowerCase().replace(/^@+/, "");
  return cleaned.length > 0 ? cleaned : null;
}

export function normalizeOptionalText(raw?: string | null): string | null {
  if (typeof raw !== "string") return null;
  const cleaned = raw.trim();
  return cleaned.length > 0 ? cleaned : null;
}

export function parseRetentionDaysInput(raw: string): number | null {
  const trimmed = raw.trim();
  if (!/^\d+$/.test(trimmed)) {
    return null;
  }
  const parsed = Number.parseInt(trimmed, 10);
  if (!Number.isSafeInteger(parsed)) {
    return null;
  }
  return parsed;
}

export function createDraftFromSettings(
  settings: TenantSettingsResponse,
): TenantSettingsDraft {
  return {
    name: settings.name,
    allowed_domain: settings.allowed_domain ?? "",
    industry: settings.industry ?? "",
    ai_processing_mode: settings.ai_processing_mode,
    retention_days_input: String(settings.retention_days),
  };
}

export function getEditableSettingsSignature(
  settings: Pick<
    TenantSettingsResponse,
    "name" | "allowed_domain" | "industry" | "ai_processing_mode" | "retention_days"
  >,
): string {
  return JSON.stringify({
    name: settings.name.trim(),
    allowed_domain: normalizeAllowedDomain(settings.allowed_domain),
    industry: normalizeOptionalText(settings.industry),
    ai_processing_mode: settings.ai_processing_mode,
    retention_days: settings.retention_days,
  });
}

export function validateTenantSettingsDraft(
  draft: TenantSettingsDraft,
): TenantSettingsValidationErrors {
  const errors: TenantSettingsValidationErrors = {};

  const trimmedName = draft.name.trim();
  if (trimmedName.length === 0) {
    errors.name = "Organization name is required.";
  } else if (trimmedName.length < 2) {
    errors.name = "Organization name must be at least 2 characters.";
  } else if (trimmedName.length > 255) {
    errors.name = "Organization name must be 255 characters or fewer.";
  }

  const normalizedDomain = normalizeAllowedDomain(draft.allowed_domain);
  if (normalizedDomain !== null) {
    if (normalizedDomain.length > 255) {
      errors.allowed_domain = "Allowed domain must be 255 characters or fewer.";
    } else if (!ALLOWED_DOMAIN_PATTERN.test(normalizedDomain)) {
      errors.allowed_domain =
        "Enter a valid domain such as company.com without https:// or spaces.";
    }
  }

  const normalizedIndustry = normalizeOptionalText(draft.industry);
  if (normalizedIndustry !== null && normalizedIndustry.length > 255) {
    errors.industry = "Industry must be 255 characters or fewer.";
  }

  const parsedRetention = parseRetentionDaysInput(draft.retention_days_input);
  if (parsedRetention === null) {
    errors.retention_days = `Enter a whole number of days between ${TENANT_RETENTION_MIN_DAYS} and ${TENANT_RETENTION_MAX_DAYS}.`;
  } else if (
    parsedRetention < TENANT_RETENTION_MIN_DAYS ||
    parsedRetention > TENANT_RETENTION_MAX_DAYS
  ) {
    errors.retention_days = `Retention period must be between ${TENANT_RETENTION_MIN_DAYS} and ${TENANT_RETENTION_MAX_DAYS} days.`;
  }

  return errors;
}

/**
 * Builds a minimal, diff-only PATCH payload strictly conforming to
 * backend `TenantSettingsUpdateRequest` (`extra="forbid"`, no explicit `null`
 * for required fields `name`, `ai_processing_mode`, `retention_days`).
 */
export function buildTenantSettingsPatch(
  current: TenantSettingsResponse,
  draft: TenantSettingsDraft,
): TenantSettingsUpdatePayload {
  const patch: TenantSettingsUpdatePayload = {};

  const nextName = draft.name.trim();
  if (nextName.length >= 2 && nextName !== current.name) {
    patch.name = nextName;
  }

  const nextDomain = normalizeAllowedDomain(draft.allowed_domain);
  const currentDomain = normalizeAllowedDomain(current.allowed_domain);
  if (nextDomain !== currentDomain) {
    patch.allowed_domain = nextDomain;
  }

  const nextIndustry = normalizeOptionalText(draft.industry);
  const currentIndustry = normalizeOptionalText(current.industry);
  if (nextIndustry !== currentIndustry) {
    patch.industry = nextIndustry;
  }

  if (draft.ai_processing_mode !== current.ai_processing_mode) {
    patch.ai_processing_mode = draft.ai_processing_mode;
  }

  const parsedRetention = parseRetentionDaysInput(draft.retention_days_input);
  if (
    parsedRetention !== null &&
    parsedRetention >= TENANT_RETENTION_MIN_DAYS &&
    parsedRetention <= TENANT_RETENTION_MAX_DAYS &&
    parsedRetention !== current.retention_days
  ) {
    patch.retention_days = parsedRetention;
  }

  return patch;
}

export function hasTenantSettingsChanges(
  current: TenantSettingsResponse | undefined,
  draft: TenantSettingsDraft,
): boolean {
  if (!current) return false;
  if (draft.name.trim() !== current.name) return true;
  if (
    normalizeAllowedDomain(draft.allowed_domain) !==
    normalizeAllowedDomain(current.allowed_domain)
  ) {
    return true;
  }
  if (
    normalizeOptionalText(draft.industry) !==
    normalizeOptionalText(current.industry)
  ) {
    return true;
  }
  if (draft.ai_processing_mode !== current.ai_processing_mode) return true;

  const parsedRetention = parseRetentionDaysInput(draft.retention_days_input);
  if (parsedRetention === null) {
    return draft.retention_days_input.trim() !== String(current.retention_days);
  }
  return parsedRetention !== current.retention_days;
}

export function isReindexActive(
  settings?: Pick<TenantSettingsResponse, "reindexing" | "reindex_status"> | null,
): boolean {
  if (!settings) return false;
  if (settings.reindexing) return true;
  const normalizedStatus = settings.reindex_status.trim().toLowerCase();
  return normalizedStatus === "queued" || normalizedStatus === "running";
}

export function getReindexProgressPercent(
  reindexedCount: number,
  totalCount: number,
): number | null {
  if (!Number.isFinite(totalCount) || totalCount <= 0) {
    return null;
  }
  const safeDone = Math.max(0, Math.min(reindexedCount, totalCount));
  return Math.round((safeDone / totalCount) * 100);
}

export function formatRetentionDuration(days: number): string {
  if (!Number.isFinite(days) || days < 1) {
    return "Invalid retention window";
  }
  if (days < 30) {
    const weeks = Math.round((days / 7) * 10) / 10;
    return `${days} days (~${weeks} ${weeks === 1 ? "week" : "weeks"})`;
  }
  if (days < 365) {
    const months = Math.round((days / 30) * 10) / 10;
    return `${days} days (~${months} ${months === 1 ? "month" : "months"})`;
  }
  const years = Math.round((days / 365) * 10) / 10;
  return `${days} days (~${years} ${years === 1 ? "year" : "years"})`;
}

export function getAiModeLabel(mode: AIProcessingMode): string {
  return mode === "local_only" ? "Local-Only Sovereign" : "Cloud ZDR";
}
