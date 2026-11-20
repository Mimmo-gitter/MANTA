"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  Check,
  Copy,
  Eye,
  EyeOff,
  KeyRound,
  Loader2,
  Lock,
  RefreshCw,
  RotateCw,
  ShieldCheck,
  X,
} from "lucide-react";
import { HealthProbeSourceSelect } from "@/components/projects/health-probe-source-select";
import { RotateHealthSecretModal } from "@/components/projects/rotate-health-secret-modal";
import { usePermissions } from "@/hooks/use-permissions";
import {
  useProjectHealthConfigQuery,
  useProjectHealthSignalsQuery,
  useRotateProjectHealthSecretMutation,
  useUpdateProjectHealthConfigMutation,
} from "@/hooks/use-projects";
import { isApiHttpError } from "@/lib/api-client";
import {
  computeMeanRecoverySeconds30d,
  formatHealthSessionTimestamp,
  formatProbeRelativeAge,
  formatRecoveryDuration,
  formatUtcHourMinute,
  getProbeSourceMeta,
  hasConfiguredHealthSecret,
  normalizeProbeSource,
} from "@/lib/health-sources";
import { getPrimaryRoleLabel } from "@/lib/permissions";
import { cn } from "@/lib/utils";
import type {
  HealthProbeConfig,
  HealthProbeSourceId,
  ProjectItem,
} from "@/types/project";

interface HealthToastState {
  id: number;
  kind: "success" | "error";
  title: string;
  description: string;
}

interface SessionSecretCache {
  projectId: string;
  value: string;
}

interface RecoveredHighlight {
  id: string;
  resolvedAt: string;
  recoverySeconds: number;
}

function HealthSourcesToast({
  toast,
  onDismiss,
}: {
  toast: HealthToastState | null;
  onDismiss: () => void;
}) {
  if (!toast) return null;

  const isError = toast.kind === "error";

  return (
    <div
      role={isError ? "alert" : "status"}
      data-testid="health-sources-toast"
      data-toast-kind={toast.kind}
      className={cn(
        "fixed right-6 top-[56px] z-40 flex w-[410px] max-w-[calc(100vw-32px)] items-start gap-3 rounded-[10px] border p-3.5 shadow-[0_16px_38px_rgba(0,0,0,0.55)] backdrop-blur-md",
        isError
          ? "border-[#ef4444]/45 bg-[#201116]/95"
          : "border-[#00e599]/40 bg-[#0c1c1a]/95",
      )}
    >
      <div
        className={cn(
          "mt-0.5 flex size-[26px] shrink-0 items-center justify-center rounded-full border",
          isError
            ? "border-[#ef4444]/50 bg-[#ef4444]/15 text-[#f87171]"
            : "border-[#00e599]/50 bg-[#00e599]/15 text-[#00e599]",
        )}
      >
        {isError ? (
          <AlertCircle className="size-3.5" aria-hidden="true" />
        ) : (
          <Check className="size-3.5" aria-hidden="true" />
        )}
      </div>

      <div className="min-w-0 flex-1">
        <p className="text-[12px] font-semibold leading-[17px] text-[#f8fafc]">
          {toast.title}
        </p>
        <p className="mt-0.5 text-[11px] leading-[16px] text-[#94a3b8]">
          {toast.description}
        </p>
      </div>

      <button
        type="button"
        onClick={onDismiss}
        aria-label="Dismiss notification"
        className="inline-flex size-[24px] shrink-0 items-center justify-center rounded-[5px] text-[#7c839b] transition-colors hover:bg-white/[0.06] hover:text-[#e2e8f0] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00e599]"
      >
        <X className="size-3.5" aria-hidden="true" />
      </button>
    </div>
  );
}

function HealthSourcesSkeleton() {
  return (
    <div
      data-testid="health-sources-loading"
      className="flex min-w-0 flex-col gap-[14px]"
    >
      <div className="h-[260px] animate-pulse rounded-[12px] border border-[#1e293b] bg-[#0d121f]" />
      <div className="h-[220px] animate-pulse rounded-[12px] border border-[#1e293b] bg-[#0d121f]" />
    </div>
  );
}

