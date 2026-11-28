"use client";

import Link from "next/link";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  AlertTriangle,
  Building2,
  CheckCircle2,
  Cloud,
  Cpu,
  Database,
  Loader2,
  Lock,
  RefreshCw,
  ShieldCheck,
  X,
} from "lucide-react";
import { ReindexStatusBanner } from "@/components/workspace-settings/reindex-status-banner";
import { usePermissions } from "@/hooks/use-permissions";
import {
  useTenantSettingsQuery,
  useUpdateTenantSettingsMutation,
} from "@/hooks/use-tenant-settings";
import { isApiHttpError } from "@/lib/api-client";
import { getPrimaryRoleLabel } from "@/lib/permissions";
import { formatRelativeTime } from "@/lib/projects-api";
import {
  TENANT_RETENTION_MAX_DAYS,
  TENANT_RETENTION_MIN_DAYS,
  TENANT_RETENTION_PRESETS,
  buildTenantSettingsPatch,
  createDraftFromSettings,
  formatRetentionDuration,
  getAiModeLabel,
  getEditableSettingsSignature,
  hasTenantSettingsChanges,
  isReindexActive,
  parseRetentionDaysInput,
  validateTenantSettingsDraft,
} from "@/lib/tenant-settings-api";
import type {
  AIProcessingMode,
  TenantSettingsDraft,
  TenantSettingsValidationErrors,
} from "@/types/tenant-settings";

interface ToastState {
  title: string;
  description: string;
}

type EditableFieldKey = keyof TenantSettingsValidationErrors;

interface SpecRow {
  label: string;
  value: string;
  valueClassName?: string;
}

const EMPTY_DRAFT: TenantSettingsDraft = {
  name: "",
  allowed_domain: "",
  industry: "",
  ai_processing_mode: "cloud_zdr",
  retention_days_input: "90",
};

const RETENTION_SCALE_TICKS = ["7d", "90d", "180d", "1y", "10y"] as const;

function computeRetentionScalePercent(days: number | null): number {
  if (
    days === null ||
    days < TENANT_RETENTION_MIN_DAYS ||
    days > TENANT_RETENTION_MAX_DAYS
  ) {
    return 3;
  }
  const normalized =
    Math.log(days / TENANT_RETENTION_MIN_DAYS) /
    Math.log(TENANT_RETENTION_MAX_DAYS / TENANT_RETENTION_MIN_DAYS);
  return Math.max(6, Math.min(100, Math.round(normalized * 100)));
}

function announceWithAriaNotify(message: string): void {
  if (typeof document === "undefined") return;
  const docWithNotify = document as Document & {
    ariaNotify?: (text: string) => void;
  };
  docWithNotify.ariaNotify?.(message);
}

function WorkspaceSettingsHeader({
  action,
}: {
  action?: React.ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <nav aria-label="Breadcrumb">
          <ol className="flex items-center gap-1.5 font-mono text-[10.5px] leading-[14px] text-[#7c839b]">
            <li>Administration</li>
            <li aria-hidden="true" className="text-[#475569]">
              /
            </li>
            <li aria-current="page" className="font-medium text-[#00e599]">
              Workspace Settings
            </li>
          </ol>
        </nav>

        <h1
          id="workspace-settings-heading"
          className="mt-[3px] text-[20px] font-bold leading-[24px] tracking-[-0.02em] text-[#f8fafc]"
        >
          Workspace Settings
        </h1>
        <p className="mt-[3px] text-[11.5px] leading-[15px] text-[#7c839b]">
          Configure AI compliance processing, live ADR vector indexing, and
          tenant retention policies.
        </p>
      </div>
      {action}
    </header>
  );
}

function WorkspaceSettingsForbiddenState({ roleLabel }: { roleLabel: string }) {
  return (
    <section
      data-testid="workspace-settings-forbidden-state"
      aria-labelledby="workspace-settings-forbidden-heading"
      className="flex min-h-[480px] flex-col items-center justify-center rounded-[12px] border border-[#1e2636] bg-[#0f1422] px-6 py-14 text-center"
    >
      <div className="flex size-[54px] items-center justify-center rounded-[12px] border border-[#ef4444]/40 bg-[#2a1216] text-[#ef4444]">
        <Lock className="size-[22px]" aria-hidden="true" />
      </div>

      <span className="mt-[14px] inline-flex items-center rounded-full border border-[#ef4444]/40 bg-[#2a1216] px-3 py-1 font-mono text-[10px] font-semibold tracking-[0.06em] text-[#f87171]">
        HTTP 403 · ACCESS RESTRICTED
      </span>

      <h2
        id="workspace-settings-forbidden-heading"
        className="mt-[14px] text-[18px] font-semibold leading-[24px] text-[#e2e8f0]"
      >
        Administrator access required
      </h2>

      <p className="mt-[8px] max-w-[480px] text-[12px] leading-[18px] text-[#7c839b]">
        Only Tenant Administrators can view or modify workspace AI compliance
        modes, trigger ADR vector re-indexing, or update tenant retention
        policies. Contact your workspace Tenant Administrator for access.
      </p>

      <div className="mt-[18px] inline-flex items-center gap-1.5 rounded-[8px] border border-[#252b3b] bg-[#0a0e18] px-3.5 py-1.5 font-mono text-[11px]">
        <span className="text-[#7c839b]">Current workspace role:</span>
        <span className="font-semibold text-[#e2e8f0]">{roleLabel}</span>
      </div>

      <div className="mt-[22px] flex items-center justify-center gap-3">
        <Link
          href="/"
          data-testid="workspace-settings-back-to-overview"
          className="inline-flex h-[36px] items-center justify-center rounded-[8px] bg-[#00e599] px-4 text-[12px] font-semibold text-[#0a0e18] transition-colors hover:bg-[#2df0b0] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#00e599]"
        >
          Back to Overview
        </Link>
      </div>
    </section>
  );
}

function WorkspaceSettingsLoadingSkeleton() {
  return (
    <div
      role="status"
      data-testid="workspace-settings-loading"
      className="flex flex-1 flex-col gap-[11px]"
    >
      <span className="sr-only">Loading workspace settings</span>
      <div
        aria-hidden="true"
        className="h-[36px] animate-pulse rounded-[8px] border border-[#1e2636] bg-[#0e1320]"
      />
      <div
        aria-hidden="true"
        className="grid flex-1 grid-cols-1 gap-[14px] lg:grid-cols-[minmax(0,626fr)_minmax(330px,374fr)]"
      >
        <div className="min-h-[380px] animate-pulse rounded-[12px] border border-[#1e2636] bg-[#0f1422]" />
        <div className="flex flex-col gap-[12px]">
          <div className="h-[190px] animate-pulse rounded-[12px] border border-[#1e2636] bg-[#0f1422]" />
          <div className="min-h-[210px] flex-1 animate-pulse rounded-[12px] border border-[#1e2636] bg-[#0f1422]" />
        </div>
      </div>
      <div
        aria-hidden="true"
        className="h-[42px] animate-pulse rounded-[10px] border border-[#1e2636] bg-[#0e1320]"
      />
    </div>
  );
}

export function WorkspaceSettingsView() {
  const { can, roles } = usePermissions();
  const canManageSettings = can("MANAGE_TENANT_USERS_AND_ROLES");
  const roleLabel = getPrimaryRoleLabel(roles);

  const settingsQuery = useTenantSettingsQuery(canManageSettings);
  const updateMutation = useUpdateTenantSettingsMutation();

  const [draft, setDraft] = useState<TenantSettingsDraft>(EMPTY_DRAFT);
  const [touched, setTouched] = useState<Partial<Record<EditableFieldKey, boolean>>>(
    {},
  );
  const [hasAttemptedSubmit, setHasAttemptedSubmit] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [forbiddenFromApi, setForbiddenFromApi] = useState(false);
  const [toast, setToast] = useState<ToastState | null>(null);
  const [toastPaused, setToastPaused] = useState(false);
  const [liveAnnouncement, setLiveAnnouncement] = useState("");
  const [dismissedReindexJobKey, setDismissedReindexJobKey] = useState<
    string | null
  >(null);

  const hydratedTenantIdRef = useRef<string | null>(null);
  const lastServerSigRef = useRef<string | null>(null);
  const wasReindexingRef = useRef<boolean>(false);

  const retentionInputRef = useRef<HTMLInputElement>(null);
  const nameInputRef = useRef<HTMLInputElement>(null);
  const domainInputRef = useRef<HTMLInputElement>(null);
  const industryInputRef = useRef<HTMLInputElement>(null);

  const idPrefix = useId();
  const cloudModeId = `${idPrefix}-mode-cloud-zdr`;
  const localModeId = `${idPrefix}-mode-local-only`;
  const retentionId = `${idPrefix}-retention-days`;
  const retentionHintId = `${idPrefix}-retention-hint`;
  const retentionErrorId = `${idPrefix}-retention-error`;
  const nameId = `${idPrefix}-org-name`;
  const nameHintId = `${idPrefix}-org-name-hint`;
  const nameErrorId = `${idPrefix}-org-name-error`;
  const domainId = `${idPrefix}-allowed-domain`;
  const domainHintId = `${idPrefix}-allowed-domain-hint`;
  const domainErrorId = `${idPrefix}-allowed-domain-error`;
  const industryId = `${idPrefix}-industry`;
  const industryErrorId = `${idPrefix}-industry-error`;

  const settings = settingsQuery.data;
  const isDirty = useMemo(
    () => hasTenantSettingsChanges(settings, draft),
    [settings, draft],
  );
  const allValidationErrors = useMemo(
    () => validateTenantSettingsDraft(draft),
    [draft],
  );

  // Sync server settings into form draft only on initial load, tenant change,
  // or external update while clean — never clobber unsaved edits during polling.
  useEffect(() => {
    if (!settings) return;
    const nextSig = getEditableSettingsSignature(settings);
    const tenantChanged = hydratedTenantIdRef.current !== settings.tenant_id;
    const serverSnapshotChanged = lastServerSigRef.current !== nextSig;

    if (tenantChanged || (!isDirty && serverSnapshotChanged)) {
      setDraft(createDraftFromSettings(settings));
      setTouched({});
      setHasAttemptedSubmit(false);
      hydratedTenantIdRef.current = settings.tenant_id;
      lastServerSigRef.current = nextSig;
    }
  }, [settings, isDirty]);

  // Announce active -> completed transition once via the centralized polite live region.
  useEffect(() => {
    if (!settings) return;
    const activeNow = isReindexActive(settings);
    const normalizedStatus = settings.reindex_status.trim().toLowerCase();
    if (wasReindexingRef.current && !activeNow) {
      if (normalizedStatus === "completed") {
        setLiveAnnouncement(
          `ADR vector index rebuild completed for ${getAiModeLabel(settings.ai_processing_mode)}.`,
        );
      } else if (normalizedStatus === "failed") {
        setLiveAnnouncement("ADR vector index rebuild failed.");
      }
    }
    wasReindexingRef.current = activeNow;
  }, [settings]);

  useEffect(() => {
    if (!toast || toastPaused) return;
    const timer = window.setTimeout(() => setToast(null), 6000);
    return () => window.clearTimeout(timer);
  }, [toast, toastPaused]);

  const queryReturnedForbidden =
    settingsQuery.isError &&
    isApiHttpError(settingsQuery.error) &&
    settingsQuery.error.status === 403;

  const isAccessForbidden =
    !canManageSettings || forbiddenFromApi || queryReturnedForbidden;

  const isFieldErrorVisible = (field: EditableFieldKey): boolean =>
    Boolean(
      allValidationErrors[field] && (hasAttemptedSubmit || touched[field]),
    );

  const visibleErrorCount = (
    ["retention_days", "name", "allowed_domain", "industry"] as const
  ).filter((field) => isFieldErrorVisible(field)).length;

  const parsedRetentionDays = parseRetentionDaysInput(draft.retention_days_input);
  const retentionValid =
    parsedRetentionDays !== null &&
    parsedRetentionDays >= TENANT_RETENTION_MIN_DAYS &&
    parsedRetentionDays <= TENANT_RETENTION_MAX_DAYS;
  const retentionScalePercent = computeRetentionScalePercent(parsedRetentionDays);
  const retentionHasError = isFieldErrorVisible("retention_days");
  const domainHasError = isFieldErrorVisible("allowed_domain");
  const orgCardHasError =
    isFieldErrorVisible("name") ||
    domainHasError ||
    isFieldErrorVisible("industry");

  const modeWillChange =
    settings !== undefined &&
    draft.ai_processing_mode !== settings.ai_processing_mode;
  const reindexCurrentlyActive = isReindexActive(settings);
  const isWanPolicyError = Boolean(
    submitError &&
      /local_only|local endpoint|wan|rfc1918|outbound|400/i.test(submitError),
  );

  const handleModeChange = (mode: AIProcessingMode) => {
    if (!canManageSettings || isAccessForbidden) return;
    setDraft((prev) => ({ ...prev, ai_processing_mode: mode }));
    setSubmitError(null);
  };

  const handleRetentionPresetClick = (days: number) => {
    if (!canManageSettings || isAccessForbidden) return;
    setDraft((prev) => ({ ...prev, retention_days_input: String(days) }));
    setTouched((prev) => ({ ...prev, retention_days: true }));
    setSubmitError(null);
  };

  const handleDiscard = () => {
    if (!settings) return;
    setDraft(createDraftFromSettings(settings));
    setTouched({});
    setHasAttemptedSubmit(false);
    setSubmitError(null);
  };

  const focusFirstInvalidField = (errors: TenantSettingsValidationErrors) => {
    if (errors.retention_days) {
      retentionInputRef.current?.focus();
      return;
    }
    if (errors.name) {
      nameInputRef.current?.focus();
      return;
    }
    if (errors.allowed_domain) {
      domainInputRef.current?.focus();
      return;
    }
    if (errors.industry) {
      industryInputRef.current?.focus();
    }
  };

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (
      !settings ||
      !canManageSettings ||
      isAccessForbidden ||
      updateMutation.isPending
    ) {
      return;
    }

    setHasAttemptedSubmit(true);
    setSubmitError(null);

    const validationErrors = validateTenantSettingsDraft(draft);
    if (Object.keys(validationErrors).length > 0) {
      setTouched({
        name: true,
        allowed_domain: true,
        industry: true,
        retention_days: true,
      });
      focusFirstInvalidField(validationErrors);
      setLiveAnnouncement("Please correct the highlighted validation errors.");
      return;
    }

    const patch = buildTenantSettingsPatch(settings, draft);
    if (Object.keys(patch).length === 0) {
      return;
    }

    const switchingMode = patch.ai_processing_mode !== undefined;

    try {
      const updated = await updateMutation.mutateAsync(patch);
      setDraft(createDraftFromSettings(updated));
      setTouched({});
      setHasAttemptedSubmit(false);
      hydratedTenantIdRef.current = updated.tenant_id;
      lastServerSigRef.current = getEditableSettingsSignature(updated);

      if (switchingMode) {
        setDismissedReindexJobKey(null);
        const targetLabel = getAiModeLabel(updated.ai_processing_mode);
        const message = isReindexActive(updated)
          ? `Switched AI compliance mode to ${targetLabel} (${updated.active_embedding_dimensions}d). Background ADR re-indexing has been queued.`
          : `Switched AI compliance mode to ${targetLabel} (${updated.active_embedding_dimensions}d).`;
        setToast({
          title: "AI compliance mode updated",
          description: message,
        });
        setToastPaused(false);
        setLiveAnnouncement(message);
        announceWithAriaNotify(`AI compliance mode updated. ${message}`);
      } else {
        const message =
          "Workspace retention and profile settings have been saved and recorded in the audit log.";
        setToast({
          title: "Workspace settings saved",
          description: message,
        });
        setToastPaused(false);
        setLiveAnnouncement(message);
        announceWithAriaNotify(`Workspace settings saved. ${message}`);
      }
    } catch (error) {
      if (isApiHttpError(error) && error.status === 403) {
        setForbiddenFromApi(true);
        setSubmitError(
          "Only Tenant Administrators are permitted to update workspace settings.",
        );
        return;
      }
      const message = isApiHttpError(error)
        ? error.detail || error.message
        : error instanceof Error
          ? error.message
          : "Failed to save workspace settings.";
      setSubmitError(message);
      setLiveAnnouncement(message);
    }
  };

  const headerAction =
    settings && !isAccessForbidden ? (
      <div className="flex flex-wrap items-center gap-[8px]">
        <span
          data-testid="workspace-tier-badge"
          className="rounded-[5px] border border-[#252b3b] bg-[#111622] px-[8px] py-[4px] font-mono text-[9.5px] font-medium uppercase tracking-[0.04em] text-[#cbd5e1]"
        >
          Tier: {settings.tier}
        </span>
        <span
          data-testid="workspace-updated-at"
          className="rounded-[5px] border border-[#1e2636] bg-[#0d111b] px-[8px] py-[4px] font-mono text-[9.5px] text-[#7c839b]"
        >
          Synced {formatRelativeTime(settings.updated_at)}
        </span>
        <button
          type="button"
          onClick={() => void settingsQuery.refetch()}
          disabled={settingsQuery.isFetching}
          data-testid="workspace-settings-refresh-button"
          className="inline-flex h-[28px] items-center gap-[6px] rounded-[7px] border border-[#252b3b] bg-[#131722] px-[10px] text-[11px] font-medium text-[#cbd5e1] transition-colors hover:border-[#334155] hover:text-[#f8fafc] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#00e599] disabled:opacity-60"
        >
          <RefreshCw
            className={`size-[12px] ${settingsQuery.isFetching ? "animate-spin" : ""}`}
            aria-hidden="true"
          />
          <span>Refresh</span>
        </button>
      </div>
    ) : undefined;

  const cloudSpecs: readonly SpecRow[] = [
    {
      label: "VECTORS",
      value: "1536d · embedding_1536",
      valueClassName: "font-medium text-[#e2e8f0]",
    },
    {
      label: "LLM ENGINE",
      value:
        settings?.ai_processing_mode === "cloud_zdr"
          ? settings.active_llm_model
          : "gpt-4o-mini (ZDR)",
      valueClassName: "text-[#cbd5e1]",
    },
    {
      label: "EMBEDDING",
      value:
        settings?.ai_processing_mode === "cloud_zdr"
          ? settings.active_embedding_model
          : "text-embedding-3-small",
      valueClassName: "text-[#94a3b8]",
    },
    {
      label: "NETWORK",
      value: "X-MANTA-ZDR: enforced",
      valueClassName: "font-medium text-[#00e599]",
    },
    {
      label: "GUARANTEE",
      value: "SOC 2 · 0-Day Training",
      valueClassName: "text-[#7dd3fc]",
    },
  ];

  const localSpecs: readonly SpecRow[] = [
    {
      label: "VECTORS",
      value: "384d · embedding_384",
      valueClassName: "font-medium text-[#e2e8f0]",
    },
    {
      label: "LLM ENGINE",
      value: isWanPolicyError
        ? "WAN Endpoint (DENIED)"
        : settings?.ai_processing_mode === "local_only"
          ? settings.active_llm_model
          : "llama3.1:8b (Ollama)",
      valueClassName: isWanPolicyError
        ? "font-medium text-[#f87171]"
        : "text-[#cbd5e1]",
    },
    {
      label: "EMBEDDING",
      value:
        settings?.ai_processing_mode === "local_only"
          ? settings.active_embedding_model
          : "bge-small-en-v1.5",
      valueClassName: "text-[#94a3b8]",
    },
    {
      label: "NETWORK",
      value: "Fail-Closed · No WAN",
      valueClassName: isWanPolicyError
        ? "font-medium text-[#ef4444]"
        : "font-medium text-[#fbbf24]",
    },
    {
      label: "GUARANTEE",
      value: "Air-Gap · EU/GDPR Ready",
      valueClassName: "text-[#00e599]",
    },
  ];

  const reindexFailed =
    settings?.reindex_status.trim().toLowerCase() === "failed";

  const footerStatus = (() => {
    if (updateMutation.isPending) {
      return {
        dotClass: "bg-[#38bdf8]",
        textClass: "text-[#7dd3fc]",
        label: "Saving workspace settings...",
      };
    }
    if (submitError) {
      return {
        dotClass: "bg-[#ef4444]",
        textClass: "text-[#fca5a5]",
        label: isWanPolicyError
          ? "Save blocked · Backend rejected non-local WAN endpoint (HTTP 400)"
          : "Save blocked · Review workspace error details above",
      };
    }
    if (visibleErrorCount > 0) {
      return {
        dotClass: "bg-[#ef4444]",
        textClass: "text-[#fca5a5]",
        label: `Fix ${visibleErrorCount} highlighted validation ${
          visibleErrorCount === 1 ? "error" : "errors"
        } before saving`,
      };
    }
    if (modeWillChange) {
      return {
        dotClass: "bg-[#f59e0b]",
        textClass: "text-[#fbbf24]",
        label: `Unsaved changes (${
          isDirty &&
          (draft.retention_days_input.trim() !==
            String(settings?.retention_days ?? "") ||
            draft.name.trim() !== (settings?.name ?? ""))
            ? "AI mode + Policy"
            : "AI mode switch"
        }) · Saving queues adr.reembed`,
      };
    }
    if (isDirty) {
      return {
        dotClass: "bg-[#f59e0b]",
        textClass: "text-[#fbbf24]",
        label: "Unsaved workspace setting changes",
      };
    }
    if (reindexCurrentlyActive && settings) {
      const counts =
        settings.reindex_total_count > 0
          ? ` (${settings.reindexed_count} / ${settings.reindex_total_count})`
          : "";
      return {
        dotClass: "bg-[#38bdf8]",
        textClass: "text-[#7dd3fc]",
        label: `Settings synced · Background ADR re-indexing active${counts}`,
      };
    }
    if (reindexFailed) {
      return {
        dotClass: "bg-[#ef4444]",
        textClass: "text-[#fca5a5]",
        label:
          "Settings synced · Last adr.reembed worker job failed — verify provider connectivity",
      };
    }
    return {
      dotClass: "bg-[#00e599]",
      textClass: "text-[#00e599]",
      label: "Workspace settings synchronized · PATCH /api/v1/tenants/settings",
    };
  })();

  return (
    <div className="flex min-h-full w-full flex-col bg-[#0a0e18] px-4 py-4 sm:px-6">
      <div className="mx-auto flex w-full max-w-[1160px] flex-1 flex-col gap-[11px]">
        <WorkspaceSettingsHeader action={headerAction} />

        {/* Pre-mounted polite live region fallback for screen reader announcements */}
        <div className="sr-only" aria-live="polite" aria-atomic="true">
          {liveAnnouncement}
        </div>

        {isAccessForbidden ? (
          <WorkspaceSettingsForbiddenState roleLabel={roleLabel} />
        ) : settingsQuery.isLoading && !settings ? (
          <WorkspaceSettingsLoadingSkeleton />
        ) : settingsQuery.isError && !settings ? (
          <section
            role="alert"
            data-testid="workspace-settings-load-error"
            className="rounded-[12px] border border-[#ef4444]/40 bg-[#180d12] p-[20px]"
          >
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div className="flex items-start gap-3">
                <AlertCircle
                  className="mt-[2px] size-[18px] shrink-0 text-[#ef4444]"
                  aria-hidden="true"
                />
                <div>
                  <h2 className="text-[15px] font-semibold text-[#fef2f2]">
                    Unable to load workspace settings
                  </h2>
                  <p className="mt-1 text-[12px] leading-[18px] text-[#fecaca]">
                    {settingsQuery.error instanceof Error
                      ? settingsQuery.error.message
                      : "The workspace settings service could not be reached."}
                  </p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => void settingsQuery.refetch()}
                data-testid="workspace-settings-retry-button"
                className="inline-flex h-[32px] items-center gap-1.5 rounded-[7px] border border-[#ef4444]/40 bg-[#ef4444]/15 px-[12px] text-[12px] font-medium text-[#fef2f2] transition-colors hover:bg-[#ef4444]/25 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#ef4444]"
              >
                <RefreshCw className="size-[13px]" aria-hidden="true" />
                <span>Retry</span>
              </button>
            </div>
          </section>
        ) : settings ? (
          <>
            <ReindexStatusBanner
              settings={settings}
              isFetching={settingsQuery.isFetching}
              dismissedJobKey={dismissedReindexJobKey}
              onRefresh={() => void settingsQuery.refetch()}
              onDismiss={(jobKey) => setDismissedReindexJobKey(jobKey)}
            />

            {/* Compact Live Telemetry Strip (Figma 8A / 8B / 8E / 8G — always mounted for telemetry & E2E parity) */}
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-[8px] border border-[#1e2636] bg-[#0e1320] px-[14px] py-[6px]">
              <div className="flex flex-wrap items-center gap-x-[10px] gap-y-1 font-mono text-[10.5px]">
                <span
                  data-testid="active-ai-mode-pill"
                  className="inline-flex shrink-0 items-center gap-[5px] rounded-full border border-[#00e599]/35 bg-[#062a1c] px-[8px] py-[2.5px] text-[9.5px] font-bold uppercase tracking-[0.04em] text-[#00e599]"
                >
                  <span
                    className="size-[5px] rounded-full bg-[#00e599]"
                    aria-hidden="true"
                  />
                  <span>
                    Active: {settings.ai_processing_mode} (
                    {settings.active_embedding_dimensions}d)
                  </span>
                </span>

                <span aria-hidden="true" className="text-[#334155]">
                  ·
                </span>
                <div className="flex items-center gap-1.5">
                  <span className="text-[#64748b]">LLM:</span>
                  <span
                    data-testid="active-llm-model"
                    className="font-medium text-[#e2e8f0]"
                    title={settings.active_llm_model}
                  >
                    {settings.active_llm_model}
                  </span>
                </div>

                <span aria-hidden="true" className="text-[#334155]">
                  ·
                </span>
                <div className="flex items-center gap-1.5">
                  <span className="text-[#64748b]">Embed:</span>
                  <span
                    data-testid="active-embedding-model"
                    className="font-medium text-[#e2e8f0]"
                    title={settings.active_embedding_model}
                  >
                    {settings.active_embedding_model}
                  </span>
                </div>

                <span aria-hidden="true" className="text-[#334155]">
                  ·
                </span>
                <div className="flex items-center gap-1.5">
                  <span className="text-[#64748b]">HNSW:</span>
                  <span
                    data-testid="active-embedding-dimensions"
                    className="font-medium text-[#38bdf8]"
                  >
                    {settings.ai_processing_mode === "local_only"
                      ? "embedding_384"
                      : "embedding_1536"}
                  </span>
                </div>
              </div>

              <div className="flex items-center gap-[6px] font-mono text-[10px]">
                <span
                  className={`size-[5px] rounded-full ${
                    isWanPolicyError ? "bg-[#ef4444]" : "bg-[#00e599]"
                  }`}
                  aria-hidden="true"
                />
                <span
                  className={
                    isWanPolicyError ? "text-[#fca5a5]" : "text-[#64748b]"
                  }
                >
                  {isWanPolicyError
                    ? "Fail-Closed Guard Active"
                    : "Audit Trail Enabled"}
                </span>
              </div>
            </div>

            <form
              aria-labelledby="workspace-settings-heading"
              onSubmit={handleSubmit}
              noValidate
              data-testid="workspace-settings-form"
              className="flex flex-1 flex-col gap-[11px]"
            >
              {/* Main 2-Column Bento Control Plane Grid */}
              <div className="grid flex-1 grid-cols-1 gap-[14px] lg:grid-cols-[minmax(0,626fr)_minmax(330px,374fr)]">
                {/* LEFT COLUMN: AI Compliance Processing Mode Card */}
                <section
                  className={`flex flex-col justify-between gap-[11px] rounded-[12px] border bg-[#0f1422] p-[15px] ${
                    isWanPolicyError
                      ? "border-[#ef4444]/50"
                      : "border-[#1e2636]"
                  }`}
                >
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-[7px]">
                        <ShieldCheck
                          className={`size-[15px] shrink-0 ${
                            isWanPolicyError
                              ? "text-[#f87171]"
                              : "text-[#00e599]"
                          }`}
                          aria-hidden="true"
                        />
                        <h2 className="text-[14.5px] font-semibold leading-[19px] text-[#f8fafc]">
                          AI Compliance Processing Mode
                        </h2>
                      </div>
                      <p className="mt-[3px] text-[11px] leading-[15px] text-[#94a3b8]">
                        Control outbound AI telemetry boundaries and active
                        pgvector HNSW space.
                      </p>
                    </div>

                    {isWanPolicyError ? (
                      <span className="inline-flex shrink-0 items-center gap-[5px] rounded-[5px] border border-[#ef4444]/50 bg-[#2b1018] px-[7px] py-[3px] font-mono text-[9.5px] font-bold uppercase tracking-[0.04em] text-[#fca5a5]">
                        <span
                          className="size-[5px] rounded-full bg-[#ef4444]"
                          aria-hidden="true"
                        />
                        <span>Blocked · HTTP 400</span>
                      </span>
                    ) : modeWillChange ? (
                      <span className="inline-flex shrink-0 items-center gap-[5px] rounded-[5px] border border-[#f59e0b]/45 bg-[#261a08] px-[7px] py-[3px] font-mono text-[9.5px] font-bold uppercase tracking-[0.04em] text-[#fbbf24]">
                        <span
                          className="size-[5px] rounded-full bg-[#fbbf24]"
                          aria-hidden="true"
                        />
                        <span>Switching → {draft.ai_processing_mode}</span>
                      </span>
                    ) : (
                      <span className="inline-flex shrink-0 items-center gap-[5px] rounded-[5px] border border-[#00e599]/35 bg-[#062a1c] px-[7px] py-[3px] font-mono text-[9.5px] font-bold uppercase tracking-[0.04em] text-[#00e599]">
                        <span
                          className="size-[5px] rounded-full bg-[#00e599]"
                          aria-hidden="true"
                        />
                        <span>Active: {settings.ai_processing_mode}</span>
                      </span>
                    )}
                  </div>

                  {/* Side-by-Side Comparison Cards */}
                  <fieldset
                    className="grid min-w-0 flex-1 grid-cols-1 items-stretch gap-[12px] sm:grid-cols-2 [min-inline-size:0]"
                    disabled={!canManageSettings || updateMutation.isPending}
                  >
                    <legend className="sr-only">
                      Select AI compliance processing mode
                    </legend>

                    {/* Cloud ZDR Option Card */}
                    <label
                      htmlFor={cloudModeId}
                      data-testid="ai-mode-card-cloud_zdr"
                      className={`flex min-w-0 cursor-pointer flex-col justify-between gap-[10px] rounded-[10px] border p-[13px] transition-colors ${
                        draft.ai_processing_mode === "cloud_zdr"
                          ? "border-[1.5px] border-[#00e599] bg-[#081822]"
                          : "border-[#1e2636] bg-[#0a0e18] hover:border-[#2d3a52]"
                      }`}
                    >
                      <div className="space-y-[8px]">
                        <div className="flex items-center justify-between gap-2">
                          <div className="flex items-center gap-[8px]">
                            <input
                              id={cloudModeId}
                              type="radio"
                              name="ai_processing_mode"
                              value="cloud_zdr"
                              checked={draft.ai_processing_mode === "cloud_zdr"}
                              onChange={() => handleModeChange("cloud_zdr")}
                              data-testid="ai-mode-option-cloud_zdr"
                              className="size-[16px] shrink-0 cursor-pointer appearance-none rounded-full border-[1.5px] border-solid border-[#475569] bg-[#090d16] transition-colors checked:border-[#00e599] checked:bg-[#00e599] checked:shadow-[inset_0_0_0_3.5px_#062a1c] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#00e599] disabled:cursor-not-allowed"
                            />
                            <div className="flex size-[26px] shrink-0 items-center justify-center rounded-[7px] border border-[#38bdf8]/35 bg-[#0c2233] text-[#38bdf8]">
                              <Cloud
                                className="size-[14px]"
                                aria-hidden="true"
                              />
                            </div>
                          </div>

                          {settings.ai_processing_mode === "cloud_zdr" ? (
                            <span className="inline-flex items-center gap-[5px] rounded-[5px] border border-[#00e599]/40 bg-[#062a1c] px-[7px] py-[2.5px] font-mono text-[9.5px] font-bold uppercase tracking-[0.04em] text-[#00e599]">
                              <span
                                className="size-[5px] rounded-full bg-[#00e599]"
                                aria-hidden="true"
                              />
                              <span>Live Mode</span>
                            </span>
                          ) : (
                            <span className="inline-flex items-center rounded-[5px] border border-[#38bdf8]/40 bg-[#0c2233] px-[7px] py-[2.5px] font-mono text-[9.5px] font-bold uppercase tracking-[0.04em] text-[#7dd3fc]">
                              1536d ZDR
                            </span>
                          )}
                        </div>

                        <div>
                          <div className="flex flex-wrap items-center gap-[6px]">
                            <span className="text-[13.5px] font-bold leading-[18px] text-[#f8fafc]">
                              Cloud ZDR
                            </span>
                            <code className="rounded-[4px] border border-[#252f44] bg-[#131a29] px-[6px] py-[1.5px] font-mono text-[9px] font-medium text-[#7dd3fc]">
                              cloud_zdr
                            </code>
                          </div>
                          <p className="mt-[4px] text-[11px] leading-[15px] text-[#94a3b8]">
                            Zero-retention enterprise cloud inference with strict
                            no-training headers.
                          </p>
                        </div>
                      </div>

                      <dl className="divide-y divide-[#131a2a] rounded-[8px] border border-[#182032] bg-[#070a12] px-[10px] py-[4px] font-mono">
                        {cloudSpecs.map((spec) => (
                          <div
                            key={spec.label}
                            className="flex items-center justify-between gap-2 py-[6px]"
                          >
                            <dt className="shrink-0 text-[9px] tracking-[0.04em] text-[#64748b]">
                              {spec.label}
                            </dt>
                            <dd
                              className={`truncate text-[10px] leading-[13px] ${spec.valueClassName ?? "text-[#cbd5e1]"}`}
                              title={spec.value}
                            >
                              {spec.value}
                            </dd>
                          </div>
                        ))}
                      </dl>
                    </label>

                    {/* Local-Only Sovereign Option Card */}
                    <label
                      htmlFor={localModeId}
                      data-testid="ai-mode-card-local_only"
                      className={`flex min-w-0 cursor-pointer flex-col justify-between gap-[10px] rounded-[10px] border p-[13px] transition-colors ${
                        isWanPolicyError
                          ? "border-[1.5px] border-[#ef4444] bg-[#1a0d14]"
                          : draft.ai_processing_mode === "local_only"
                            ? "border-[1.5px] border-[#00e599] bg-[#071c19]"
                            : "border-[#1e2636] bg-[#0a0e18] hover:border-[#2d3a52]"
                      }`}
                    >
                      <div className="space-y-[8px]">
                        <div className="flex items-center justify-between gap-2">
                          <div className="flex items-center gap-[8px]">
                            <input
                              id={localModeId}
                              type="radio"
                              name="ai_processing_mode"
                              value="local_only"
                              checked={draft.ai_processing_mode === "local_only"}
                              onChange={() => handleModeChange("local_only")}
                              data-testid="ai-mode-option-local_only"
                              className={`size-[16px] shrink-0 cursor-pointer appearance-none rounded-full border-[1.5px] border-solid transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 disabled:cursor-not-allowed ${
                                isWanPolicyError
                                  ? "border-[#ef4444] bg-[#2a1216] checked:border-[#ef4444] checked:bg-[#ef4444] checked:shadow-[inset_0_0_0_3.5px_#2a1216] focus-visible:outline-[#ef4444]"
                                  : "border-[#475569] bg-[#090d16] checked:border-[#00e599] checked:bg-[#00e599] checked:shadow-[inset_0_0_0_3.5px_#062a1c] focus-visible:outline-[#00e599]"
                              }`}
                            />
                            <div
                              className={`flex size-[26px] shrink-0 items-center justify-center rounded-[7px] border ${
                                isWanPolicyError
                                  ? "border-[#ef4444]/40 bg-[#30121b] text-[#f87171]"
                                  : "border-[#00e599]/35 bg-[#08241d] text-[#00e599]"
                              }`}
                            >
                              <Cpu className="size-[14px]" aria-hidden="true" />
                            </div>
                          </div>

                          {isWanPolicyError ? (
                            <span className="inline-flex items-center gap-[5px] rounded-[5px] border border-[#ef4444]/50 bg-[#38131d] px-[7px] py-[2.5px] font-mono text-[9.5px] font-bold uppercase tracking-[0.04em] text-[#fca5a5]">
                              <span
                                className="size-[5px] rounded-full bg-[#ef4444]"
                                aria-hidden="true"
                              />
                              <span>WAN Blocked</span>
                            </span>
                          ) : settings.ai_processing_mode === "local_only" ? (
                            <span className="inline-flex items-center gap-[5px] rounded-[5px] border border-[#00e599]/40 bg-[#062a1c] px-[7px] py-[2.5px] font-mono text-[9.5px] font-bold uppercase tracking-[0.04em] text-[#00e599]">
                              <span
                                className="size-[5px] rounded-full bg-[#00e599]"
                                aria-hidden="true"
                              />
                              <span>Live Mode</span>
                            </span>
                          ) : (
                            <span className="inline-flex items-center rounded-[5px] border border-[#f59e0b]/45 bg-[#261a08] px-[7px] py-[2.5px] font-mono text-[9.5px] font-bold uppercase tracking-[0.04em] text-[#fbbf24]">
                              Zero WAN
                            </span>
                          )}
                        </div>

                        <div>
                          <div className="flex flex-wrap items-center gap-[6px]">
                            <span className="text-[13.5px] font-bold leading-[18px] text-[#f8fafc]">
                              Local-Only Sovereign
                            </span>
                            <code
                              className={`rounded-[4px] border border-[#252f44] bg-[#131a29] px-[6px] py-[1.5px] font-mono text-[9px] font-medium ${
                                isWanPolicyError
                                  ? "text-[#fca5a5]"
                                  : "text-[#00e599]"
                              }`}
                            >
                              local_only
                            </code>
                          </div>
                          <p className="mt-[4px] text-[11px] leading-[15px] text-[#94a3b8]">
                            Confines inference strictly inside RFC1918 private
                            networks or cluster DNS.
                          </p>
                        </div>
                      </div>

                      <dl
                        className={`divide-y rounded-[8px] border bg-[#070a12] px-[10px] py-[4px] font-mono ${
                          isWanPolicyError
                            ? "divide-[#261118] border-[#451a26]"
                            : "divide-[#131a2a] border-[#182032]"
                        }`}
                      >
                        {localSpecs.map((spec) => (
                          <div
                            key={spec.label}
                            className="flex items-center justify-between gap-2 py-[6px]"
                          >
                            <dt className="shrink-0 text-[9px] tracking-[0.04em] text-[#64748b]">
                              {spec.label}
                            </dt>
                            <dd
                              className={`truncate text-[10px] leading-[13px] ${spec.valueClassName ?? "text-[#cbd5e1]"}`}
                              title={spec.value}
                            >
                              {spec.value}
                            </dd>
                          </div>
                        ))}
                      </dl>
                    </label>
                  </fieldset>

                  {/* Bottom Contextual Callout (HTTP 400 Error -> Mode Switch Warning -> Dual HNSW Info) */}
                  {submitError && isWanPolicyError ? (
                    <div
                      role="alert"
                      data-testid="workspace-settings-submit-error"
                      className="flex items-start gap-[10px] rounded-[8px] border border-[#ef4444]/55 bg-[#241015] px-[12px] py-[10px]"
                    >
                      <AlertTriangle
                        className="mt-[1px] size-[15px] shrink-0 text-[#f87171]"
                        aria-hidden="true"
                      />
                      <div className="space-y-[3px]">
                        <p className="text-[11.5px] font-semibold leading-[15px] text-[#fee2e2]">
                          HTTP 400 · Fail-Closed Local-Only Policy Violation
                        </p>
                        <p className="text-[10.5px] leading-[14.5px] text-[#fecaca]">
                          {submitError}
                        </p>
                      </div>
                    </div>
                  ) : modeWillChange ? (
                    <div
                      data-testid="ai-mode-switch-warning"
                      className="flex items-start gap-[10px] rounded-[8px] border border-[#f59e0b]/50 bg-[#211708] px-[12px] py-[10px]"
                    >
                      <AlertTriangle
                        className="mt-[1px] size-[15px] shrink-0 text-[#fbbf24]"
                        aria-hidden="true"
                      />
                      <div className="space-y-[3px]">
                        <p className="text-[11.5px] font-semibold leading-[15px] text-[#fef3c7]">
                          Switching {getAiModeLabel(settings.ai_processing_mode)}{" "}
                          → {getAiModeLabel(draft.ai_processing_mode)} triggers
                          ADR vector re-indexing
                        </p>
                        <p className="text-[10.5px] leading-[14.5px] text-[#fde68a]">
                          Saving immediately enqueues a high-priority{" "}
                          <code className="font-mono">adr.reembed</code> worker
                          job into the{" "}
                          {draft.ai_processing_mode === "local_only"
                            ? "384d HNSW partition. LOCAL_LLM_BASE_URL must resolve to a local/internal host."
                            : "1536d Cloud ZDR HNSW partition."}
                        </p>
                      </div>
                    </div>
                  ) : (
                    <div className="flex items-center gap-[10px] rounded-[8px] border border-[#1c2436] bg-[#090d16] px-[12px] py-[10px]">
                      <span className="shrink-0 rounded-[5px] border border-[#00e599]/35 bg-[#062a1c] px-[7px] py-[3px] font-mono text-[9px] font-bold uppercase tracking-[0.04em] text-[#00e599]">
                        Dual HNSW
                      </span>
                      <p className="text-[10.5px] leading-[14.5px] text-[#94a3b8]">
                        PostgreSQL tenant partitions maintain isolated 384d and
                        1536d HNSW vector indexes. Switching modes re-embeds
                        approved ADRs asynchronously with zero downtime.
                      </p>
                    </div>
                  )}
                </section>

                {/* RIGHT COLUMN: Tenant Data Retention + Organization Identity */}
                <div className="flex min-w-0 flex-col gap-[12px]">
                  {/* Card A: Tenant Data Retention */}
                  <section
                    className={`flex flex-col gap-[10px] rounded-[12px] border bg-[#0f1422] p-[14px] ${
                      retentionHasError
                        ? "border-[#ef4444]/55"
                        : "border-[#1e2636]"
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-[7px]">
                        <Database
                          className={`size-[14px] shrink-0 ${
                            retentionHasError
                              ? "text-[#f87171]"
                              : "text-[#00e599]"
                          }`}
                          aria-hidden="true"
                        />
                        <h2 className="text-[13.5px] font-semibold leading-[18px] text-[#f8fafc]">
                          Tenant Data Retention
                        </h2>
                      </div>

                      <span
                        data-testid="retention-duration-preview"
                        className={`shrink-0 rounded-[5px] border px-[7px] py-[2.5px] font-mono text-[9px] font-bold uppercase tracking-[0.03em] ${
                          retentionValid && parsedRetentionDays !== null
                            ? "border-[#00e599]/35 bg-[#062a1c] text-[#00e599]"
                            : "border-[#ef4444]/50 bg-[#2b1018] text-[#fca5a5]"
                        }`}
                      >
                        {retentionValid && parsedRetentionDays !== null
                          ? formatRetentionDuration(parsedRetentionDays)
                          : "Out of range"}
                      </span>
                    </div>

                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-[8px]">
                        <label htmlFor={retentionId} className="sr-only">
                          Retention Period (Days)
                        </label>
                        <input
                          ref={retentionInputRef}
                          id={retentionId}
                          name="retention_days"
                          type="text"
                          inputMode="numeric"
                          enterKeyHint="done"
                          required
                          value={draft.retention_days_input}
                          onChange={(e) => {
                            setDraft((prev) => ({
                              ...prev,
                              retention_days_input: e.target.value,
                            }));
                            setSubmitError(null);
                          }}
                          onBlur={() =>
                            setTouched((prev) => ({
                              ...prev,
                              retention_days: true,
                            }))
                          }
                          aria-invalid={retentionHasError ? "true" : "false"}
                          aria-describedby={
                            retentionHasError
                              ? `${retentionHintId} ${retentionErrorId}`
                              : retentionHintId
                          }
                          data-testid="retention-days-input"
                          className={`h-[32px] w-[108px] rounded-[7px] border border-solid px-[11px] font-mono text-[13.5px] font-bold transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 ${
                            retentionHasError
                              ? "border-[#ef4444] bg-[#1c0d13] text-[#fca5a5] focus-visible:outline-[#ef4444]"
                              : "border-[#263044] bg-[#080c15] text-[#f8fafc] focus-visible:border-[#00e599] focus-visible:outline-[#00e599]"
                          }`}
                        />
                        <span className="font-mono text-[11px] text-[#94a3b8]">
                          days
                        </span>
                      </div>

                      <span
                        id={retentionHintId}
                        className={`font-mono text-[9.5px] ${
                          retentionHasError ? "text-[#f87171]" : "text-[#64748b]"
                        }`}
                      >
                        Range: {TENANT_RETENTION_MIN_DAYS} –{" "}
                        {TENANT_RETENTION_MAX_DAYS}d
                      </span>
                    </div>

                    {retentionHasError && (
                      <div className="flex items-center gap-[5px]">
                        <AlertTriangle
                          className="size-[11px] shrink-0 text-[#f87171]"
                          aria-hidden="true"
                        />
                        <p
                          id={retentionErrorId}
                          data-testid="retention-days-error"
                          className="text-[10.5px] font-medium leading-[13px] text-[#fca5a5]"
                        >
                          {allValidationErrors.retention_days}
                        </p>
                      </div>
                    )}

                    {/* Visual Retention Scale + Ticks */}
                    <div className="space-y-[4px]">
                      <div
                        className="h-[6px] w-full overflow-clip rounded-full bg-[#161e2e]"
                        aria-hidden="true"
                      >
                        <div
                          className={`h-full rounded-full transition-[width] duration-200 ${
                            retentionHasError || !retentionValid
                              ? "bg-[#ef4444]"
                              : "bg-[#00e599]"
                          }`}
                          style={{ width: `${retentionScalePercent}%` }}
                        />
                      </div>
                      <div
                        className="flex items-center justify-between font-mono text-[8.5px] leading-[11px] text-[#475569]"
                        aria-hidden="true"
                      >
                        {RETENTION_SCALE_TICKS.map((tick) => (
                          <span key={tick}>{tick}</span>
                        ))}
                      </div>
                    </div>

                    {/* Quick Presets */}
                    <div className="grid grid-cols-5 gap-[6px]">
                      {TENANT_RETENTION_PRESETS.map((preset) => {
                        const isSelected = parsedRetentionDays === preset.days;
                        return (
                          <button
                            key={preset.days}
                            type="button"
                            onClick={() =>
                              handleRetentionPresetClick(preset.days)
                            }
                            aria-pressed={isSelected}
                            aria-label={`Set retention to ${preset.label}`}
                            data-testid={`retention-preset-${preset.days}`}
                            className={`flex h-[27px] items-center justify-center rounded-[6px] border font-mono text-[10.5px] transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#00e599] ${
                              isSelected
                                ? "border-[#00e599] bg-[#062a1c] font-bold text-[#00e599]"
                                : "border-[#1e2636] bg-[#0a0e18] font-medium text-[#cbd5e1] hover:border-[#334155] hover:text-[#f8fafc]"
                            }`}
                          >
                            {preset.shortLabel}
                          </button>
                        );
                      })}
                    </div>

                    <p className="font-mono text-[9.5px] leading-[13px] text-[#64748b]">
                      Writes tenant.settings.updated audit log · No ADR
                      re-index.
                    </p>
                  </section>

                  {/* Card B: Organization Identity */}
                  <section
                    className={`flex flex-1 flex-col justify-between gap-[10px] rounded-[12px] border bg-[#0f1422] p-[14px] ${
                      orgCardHasError ? "border-[#ef4444]/45" : "border-[#1e2636]"
                    }`}
                  >
                    <div className="space-y-[9px]">
                      <div className="flex items-center justify-between gap-2">
                        <div className="flex items-center gap-[7px]">
                          <Building2
                            className={`size-[14px] shrink-0 ${
                              orgCardHasError
                                ? "text-[#f87171]"
                                : "text-[#94a3b8]"
                            }`}
                            aria-hidden="true"
                          />
                          <h2 className="text-[13.5px] font-semibold leading-[18px] text-[#f8fafc]">
                            Organization Identity
                          </h2>
                        </div>

                        {domainHasError ? (
                          <span className="inline-flex shrink-0 items-center rounded-[5px] border border-[#ef4444]/45 bg-[#2b1018] px-[6px] py-[2px] font-mono text-[8.5px] font-bold uppercase tracking-[0.04em] text-[#fca5a5]">
                            Invalid Domain
                          </span>
                        ) : orgCardHasError ? (
                          <span className="inline-flex shrink-0 items-center rounded-[5px] border border-[#ef4444]/45 bg-[#2b1018] px-[6px] py-[2px] font-mono text-[8.5px] font-bold uppercase tracking-[0.04em] text-[#fca5a5]">
                            Check Fields
                          </span>
                        ) : (
                          <span className="inline-flex shrink-0 items-center gap-[5px] rounded-[5px] border border-[#00e599]/30 bg-[#062a1c] px-[6px] py-[2px] font-mono text-[8.5px] font-bold uppercase tracking-[0.04em] text-[#00e599]">
                            <span
                              className="size-[5px] rounded-full bg-[#00e599]"
                              aria-hidden="true"
                            />
                            <span>{settings.status}</span>
                          </span>
                        )}
                      </div>

                      {/* Organization Name */}
                      <div>
                        <label
                          htmlFor={nameId}
                          className="block font-mono text-[9px] tracking-[0.05em] text-[#64748b]"
                        >
                          ORGANIZATION NAME{" "}
                          <span className="text-[#ef4444]" aria-hidden="true">
                            *
                          </span>
                        </label>
                        <span id={nameHintId} className="sr-only">
                          2 to 255 characters
                        </span>
                        <input
                          ref={nameInputRef}
                          id={nameId}
                          name="name"
                          type="text"
                          required
                          minLength={2}
                          maxLength={255}
                          autoComplete="organization"
                          value={draft.name}
                          onChange={(e) => {
                            setDraft((prev) => ({
                              ...prev,
                              name: e.target.value,
                            }));
                            setSubmitError(null);
                          }}
                          onBlur={() =>
                            setTouched((prev) => ({ ...prev, name: true }))
                          }
                          aria-invalid={
                            isFieldErrorVisible("name") ? "true" : "false"
                          }
                          aria-describedby={
                            isFieldErrorVisible("name")
                              ? `${nameHintId} ${nameErrorId}`
                              : nameHintId
                          }
                          data-testid="workspace-name-input"
                          className={`mt-[4px] h-[31px] w-full rounded-[6px] border border-solid px-[10px] text-[12px] transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 ${
                            isFieldErrorVisible("name")
                              ? "border-[#ef4444] bg-[#1c0d13] text-[#fca5a5] focus-visible:outline-[#ef4444]"
                              : "border-[#252f44] bg-[#080c15] text-[#f8fafc] focus-visible:border-[#00e599] focus-visible:outline-[#00e599]"
                          }`}
                        />
                        {isFieldErrorVisible("name") && (
                          <p
                            id={nameErrorId}
                            data-testid="workspace-name-error"
                            className="mt-[4px] text-[10px] font-medium text-[#fca5a5]"
                          >
                            {allValidationErrors.name}
                          </p>
                        )}
                      </div>

                      {/* Allowed SSO Domain & Industry 2-Column Row */}
                      <div className="grid grid-cols-1 gap-[8px] sm:grid-cols-2">
                        <div>
                          <label
                            htmlFor={domainId}
                            className={`block font-mono text-[9px] tracking-[0.04em] ${
                              domainHasError ? "text-[#fca5a5]" : "text-[#64748b]"
                            }`}
                          >
                            ALLOWED SSO DOMAIN
                          </label>
                          <span id={domainHintId} className="sr-only">
                            Optional domain such as company.com without https://
                          </span>
                          <input
                            ref={domainInputRef}
                            id={domainId}
                            name="allowed_domain"
                            type="text"
                            maxLength={255}
                            autoCorrect="off"
                            autoCapitalize="none"
                            spellCheck={false}
                            placeholder="company.com"
                            value={draft.allowed_domain}
                            onChange={(e) => {
                              setDraft((prev) => ({
                                ...prev,
                                allowed_domain: e.target.value,
                              }));
                              setSubmitError(null);
                            }}
                            onBlur={() =>
                              setTouched((prev) => ({
                                ...prev,
                                allowed_domain: true,
                              }))
                            }
                            aria-invalid={domainHasError ? "true" : "false"}
                            aria-describedby={
                              domainHasError
                                ? `${domainHintId} ${domainErrorId}`
                                : domainHintId
                            }
                            data-testid="workspace-domain-input"
                            className={`mt-[4px] h-[31px] w-full rounded-[6px] border border-solid px-[9px] font-mono text-[11px] placeholder:text-[#475569] transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 ${
                              domainHasError
                                ? "border-[#ef4444] bg-[#1c0d13] text-[#fca5a5] focus-visible:outline-[#ef4444]"
                                : "border-[#252f44] bg-[#080c15] text-[#e2e8f0] focus-visible:border-[#00e599] focus-visible:outline-[#00e599]"
                            }`}
                          />
                          {domainHasError && (
                            <p
                              id={domainErrorId}
                              data-testid="workspace-domain-error"
                              className="mt-[4px] text-[10px] font-medium leading-[13px] text-[#f87171]"
                            >
                              {allValidationErrors.allowed_domain}
                            </p>
                          )}
                        </div>

                        <div>
                          <label
                            htmlFor={industryId}
                            className="block font-mono text-[9px] tracking-[0.04em] text-[#64748b]"
                          >
                            INDUSTRY
                          </label>
                          <input
                            ref={industryInputRef}
                            id={industryId}
                            name="industry"
                            type="text"
                            maxLength={255}
                            placeholder="FinTech, SaaS..."
                            value={draft.industry}
                            onChange={(e) => {
                              setDraft((prev) => ({
                                ...prev,
                                industry: e.target.value,
                              }));
                              setSubmitError(null);
                            }}
                            onBlur={() =>
                              setTouched((prev) => ({ ...prev, industry: true }))
                            }
                            aria-invalid={
                              isFieldErrorVisible("industry") ? "true" : "false"
                            }
                            aria-describedby={
                              isFieldErrorVisible("industry")
                                ? industryErrorId
                                : undefined
                            }
                            data-testid="workspace-industry-input"
                            className={`mt-[4px] h-[31px] w-full rounded-[6px] border border-solid px-[9px] text-[11.5px] placeholder:text-[#475569] transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 ${
                              isFieldErrorVisible("industry")
                                ? "border-[#ef4444] bg-[#1c0d13] text-[#fca5a5] focus-visible:outline-[#ef4444]"
                                : "border-[#252f44] bg-[#080c15] text-[#e2e8f0] focus-visible:border-[#00e599] focus-visible:outline-[#00e599]"
                            }`}
                          />
                          {isFieldErrorVisible("industry") && (
                            <p
                              id={industryErrorId}
                              data-testid="workspace-industry-error"
                              className="mt-[4px] text-[10px] font-medium text-[#fca5a5]"
                            >
                              {allValidationErrors.industry}
                            </p>
                          )}
                        </div>
                      </div>
                    </div>

                    {/* Inset Tenant Metadata Strip */}
                    <dl className="space-y-[5px] rounded-[7px] border border-[#182032] bg-[#070a12] px-[10px] py-[8px] font-mono">
                      <div className="flex items-center justify-between gap-2">
                        <dt className="text-[8.5px] uppercase tracking-[0.04em] text-[#64748b]">
                          Tenant Slug
                        </dt>
                        <dd
                          data-testid="workspace-slug-value"
                          className="truncate text-[9.5px] font-medium text-[#cbd5e1]"
                          title={settings.slug}
                        >
                          {settings.slug}
                        </dd>
                      </div>
                      <div className="flex items-center justify-between gap-2">
                        <dt className="text-[8.5px] uppercase tracking-[0.04em] text-[#64748b]">
                          Project Quota
                        </dt>
                        <dd
                          data-testid="workspace-max-projects-value"
                          className="text-[9.5px] font-medium text-[#00e599]"
                        >
                          max {settings.max_projects} projects
                        </dd>
                      </div>
                    </dl>
                  </section>
                </div>
              </div>

              {/* Non-WAN Server Error Banner (fallback above footer) */}
              {submitError && !isWanPolicyError && (
                <div
                  role="alert"
                  data-testid="workspace-settings-submit-error"
                  className="flex items-start gap-[10px] rounded-[10px] border border-[#ef4444]/45 bg-[#241015] px-[14px] py-[10px] text-[11.5px] leading-[16px] text-[#fca5a5]"
                >
                  <AlertCircle
                    className="mt-[2px] size-[14px] shrink-0 text-[#ef4444]"
                    aria-hidden="true"
                  />
                  <span>{submitError}</span>
                </div>
              )}

              {/* Unified Bottom Action Footer Bar */}
              <div className="flex min-h-[42px] flex-wrap items-center justify-between gap-3 rounded-[10px] border border-[#1e2636] bg-[#0e1320] px-[14px] py-[7px]">
                <div
                  className={`flex items-center gap-[8px] font-mono text-[10.5px] ${footerStatus.textClass}`}
                >
                  <span
                    className={`size-[6px] shrink-0 rounded-full ${footerStatus.dotClass}`}
                    aria-hidden="true"
                  />
                  <span>{footerStatus.label}</span>
                </div>

                <div className="flex items-center gap-[8px]">
                  <button
                    type="button"
                    onClick={handleDiscard}
                    disabled={!isDirty || updateMutation.isPending}
                    data-testid="workspace-settings-discard-button"
                    className="inline-flex h-[28px] items-center justify-center rounded-[7px] border border-[#263044] bg-[#141a28] px-[12px] text-[11.5px] font-medium text-[#cbd5e1] transition-colors hover:border-[#334155] hover:text-[#f8fafc] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#00e599] disabled:cursor-not-allowed disabled:border-[#1a2232] disabled:bg-[#0e1320] disabled:text-[#475569]"
                  >
                    Discard
                  </button>
                  <button
                    type="submit"
                    disabled={!isDirty || updateMutation.isPending}
                    data-testid="workspace-settings-save-button"
                    className="inline-flex h-[28px] items-center gap-1.5 rounded-[7px] bg-[#00e599] px-[15px] text-[11.5px] font-bold text-[#07111a] transition-colors hover:bg-[#1ef5ab] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#00e599] disabled:cursor-not-allowed disabled:bg-[#1c2433] disabled:text-[#64748b]"
                  >
                    {updateMutation.isPending ? (
                      <>
                        <Loader2
                          className="size-[12px] animate-spin"
                          aria-hidden="true"
                        />
                        <span>Saving...</span>
                      </>
                    ) : (
                      <span>Save Changes</span>
                    )}
                  </button>
                </div>
              </div>
            </form>
          </>
        ) : null}
      </div>

      {/* Persistent Notifications Region + Floating Toast */}
      <div
        role="region"
        aria-label="Notifications"
        className="pointer-events-none fixed right-[24px] top-[58px] z-50"
      >
        {toast && (
          <div
            role="status"
            data-testid="workspace-settings-toast"
            onMouseEnter={() => setToastPaused(true)}
            onMouseLeave={() => setToastPaused(false)}
            onFocusCapture={() => setToastPaused(true)}
            onBlurCapture={(e) => {
              if (!e.currentTarget.contains(e.relatedTarget)) {
                setToastPaused(false);
              }
            }}
            className="pointer-events-auto flex w-[min(392px,calc(100vw-32px))] items-start justify-between gap-[10px] rounded-[10px] border border-[#00e599]/55 bg-[#081d21] px-[13px] py-[10px] text-[#e2e8f0] shadow-[0_12px_28px_rgba(0,0,0,0.65)]"
          >
            <div className="flex items-start gap-[10px]">
              <CheckCircle2
                className="mt-[1px] size-[15px] shrink-0 text-[#00e599]"
                aria-hidden="true"
              />
              <div>
                <p className="text-[11.5px] font-semibold leading-[15px] text-[#f8fafc]">
                  {toast.title}
                </p>
                <p className="mt-[2px] text-[10.5px] leading-[14px] text-[#94a3b8]">
                  {toast.description}
                </p>
              </div>
            </div>
            <button
              type="button"
              onClick={() => setToast(null)}
              aria-label="Dismiss notification"
              className="flex size-[22px] shrink-0 items-center justify-center rounded-[5px] text-[#94a3b8] transition-colors hover:bg-white/[0.06] hover:text-[#f8fafc] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#00e599]"
            >
              <X className="size-[13px]" aria-hidden="true" />
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