export function HealthSourcesTab({ project }: { project: ProjectItem }) {
  const { can, roles } = usePermissions();
  const isTenantAdmin = can("MANAGE_WEBHOOK_INTEGRATIONS");
  const roleLabel = getPrimaryRoleLabel(roles);

  const [pollStatusHint, setPollStatusHint] = useState<"HEALTHY" | "FAILING">(
    project.health_status === "FAILING" ? "FAILING" : "HEALTHY",
  );
  const activePollMs = pollStatusHint === "FAILING" ? 5_000 : 15_000;

  const configQuery = useProjectHealthConfigQuery(
    project.id,
    true,
    activePollMs,
  );
  const signalsQuery = useProjectHealthSignalsQuery(
    project.id,
    true,
    activePollMs,
  );

  const updateConfigMutation = useUpdateProjectHealthConfigMutation(project.id);
  const rotateSecretMutation = useRotateProjectHealthSecretMutation(project.id);

  const [sessionSecretCache, setSessionSecretCache] =
    useState<SessionSecretCache | null>(null);
  const [isSecretRevealed, setIsSecretRevealed] = useState(false);
  const [secretHint, setSecretHint] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [rotateModalOpen, setRotateModalOpen] = useState(false);
  const [rotateError, setRotateError] = useState<string | null>(null);
  const [copiedKey, setCopiedKey] = useState<"url" | "secret" | null>(null);
  const [toast, setToast] = useState<HealthToastState | null>(null);
  const [recoveredHighlight, setRecoveredHighlight] =
    useState<RecoveredHighlight | null>(null);
  const [nowMs, setNowMs] = useState<number>(() => Date.now());

  const trackedOpenSessionRef = useRef<{
    id: string;
    startedAt: string;
  } | null>(null);

  const config: HealthProbeConfig | undefined = configQuery.data;
  const sessions = useMemo(
    () => signalsQuery.data ?? [],
    [signalsQuery.data],
  );

  const openSession = useMemo(
    () => sessions.find((session) => session.status === "OPEN") ?? null,
    [sessions],
  );

  const isFailing =
    config?.health_status === "FAILING" || Boolean(openSession);

  useEffect(() => {
    setPollStatusHint(isFailing ? "FAILING" : "HEALTHY");
  }, [isFailing]);

  // Live 1s timer only runs when an OPEN incident session is active (state 6E).
  useEffect(() => {
    if (!openSession) return;
    const interval = window.setInterval(() => {
      setNowMs(Date.now());
    }, 1_000);
    return () => window.clearInterval(interval);
  }, [openSession]);

  // Update relative timestamps smoothly when queries refresh.
  useEffect(() => {
    if (configQuery.dataUpdatedAt || signalsQuery.dataUpdatedAt) {
      setNowMs(Date.now());
    }
  }, [configQuery.dataUpdatedAt, signalsQuery.dataUpdatedAt]);

  // Detect live FAILING -> HEALTHY transition (6E -> 6F) and trigger recovery toast.
  useEffect(() => {
    if (openSession) {
      trackedOpenSessionRef.current = {
        id: openSession.id,
        startedAt: openSession.started_at,
      };
      return;
    }

    const previousOpen = trackedOpenSessionRef.current;
    if (previousOpen && config?.health_status !== "FAILING") {
      const resolvedMatch = sessions.find(
        (session) =>
          session.id === previousOpen.id && session.status === "RESOLVED",
      );
      trackedOpenSessionRef.current = null;
      if (resolvedMatch) {
        const computedSeconds =
          resolvedMatch.recovery_time_seconds ??
          Math.max(
            0,
            Math.round(
              (Date.parse(
                resolvedMatch.resolved_at ?? new Date().toISOString(),
              ) -
                Date.parse(resolvedMatch.started_at)) /
                1000,
            ),
          );
        setRecoveredHighlight({
          id: resolvedMatch.id,
          resolvedAt: resolvedMatch.resolved_at ?? new Date().toISOString(),
          recoverySeconds: computedSeconds,
        });
        setToast({
          id: Date.now(),
          kind: "success",
          title: "Production health recovered",
          description: `Incident resolved after ${formatRecoveryDuration(computedSeconds)} · Recovery metric recorded.`,
        });
      }
    }
  }, [config?.health_status, openSession, sessions]);

  useEffect(() => {
    if (!toast || toast.kind !== "success") return;
    const timer = window.setTimeout(() => {
      setToast((prev) => (prev?.id === toast.id ? null : prev));
    }, 6_000);
    return () => window.clearTimeout(timer);
  }, [toast]);

  if (configQuery.isLoading && !config) {
    return <HealthSourcesSkeleton />;
  }

  if (configQuery.isError && !config) {
    return (
      <div
        role="alert"
        data-testid="health-sources-error"
        className="flex flex-col items-center justify-center rounded-[12px] border border-[#ef4444]/35 bg-[#0d121f] px-6 py-14 text-center"
      >
        <AlertCircle className="size-6 text-[#ef4444]" aria-hidden="true" />
        <h2 className="mt-3 text-[15px] font-semibold text-[#f8fafc]">
          Unable to load Health Sources configuration
        </h2>
        <p className="mt-1 max-w-[440px] text-[12px] text-[#94a3b8]">
          {isApiHttpError(configQuery.error)
            ? configQuery.error.detail
            : configQuery.error instanceof Error
              ? configQuery.error.message
              : "Unexpected error while loading project health configuration."}
        </p>
        <button
          type="button"
          onClick={() => {
            void configQuery.refetch();
            void signalsQuery.refetch();
          }}
          className="mt-4 inline-flex h-[32px] items-center gap-1.5 rounded-[7px] border border-[#262d3d] bg-[#131722] px-3.5 text-[12px] font-medium text-[#e2e8f0] hover:border-[#3b455e]"
        >
          <RefreshCw className="size-3.5" aria-hidden="true" />
          Retry
        </button>
      </div>
    );
  }

  if (!config) {
    return null;
  }

  const activeSourceId = normalizeProbeSource(config.health_probe_source);
  const activeSourceMeta = getProbeSourceMeta(activeSourceId);
  const hasSecret = hasConfiguredHealthSecret(config);
  const isProbeEnabled = config.health_probe_enabled;

  const plaintextSecret =
    config.webhook_secret ||
    (sessionSecretCache?.projectId === project.id
      ? sessionSecretCache.value
      : null);

  const maskedDisplay =
    config.webhook_secret_masked && config.webhook_secret_masked !== "********"
      ? config.webhook_secret_masked
      : "whsec_manta_••••••••••••••••••••";

  const latestResolvedSession =
    sessions.find((session) => session.status === "RESOLVED") ?? null;

  const mean30dSeconds = computeMeanRecoverySeconds30d(sessions, nowMs);
  const mean30dLabel =
    mean30dSeconds !== null ? formatRecoveryDuration(mean30dSeconds) : "—";

  const openElapsedSeconds = openSession
    ? Math.max(
        0,
        Math.floor((nowMs - Date.parse(openSession.started_at)) / 1000),
      )
    : 0;
  const openElapsedLabel = formatRecoveryDuration(openElapsedSeconds);

  const handleCopyText = async (key: "url" | "secret", text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedKey(key);
      window.setTimeout(() => {
        setCopiedKey((prev) => (prev === key ? null : prev));
      }, 2_000);
    } catch {
      setActionError("Clipboard access unavailable in this browser context.");
    }
  };

  const handleToggleSecretVisibility = () => {
    if (!isTenantAdmin) return;
    setActionError(null);

    if (isSecretRevealed) {
      setIsSecretRevealed(false);
      return;
    }

    if (!plaintextSecret) {
      setSecretHint(
        'Plaintext signing secret is shown once right after generation. Click "Rotate Secret" to generate a new revealable secret.',
      );
      return;
    }

    setSecretHint(null);
    setIsSecretRevealed(true);
  };

  const handleCopySecret = async () => {
    if (!isTenantAdmin) return;
    setActionError(null);

    if (!plaintextSecret) {
      setSecretHint(
        'Plaintext signing secret is shown once right after generation. Click "Rotate Secret" to copy a new secret.',
      );
      return;
    }

    setSecretHint(null);
    await handleCopyText("secret", plaintextSecret);
  };

  const handleGenerateFirstSecret = async () => {
    if (!isTenantAdmin) return;
    setActionError(null);
    setSecretHint(null);

    try {
      const updated = await rotateSecretMutation.mutateAsync();
      if (!isProbeEnabled) {
        await updateConfigMutation.mutateAsync({ health_probe_enabled: true });
      }
      if (updated.webhook_secret) {
        setSessionSecretCache({
          projectId: project.id,
          value: updated.webhook_secret,
        });
        setIsSecretRevealed(true);
      }
      setToast({
        id: Date.now(),
        kind: "success",
        title: "HMAC signing secret generated",
        description:
          "Copy your signing secret below — plaintext is displayed once for security.",
      });
    } catch (error) {
      setActionError(
        error instanceof Error
          ? error.message
          : "Failed to generate HMAC signing secret.",
      );
    }
  };

  const handleConfirmRotateSecret = async () => {
    if (!isTenantAdmin) return;
    setRotateError(null);
    setSecretHint(null);

    try {
      const updated = await rotateSecretMutation.mutateAsync();
      if (updated.webhook_secret) {
        setSessionSecretCache({
          projectId: project.id,
          value: updated.webhook_secret,
        });
        setIsSecretRevealed(true);
      }
      setRotateModalOpen(false);
      setToast({
        id: Date.now(),
        kind: "success",
        title: "HMAC signing secret rotated",
        description:
          "New signing secret is active and revealed below. Update your webhook sender now.",
      });
    } catch (error) {
      setRotateError(
        error instanceof Error
          ? error.message
          : "Failed to rotate HMAC signing secret.",
      );
    }
  };

  const handleProbeSourceChange = async (nextSource: HealthProbeSourceId) => {
    if (!isTenantAdmin) return;
    setActionError(null);

    try {
      await updateConfigMutation.mutateAsync({
        health_probe_source: nextSource,
      });
      const nextMeta = getProbeSourceMeta(nextSource);
      setToast({
        id: Date.now(),
        kind: "success",
        title: "Probe source updated",
        description: `Health receiver configured for ${nextMeta.label}.`,
      });
    } catch (error) {
      setActionError(
        error instanceof Error
          ? error.message
          : "Failed to update health probe source.",
      );
    }
  };

  const handleToggleProbeEnabled = async () => {
    if (!isTenantAdmin || updateConfigMutation.isPending) return;
    setActionError(null);
    const nextEnabled = !isProbeEnabled;

    try {
      await updateConfigMutation.mutateAsync({
        health_probe_enabled: nextEnabled,
      });
      setToast({
        id: Date.now(),
        kind: "success",
        title: nextEnabled
          ? "Health probe ingestion enabled"
          : "Health probe ingestion paused",
        description: nextEnabled
          ? "Incoming FAIL/PASS webhooks will now update project health status."
          : "Incoming health probes are paused until re-enabled.",
      });
    } catch (error) {
      setActionError(
        error instanceof Error
          ? error.message
          : "Failed to update probe enabled state.",
      );
    }
  };

  return (
    <div
      className="flex min-w-0 flex-col gap-[16px]"
      data-testid="health-sources-panel"
    >
      <HealthSourcesToast toast={toast} onDismiss={() => setToast(null)} />

      {/* Card 1: Production Health Webhook (6A / 6B / 6C / 6E / 6F / 6G) */}
      <section
        data-testid="health-webhook-card"
        aria-labelledby="health-webhook-heading"
        className={cn(
          "min-w-0 rounded-[12px] border bg-[#0d121f] p-[20px] transition-colors",
          isFailing ? "border-[#7f1d1d]/75" : "border-[#1e293b]",
        )}
      >
        {/* Header Row */}
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h2
              id="health-webhook-heading"
              className="text-[16px] font-semibold leading-[22px] text-[#f8fafc]"
            >
              Production Health Webhook
            </h2>
            <p className="mt-[4px] text-[12px] leading-[17px] text-[#94a3b8]">
              Receive HMAC-signed PASS and FAIL health signals from your
              production monitors to measure incident recovery time.
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-[8px]">
            {isTenantAdmin && (
              <button
                type="button"
                role="switch"
                aria-checked={isProbeEnabled}
                aria-label="Toggle health probe ingestion"
                data-testid="health-probe-enabled-toggle"
                disabled={updateConfigMutation.isPending}
                onClick={() => void handleToggleProbeEnabled()}
                className={cn(
                  "inline-flex h-[26px] items-center gap-2 rounded-full border px-[9px] font-mono text-[10px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00e599] disabled:opacity-60",
                  isProbeEnabled
                    ? "border-[#00e599]/35 bg-[#06241d]/80 text-[#cbd5e1]"
                    : "border-[#92400e]/60 bg-[#23190c] text-[#fbbf24]",
                )}
              >
                <span
                  aria-hidden="true"
                  className={cn(
                    "relative inline-flex h-[14px] w-[24px] shrink-0 items-center rounded-full transition-colors",
                    isProbeEnabled ? "bg-[#00e599]" : "bg-[#334155]",
                  )}
                >
                  <span
                    className={cn(
                      "inline-block size-[10px] rounded-full bg-[#05080f] transition-transform",
                      isProbeEnabled ? "translate-x-[12px]" : "translate-x-[2px]",
                    )}
                  />
                </span>
                <span>{isProbeEnabled ? "Enabled" : "Paused"}</span>
              </button>
            )}

            {!hasSecret ? (
              <span
                data-testid="health-status-pill"
                className="inline-flex h-[26px] items-center gap-1.5 rounded-full border border-[#262d3d] bg-[#131722] px-[10px] text-[11px] font-semibold text-[#cbd5e1]"
              >
                <span className="size-1.5 rounded-full bg-[#f59e0b]" />
                Awaiting Probe
              </span>
            ) : !isProbeEnabled ? (
              <span
                data-testid="health-status-pill"
                className="inline-flex h-[26px] items-center gap-1.5 rounded-full border border-[#92400e]/60 bg-[#23190c] px-[10px] text-[11px] font-semibold text-[#fbbf24]"
              >
                <span className="size-1.5 rounded-full bg-[#f59e0b]" />
                Paused
              </span>
            ) : isFailing ? (
              <>
                <span
                  data-testid="health-status-pill"
                  className="inline-flex h-[26px] items-center gap-1.5 rounded-full border border-[#ef4444]/50 bg-[#2a1216] px-[10px] text-[11px] font-semibold text-[#f87171]"
                >
                  <span className="size-1.5 rounded-full bg-[#ef4444]" />
                  Failing
                </span>
                {openSession && (
                  <span
                    data-testid="health-failing-timer"
                    className="inline-flex h-[26px] items-center rounded-[6px] border border-[#7f1d1d]/70 bg-[#1a0d12] px-[10px] font-mono text-[11px] text-[#fca5a5]"
                  >
                    Open: {openElapsedLabel} · since{" "}
                    {formatUtcHourMinute(openSession.started_at)}
                  </span>
                )}
              </>
            ) : (
              <>
                <span
                  data-testid="health-status-pill"
                  className="inline-flex h-[26px] items-center gap-1.5 rounded-full border border-[#00e599]/40 bg-[#06241d] px-[10px] text-[11px] font-semibold text-[#00e599]"
                >
                  <span className="size-1.5 rounded-full bg-[#00e599]" />
                  Healthy
                </span>
                <span
                  data-testid="health-last-probe-pill"
                  className="inline-flex h-[26px] items-center rounded-[6px] border border-[#1e293b] bg-[#0a0e18] px-[10px] font-mono text-[11px] text-[#94a3b8]"
                >
                  {recoveredHighlight
                    ? `Recovered ${formatProbeRelativeAge(recoveredHighlight.resolvedAt, nowMs)} · PASS`
                    : latestResolvedSession
                      ? `Last probe: PASS · ${formatProbeRelativeAge(latestResolvedSession.resolved_at ?? latestResolvedSession.started_at, nowMs)}`
                      : `Ready · ${formatProbeRelativeAge(config.updated_at, nowMs)}`}
                </span>
              </>
            )}
          </div>
        </div>

        {/* Read-only banner for non-admins (Figma 6G) */}
        {!isTenantAdmin && (
          <div
            role="status"
            data-testid="health-sources-readonly-banner"
            className="mt-[14px] flex flex-wrap items-center gap-[10px] rounded-[8px] border border-[#f59e0b]/45 bg-[#23190c]/85 px-[12px] py-[10px] text-[12px] text-[#fde68a]"
          >
            <span className="rounded-[4px] border border-[#f59e0b]/60 bg-[#f59e0b]/15 px-[7px] py-[2px] font-mono text-[10px] font-semibold uppercase tracking-[0.5px] text-[#fbbf24]">
              Read-Only
            </span>
            <span className="rounded-[4px] border border-[#334155] bg-[#0f131d] px-[7px] py-[2px] font-mono text-[10px] uppercase text-[#cbd5e1]">
              Role: {roleLabel}
            </span>
            <span>
              Only Tenant Administrators can reveal HMAC signing secrets, change
              probe sources, or rotate credentials.
            </span>
          </div>
        )}

        {actionError && (
          <div
            role="alert"
            data-testid="health-sources-action-error"
            className="mt-[12px] flex items-center justify-between gap-2 rounded-[8px] border border-[#ef4444]/40 bg-[#ef4444]/10 px-[12px] py-[8px] text-[12px] text-[#fca5a5]"
          >
            <span className="flex items-center gap-2">
              <AlertCircle
                className="size-[14px] shrink-0 text-[#ef4444]"
                aria-hidden="true"
              />
              <span>{actionError}</span>
            </span>
            <button
              type="button"
              onClick={() => setActionError(null)}
              aria-label="Dismiss error"
              className="rounded p-0.5 text-[#fca5a5] hover:bg-[#ef4444]/20"
            >
              <X className="size-3.5" aria-hidden="true" />
            </button>
          </div>
        )}

        {/* HMAC Signing Secret Block */}
        <div className="mt-[16px]">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.6px] text-[#64748b]">
              HMAC SIGNING SECRET
            </span>
            <span className="font-mono text-[10px] text-[#64748b]">
              Signs X-Health-Signature-256 (HMAC-SHA256) header over raw JSON body
            </span>
          </div>

          {!hasSecret && isTenantAdmin ? (
            /* 6A: First-Time Setup */
            <div
              data-testid="health-secret-first-time-setup"
              className="mt-[6px] flex flex-wrap items-center justify-between gap-3 rounded-[8px] border border-[#262d3d] bg-[#0a0e18] px-[12px] py-[8px]"
            >
              <div className="flex items-center gap-2 text-[12px] text-[#cbd5e1]">
                <span
                  aria-hidden="true"
                  className="size-1.5 shrink-0 rounded-full bg-[#f59e0b]"
                />
                <span>
                  No HMAC signing secret generated yet — create a secret to
                  activate probe signature checks.
                </span>
              </div>

              <button
                type="button"
                data-testid="generate-signing-secret-button"
                disabled={rotateSecretMutation.isPending}
                onClick={() => void handleGenerateFirstSecret()}
                className="inline-flex h-[32px] shrink-0 items-center gap-1.5 rounded-[7px] bg-[#00e599] px-[14px] text-[12px] font-semibold text-[#05080f] transition-colors hover:bg-[#2df0b0] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00e599] disabled:cursor-not-allowed disabled:opacity-60"
              >
                {rotateSecretMutation.isPending ? (
                  <>
                    <Loader2
                      className="size-[13px] animate-spin"
                      aria-hidden="true"
                    />
                    <span>Generating...</span>
                  </>
                ) : (
                  <>
                    <KeyRound className="size-[13px]" aria-hidden="true" />
                    <span>Generate Signing Secret</span>
                  </>
                )}
              </button>
            </div>
          ) : !isTenantAdmin ? (
            /* 6G: Read-Only Non-Admin Secret Row */
            <div
              data-testid="health-secret-readonly"
              className="mt-[6px] flex flex-wrap items-center justify-between gap-3 rounded-[8px] border border-[#1e293b] bg-[#0a0e18] px-[12px] py-[8px]"
            >
              <div className="flex min-w-0 items-center gap-2 font-mono text-[12px] text-[#64748b]">
                <Lock className="size-[13px] shrink-0" aria-hidden="true" />
                <span className="truncate">
                  {maskedDisplay}{" "}
                  <span className="text-[#475569]">
                    (Hidden for {roleLabel} role)
                  </span>
                </span>
              </div>

              <div className="flex items-center gap-1.5">
                <button
                  type="button"
                  disabled
                  data-testid="secret-reveal-button"
                  className="inline-flex h-[30px] cursor-not-allowed items-center gap-1.5 rounded-[6px] border border-[#1e293b] bg-[#131722] px-[10px] text-[11px] font-medium text-[#64748b] opacity-75"
                >
                  <Lock className="size-[12px]" aria-hidden="true" />
                  <span>Reveal</span>
                </button>
                <button
                  type="button"
                  disabled
                  data-testid="rotate-secret-button"
                  className="inline-flex h-[30px] cursor-not-allowed items-center gap-1.5 rounded-[6px] border border-[#1e293b] bg-[#131722] px-[10px] text-[11px] font-medium text-[#64748b] opacity-75"
                >
                  <Lock className="size-[12px]" aria-hidden="true" />
                  <span>Rotate Secret</span>
                </button>
              </div>
            </div>
          ) : (
            /* 6B / 6C: Configured Admin Secret Row */
            <div className="mt-[6px] space-y-1.5">
              <div
                className={cn(
                  "flex flex-wrap items-center justify-between gap-3 rounded-[8px] border px-[12px] py-[8px] transition-colors",
                  isSecretRevealed && plaintextSecret
                    ? "border-[#00e599]/60 bg-[#071514]"
                    : "border-[#1e293b] bg-[#0a0e18]",
                )}
              >
                <div className="flex min-w-0 flex-1 items-center gap-2">
                  <code
                    data-testid="health-secret-value"
                    className={cn(
                      "truncate font-mono text-[12px]",
                      isSecretRevealed && plaintextSecret
                        ? "font-medium text-[#00e599]"
                        : "text-[#e2e8f0]",
                    )}
                  >
                    {isSecretRevealed && plaintextSecret
                      ? plaintextSecret
                      : maskedDisplay}
                  </code>

                  {isSecretRevealed && plaintextSecret && (
                    <span className="shrink-0 rounded-[4px] border border-[#00e599]/40 bg-[#00e599]/10 px-[6px] py-[1px] font-mono text-[9px] font-semibold uppercase tracking-[0.5px] text-[#00e599]">
                      REVEALED
                    </span>
                  )}
                </div>

                <div className="flex flex-wrap items-center gap-1.5">
                  <button
                    type="button"
                    data-testid="secret-reveal-button"
                    onClick={handleToggleSecretVisibility}
                    className="inline-flex h-[30px] items-center gap-1.5 rounded-[6px] border border-[#262d3d] bg-[#131722] px-[10px] text-[11px] font-medium text-[#cbd5e1] transition-colors hover:border-[#3b455e] hover:text-[#f8fafc] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00e599]"
                  >
                    {isSecretRevealed && plaintextSecret ? (
                      <>
                        <EyeOff className="size-[12px]" aria-hidden="true" />
                        <span>Hide</span>
                      </>
                    ) : (
                      <>
                        <Eye className="size-[12px]" aria-hidden="true" />
                        <span>Reveal</span>
                      </>
                    )}
                  </button>

                  <button
                    type="button"
                    data-testid="secret-copy-button"
                    onClick={() => void handleCopySecret()}
                    className={cn(
                      "inline-flex h-[30px] items-center gap-1.5 rounded-[6px] border px-[10px] text-[11px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00e599]",
                      copiedKey === "secret"
                        ? "border-[#00e599]/55 bg-[#06241d] text-[#00e599]"
                        : "border-[#262d3d] bg-[#131722] text-[#cbd5e1] hover:border-[#3b455e] hover:text-[#f8fafc]",
                    )}
                  >
                    {copiedKey === "secret" ? (
                      <>
                        <Check className="size-[12px]" aria-hidden="true" />
                        <span>Copied</span>
                      </>
                    ) : (
                      <>
                        <Copy className="size-[12px]" aria-hidden="true" />
                        <span>Copy</span>
                      </>
                    )}
                  </button>

                  <button
                    type="button"
                    data-testid="rotate-secret-button"
                    onClick={() => {
                      setRotateError(null);
                      setRotateModalOpen(true);
                    }}
                    className="inline-flex h-[30px] items-center gap-1.5 rounded-[6px] border border-[#92400e]/70 bg-[#23190c] px-[10px] text-[11px] font-semibold text-[#f59e0b] transition-colors hover:border-[#f59e0b]/70 hover:bg-[#2d2010] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#f59e0b]"
                  >
                    <RotateCw className="size-[12px]" aria-hidden="true" />
                    <span>Rotate Secret</span>
                  </button>
                </div>
              </div>

              {secretHint && (
                <p
                  data-testid="health-secret-hint"
                  className="text-[11px] text-[#fbbf24]"
                >
                  {secretHint}
                </p>
              )}
            </div>
          )}
        </div>

        {/* 2-Column Row: Webhook URL + Probe Source */}
        <div className="mt-[14px] grid grid-cols-1 gap-[12px] lg:grid-cols-[minmax(0,1.65fr)_minmax(240px,1fr)]">
          <div className="min-w-0">
            <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.6px] text-[#64748b]">
              HEALTH WEBHOOK URL
            </span>
            <div className="mt-[6px] flex h-[40px] items-center justify-between gap-2 rounded-[8px] border border-[#1e293b] bg-[#0a0e18] px-[10px]">
              <div className="flex min-w-0 items-center gap-2">
                <span className="shrink-0 rounded-[4px] border border-[#00e599]/35 bg-[#06241d] px-[6px] py-[2px] font-mono text-[10px] font-semibold text-[#00e599]">
                  POST
                </span>
                <code
                  data-testid="health-webhook-url"
                  className="truncate font-mono text-[12px] text-[#cbd5e1]"
                >
                  {config.webhook_url}
                </code>
              </div>

              <button
                type="button"
                data-testid="copy-webhook-url-button"
                onClick={() => void handleCopyText("url", config.webhook_url)}
                className={cn(
                  "inline-flex h-[28px] shrink-0 items-center gap-1.5 rounded-[6px] border px-[10px] font-mono text-[11px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00e599]",
                  copiedKey === "url"
                    ? "border-[#00e599]/55 bg-[#06241d] text-[#00e599]"
                    : "border-[#262d3d] bg-[#131722] text-[#cbd5e1] hover:border-[#3b455e] hover:text-[#f8fafc]",
                )}
              >
                {copiedKey === "url" ? (
                  <>
                    <Check className="size-[11px]" aria-hidden="true" />
                    <span>Copied</span>
                  </>
                ) : (
                  <>
                    <Copy className="size-[11px]" aria-hidden="true" />
                    <span>Copy URL</span>
                  </>
                )}
              </button>
            </div>
          </div>

          <div className="min-w-0">
            <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.6px] text-[#64748b]">
              PROBE SOURCE
            </span>
            <div className="mt-[6px]">
              <HealthProbeSourceSelect
                value={activeSourceId}
                onChange={(nextValue) => void handleProbeSourceChange(nextValue)}
                readOnly={!isTenantAdmin}
                isSaving={updateConfigMutation.isPending}
              />
            </div>
          </div>
        </div>

        {/* Payload & Signature Reference Snippet */}
        <div className="mt-[12px] overflow-x-auto rounded-[8px] border border-[#1e293b] bg-[#070a11] px-[12px] py-[8px] font-mono text-[11px]">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span>
              <span className="font-semibold text-[#22d3ee]">HEADER</span>{" "}
              <code className="text-[#cbd5e1]">
                X-Health-Signature-256: sha256=&lt;hmac&gt;
              </code>
            </span>
            <span aria-hidden="true" className="text-[#475569]">
              ·
            </span>
            <span>
              <span className="font-semibold text-[#00e599]">PAYLOAD</span>{" "}
              <code className="text-[#cbd5e1]">
                {"{"} &quot;status&quot;: &quot;FAIL&quot; | &quot;PASS&quot;,
                &quot;probe_source&quot;: &quot;{activeSourceMeta.payloadSource}
                &quot; {"}"}
              </code>
            </span>
          </div>
        </div>
      </section>

      {/* Card 2: Recent Health Sessions */}
      <section
        data-testid="recent-health-sessions-card"
        aria-labelledby="recent-health-sessions-heading"
        className="min-w-0 overflow-hidden rounded-[12px] border border-[#1e293b] bg-[#0d121f]"
      >
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[#1e293b] px-[20px] py-[13px]">
          <div className="flex items-center gap-2.5">
            <h2
              id="recent-health-sessions-heading"
              className="font-mono text-[11px] font-semibold uppercase tracking-[0.6px] text-[#94a3b8]"
            >
              RECENT HEALTH SESSIONS
            </h2>

            {openSession ? (
              <span
                data-testid="health-sessions-badge"
                className="inline-flex h-[20px] items-center rounded-full border border-[#ef4444]/45 bg-[#2a1216] px-2 font-mono text-[10px] font-semibold text-[#f87171]"
              >
                1 OPEN
              </span>
            ) : (
              <span
                data-testid="health-sessions-badge"
                className={cn(
                  "inline-flex h-[20px] min-w-[22px] items-center justify-center rounded-[5px] border px-1.5 font-mono text-[11px] font-semibold",
                  sessions.length > 0
                    ? "border-[#00e599]/35 bg-[#06241d] text-[#00e599]"
                    : "border-[#262d3d] bg-[#131722] text-[#7c839b]",
                )}
              >
                {sessions.length}
              </span>
            )}
          </div>

          <p
            data-testid="health-sessions-summary"
            className={cn(
              "text-[11px]",
              openSession ? "text-[#f87171]" : "text-[#7c839b]",
            )}
          >
            {!isTenantAdmin
              ? "Read-only health timeline · Tenant Admin required for webhook changes"
              : openSession
                ? `Failure opened at ${formatUtcHourMinute(openSession.started_at)} · Awaiting PASS recovery signal`
                : sessions.length === 0
                  ? "Waiting for first signed FAIL or PASS webhook event"
                  : recoveredHighlight
                    ? `Mean recovery (30d): ${mean30dLabel} · Latest incident resolved in ${formatRecoveryDuration(recoveredHighlight.recoverySeconds)}`
                    : `Mean recovery (30d): ${mean30dLabel} · Active source: ${activeSourceMeta.shortLabel}`}
          </p>
        </div>

        {signalsQuery.isLoading && sessions.length === 0 ? (
          <div className="flex h-[150px] items-center justify-center text-[12px] text-[#94a3b8]">
            <Loader2
              className="mr-2 size-4 animate-spin text-[#00e599]"
              aria-hidden="true"
            />
            Loading production health sessions...
          </div>
        ) : signalsQuery.isError && sessions.length === 0 ? (
          <div
            role="alert"
            className="px-[20px] py-[28px] text-center text-[12px] text-[#fca5a5]"
          >
            {signalsQuery.error instanceof Error
              ? signalsQuery.error.message
              : "Failed to load recent health sessions."}
          </div>
        ) : sessions.length === 0 ? (
          <div
            data-testid="health-sessions-empty"
            className="flex flex-col items-center justify-center px-[20px] py-[56px] text-center"
          >
            <div className="flex size-[38px] items-center justify-center rounded-[9px] border border-[#00e599]/30 bg-[#06241d] text-[#00e599]">
              <ShieldCheck className="size-[18px]" aria-hidden="true" />
            </div>
            <p className="mt-3 text-[13px] font-semibold text-[#f8fafc]">
              No health sessions recorded yet
            </p>
            <p className="mt-1 max-w-[440px] text-[12px] leading-[17px] text-[#7c839b]">
              {!hasSecret
                ? "Generate an HMAC signing secret above and point your production probe webhook to start tracking uptime & recovery."
                : "Point your production probe webhook above and send a signed FAIL or PASS signal to track failure sessions and MTTR recovery."}
            </p>
          </div>
        ) : (
          <div className="w-full overflow-x-auto">
            <table
              data-testid="health-sessions-table"
              className="w-full min-w-[720px] border-collapse text-left text-[12px]"
            >
              <caption className="sr-only">
                Recent production health sessions and incident recovery times
              </caption>
              <thead>
                <tr className="border-b border-[#1e293b] bg-[#0b0f1b] font-mono text-[10px] font-semibold uppercase tracking-[0.6px] text-[#64748b]">
                  <th scope="col" className="px-[20px] py-[10px]">
                    Session Status
                  </th>
                  <th scope="col" className="px-[16px] py-[10px]">
                    Probe Source
                  </th>
                  <th scope="col" className="px-[16px] py-[10px]">
                    Started At
                  </th>
                  <th scope="col" className="px-[16px] py-[10px]">
                    Resolved At
                  </th>
                  <th scope="col" className="px-[20px] py-[10px] text-right">
                    Recovery Time
                  </th>
                </tr>
              </thead>
              <tbody>
                {sessions.map((session) => {
                  const isOpen = session.status === "OPEN";
                  const isJustRecovered =
                    !isOpen && recoveredHighlight?.id === session.id;
                  const rowSourceMeta = getProbeSourceMeta(session.probe_source);
                  const liveOrRecordedSeconds = isOpen
                    ? Math.max(
                        0,
                        Math.floor(
                          (nowMs - Date.parse(session.started_at)) / 1000,
                        ),
                      )
                    : session.recovery_time_seconds;

                  return (
                    <tr
                      key={session.id}
                      data-testid={`health-session-row-${session.id}`}
                      className={cn(
                        "border-b border-[#1e293b]/70 last:border-b-0 transition-colors",
                        isOpen
                          ? "bg-[#241016]/55"
                          : isJustRecovered
                            ? "bg-[#071c17]/65"
                            : "hover:bg-[#131928]/60",
                      )}
                    >
                      <td className="px-[20px] py-[13px] align-middle">
                        {isOpen ? (
                          <span className="inline-flex items-center gap-1.5 rounded-full border border-[#7f1d1d]/70 bg-[#2a1216] px-2.5 py-0.5 text-[11px] font-semibold text-[#f87171]">
                            <span className="size-1.5 rounded-full bg-[#ef4444]" />
                            Open
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1.5 rounded-full border border-[#00e599]/35 bg-[#06241d] px-2.5 py-0.5 text-[11px] font-semibold text-[#00e599]">
                            <span className="size-1.5 rounded-full bg-[#00e599]" />
                            Resolved
                          </span>
                        )}
                      </td>

                      <td className="px-[16px] py-[13px] align-middle">
                        <span className="inline-flex items-center gap-2 text-[12px] font-medium text-[#e2e8f0]">
                          <span
                            aria-hidden="true"
                            className={cn(
                              "size-1.5 rounded-full",
                              isOpen ? "bg-[#ef4444]" : "bg-[#38bdf8]",
                            )}
                          />
                          {rowSourceMeta.label}
                        </span>
                      </td>

                      <td className="px-[16px] py-[13px] align-middle whitespace-nowrap font-mono text-[11px] text-[#cbd5e1]">
                        {formatHealthSessionTimestamp(session.started_at)}
                      </td>

                      <td className="px-[16px] py-[13px] align-middle whitespace-nowrap font-mono text-[11px]">
                        {isOpen || !session.resolved_at ? (
                          <span className="text-[#f87171]">
                            – (Incident active)
                          </span>
                        ) : (
                          <span className="text-[#cbd5e1]">
                            {formatHealthSessionTimestamp(session.resolved_at)}
                          </span>
                        )}
                      </td>

                      <td
                        className={cn(
                          "px-[20px] py-[13px] text-right align-middle whitespace-nowrap font-mono text-[11px]",
                          isOpen
                            ? "font-semibold text-[#f87171]"
                            : isJustRecovered
                              ? "font-semibold text-[#00e599]"
                              : "font-medium text-[#e2e8f0]",
                        )}
                      >
                        {isOpen
                          ? `${formatRecoveryDuration(liveOrRecordedSeconds)} running`
                          : formatRecoveryDuration(liveOrRecordedSeconds)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <RotateHealthSecretModal
        open={rotateModalOpen}
        onClose={() => setRotateModalOpen(false)}
        maskedSecret={maskedDisplay}
        probeSourceLabel={activeSourceMeta.label}
        webhookUrl={config.webhook_url}
        isPending={rotateSecretMutation.isPending}
        errorMessage={rotateError}
        onConfirm={() => void handleConfirmRotateSecret()}
      />
    </div>
  );
}
