"use client";

import {
  AlertTriangle,
  CheckCircle2,
  Loader2,
  RefreshCw,
  X,
} from "lucide-react";
import {
  getAiModeLabel,
  getReindexProgressPercent,
  isReindexActive,
} from "@/lib/tenant-settings-api";
import type { TenantSettingsResponse } from "@/types/tenant-settings";

interface ReindexStatusBannerProps {
  settings: TenantSettingsResponse;
  isFetching: boolean;
  dismissedJobKey: string | null;
  onRefresh: () => void;
  onDismiss: (jobKey: string) => void;
}

function formatShortJobId(jobId: string): string {
  return jobId.length > 12 ? `${jobId.slice(0, 8)}...` : jobId;
}

export function ReindexStatusBanner({
  settings,
  isFetching,
  dismissedJobKey,
  onRefresh,
  onDismiss,
}: ReindexStatusBannerProps) {
  const normalizedStatus = settings.reindex_status.trim().toLowerCase();
  const active = isReindexActive(settings);

  if (!active && normalizedStatus === "idle") {
    return null;
  }

  const jobKey = `${normalizedStatus}:${settings.reindex_job_id ?? settings.updated_at}`;

  if (!active && dismissedJobKey === jobKey) {
    return null;
  }

  const modeLabel = getAiModeLabel(settings.ai_processing_mode);
  const progressPercent = getReindexProgressPercent(
    settings.reindexed_count,
    settings.reindex_total_count,
  );

  if (active) {
    const isRunning = normalizedStatus === "running";
    const hasTotal = settings.reindex_total_count > 0;
    const progressText = hasTotal
      ? `Rebuilding ADR vector index — ${settings.reindexed_count} / ${settings.reindex_total_count} approved ADRs re-embedded (${progressPercent ?? 0}%) · Model: ${settings.active_embedding_model}`
      : isRunning
        ? `Scanning approved ADRs and generating fresh vector embeddings · Model: ${settings.active_embedding_model}`
        : `Job queued on high-priority ARQ worker — re-embedding will begin shortly · Model: ${settings.active_embedding_model}`;

    return (
      <section
        data-testid="reindex-status-banner"
        data-reindex-status={isRunning ? "running" : "queued"}
        aria-label="Live ADR re-indexing status"
        className="rounded-[10px] border border-[#38bdf8]/45 bg-[#071726] px-[14px] py-[10px]"
      >
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 flex-1 items-center gap-[10px]">
            <div className="flex size-[28px] shrink-0 items-center justify-center rounded-[8px] border border-[#38bdf8]/40 bg-[#0c2740] text-[#38bdf8]">
              <Loader2 className="size-[14px] animate-spin" aria-hidden="true" />
            </div>

            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-[6px]">
                <span
                  data-testid="reindex-status-badge"
                  className="inline-flex items-center gap-[5px] rounded-[5px] border border-[#38bdf8]/50 bg-[#0e2d48] px-[7px] py-[2px] font-mono text-[9.5px] font-bold uppercase tracking-[0.04em] text-[#7dd3fc]"
                >
                  <span
                    className="size-[5px] rounded-full bg-[#38bdf8]"
                    aria-hidden="true"
                  />
                  <span>
                    {isRunning ? "Re-Indexing · Running" : "Re-Indexing · Queued"}
                  </span>
                </span>
                <span className="rounded-[5px] border border-[#1e293b] bg-[#0b1420] px-[7px] py-[2px] font-mono text-[9.5px] font-medium text-[#cbd5e1]">
                  {modeLabel} · {settings.active_embedding_dimensions}d
                </span>
                {settings.reindex_job_id && (
                  <code
                    title={settings.reindex_job_id}
                    className="rounded-[5px] border border-[#1e293b] bg-[#0b1420] px-[7px] py-[2px] font-mono text-[9.5px] text-[#94a3b8]"
                  >
                    job:{formatShortJobId(settings.reindex_job_id)}
                  </code>
                )}
              </div>

              <p
                data-testid="reindex-progress-summary"
                className="mt-[3px] truncate text-[11px] font-medium leading-[15px] text-[#bae6fd]"
                title={progressText}
              >
                {progressText}
              </p>
            </div>
          </div>

          <div className="flex shrink-0 items-center gap-[8px]">
            <span className="inline-flex items-center gap-[5px] rounded-[5px] border border-[#38bdf8]/35 bg-[#0b1f33] px-[7px] py-[3px] font-mono text-[9.5px] font-medium text-[#7dd3fc]">
              <span
                className="size-[5px] animate-pulse rounded-full bg-[#38bdf8]"
                aria-hidden="true"
              />
              <span>Live polling 2.5s</span>
            </span>
            <button
              type="button"
              onClick={onRefresh}
              disabled={isFetching}
              data-testid="reindex-refresh-button"
              className="inline-flex h-[26px] items-center gap-[5px] rounded-[6px] border border-[#38bdf8]/40 bg-[#0f2840] px-[9px] text-[10.5px] font-medium text-[#e0f2fe] transition-colors hover:bg-[#143554] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#38bdf8] disabled:cursor-not-allowed disabled:opacity-60"
            >
              <RefreshCw
                className={`size-[11px] ${isFetching ? "animate-spin" : ""}`}
                aria-hidden="true"
              />
              <span>Refresh</span>
            </button>
          </div>
        </div>

        <div className="mt-[8px] flex items-center gap-[10px]">
          <progress
            data-testid="reindex-progress-bar"
            aria-label={
              hasTotal
                ? "ADR vector re-indexing progress"
                : "ADR vector re-indexing in progress"
            }
            {...(hasTotal
              ? {
                  value: Math.min(
                    settings.reindexed_count,
                    settings.reindex_total_count,
                  ),
                  max: settings.reindex_total_count,
                }
              : {})}
            className="manta-reindex-progress block h-[6px] min-w-0 flex-1"
          />
          <span className="shrink-0 font-mono text-[10px] font-bold text-[#7dd3fc]">
            {hasTotal
              ? `${settings.reindexed_count} / ${settings.reindex_total_count} (${progressPercent ?? 0}%)`
              : isRunning
                ? "In progress..."
                : "Queued..."}
          </span>
        </div>
      </section>
    );
  }

  if (normalizedStatus === "failed") {
    const hasProcessed = settings.reindex_total_count > 0;
    return (
      <div
        role="alert"
        data-testid="reindex-status-banner"
        data-reindex-status="failed"
        className="rounded-[10px] border border-[#ef4444]/50 bg-[#210e14] px-[14px] py-[9px]"
      >
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 flex-1 items-center gap-[10px]">
            <div className="flex size-[28px] shrink-0 items-center justify-center rounded-[8px] border border-[#ef4444]/45 bg-[#38131d] text-[#f87171]">
              <AlertTriangle className="size-[14px]" aria-hidden="true" />
            </div>

            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-[6px]">
                <span
                  data-testid="reindex-status-badge"
                  className="inline-flex items-center gap-[5px] rounded-[5px] border border-[#ef4444]/55 bg-[#3d1520] px-[7px] py-[2px] font-mono text-[9.5px] font-bold uppercase tracking-[0.04em] text-[#fca5a5]"
                >
                  <span
                    className="size-[5px] rounded-full bg-[#ef4444]"
                    aria-hidden="true"
                  />
                  <span>Re-Index · Failed</span>
                </span>
                <span className="rounded-[5px] border border-[#451a26] bg-[#180c12] px-[7px] py-[2px] font-mono text-[9.5px] font-medium text-[#e2e8f0]">
                  {modeLabel} · {settings.active_embedding_dimensions}d
                </span>
                {settings.reindex_job_id && (
                  <code
                    title={settings.reindex_job_id}
                    className="rounded-[5px] border border-[#451a26] bg-[#180c12] px-[7px] py-[2px] font-mono text-[9.5px] text-[#94a3b8]"
                  >
                    job:{formatShortJobId(settings.reindex_job_id)}
                  </code>
                )}
                {hasProcessed && (
                  <span className="rounded-[5px] border border-[#451a26] bg-[#180c12] px-[7px] py-[2px] font-mono text-[9.5px] font-medium text-[#fca5a5]">
                    {settings.reindexed_count} / {settings.reindex_total_count} processed
                  </span>
                )}
              </div>

              <p
                data-testid="reindex-error-detail"
                className="mt-[3px] text-[11px] font-medium leading-[15px] text-[#fecaca]"
              >
                {settings.reindex_error?.trim() ||
                  "The background worker could not finish re-embedding approved ADRs. Verify AI provider credentials and worker connectivity."}
              </p>
            </div>
          </div>

          <div className="flex shrink-0 items-center gap-[8px]">
            <button
              type="button"
              onClick={onRefresh}
              disabled={isFetching}
              data-testid="reindex-refresh-button"
              className="inline-flex h-[26px] items-center gap-[5px] rounded-[6px] border border-[#ef4444]/45 bg-[#38131d] px-[9px] text-[10.5px] font-medium text-[#fee2e2] transition-colors hover:bg-[#4c1826] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#ef4444] disabled:opacity-60"
            >
              <RefreshCw
                className={`size-[11px] ${isFetching ? "animate-spin" : ""}`}
                aria-hidden="true"
              />
              <span>Check Status</span>
            </button>
            <button
              type="button"
              onClick={() => onDismiss(jobKey)}
              aria-label="Dismiss re-index failure banner"
              className="flex size-[24px] items-center justify-center rounded-[6px] border border-[#ef4444]/30 bg-[#2b1018] text-[#fca5a5] transition-colors hover:bg-[#3d1520] hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#ef4444]"
            >
              <X className="size-[12px]" aria-hidden="true" />
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (normalizedStatus === "completed") {
    const countCopy =
      settings.reindex_total_count > 0
        ? `${settings.reindexed_count} / ${settings.reindex_total_count} approved ADRs indexed`
        : "0 approved ADRs required re-embedding";

    return (
      <section
        data-testid="reindex-status-banner"
        data-reindex-status="completed"
        aria-label="Completed ADR re-indexing status"
        className="rounded-[10px] border border-[#00e599]/45 bg-[#061f1a] px-[14px] py-[9px]"
      >
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 flex-1 items-center gap-[10px]">
            <div className="flex size-[24px] shrink-0 items-center justify-center rounded-[7px] border border-[#00e599]/40 bg-[#082e24] text-[#00e599]">
              <CheckCircle2 className="size-[13px]" aria-hidden="true" />
            </div>

            <div className="flex min-w-0 flex-wrap items-center gap-[10px]">
              <span
                data-testid="reindex-status-badge"
                className="inline-flex shrink-0 items-center rounded-[5px] border border-[#00e599]/45 bg-[#083326] px-[7px] py-[2px] font-mono text-[9.5px] font-bold uppercase tracking-[0.04em] text-[#00e599]"
              >
                Re-Index · Completed
              </span>
              <p
                data-testid="reindex-progress-summary"
                className="text-[11.5px] font-medium leading-[16px] text-[#d1fae5]"
              >
                ADR vector index synchronized — {countCopy} in{" "}
                {settings.active_embedding_dimensions}d (
                {settings.active_embedding_model}).
              </p>
            </div>
          </div>

          <button
            type="button"
            onClick={() => onDismiss(jobKey)}
            aria-label="Dismiss completed re-index banner"
            data-testid="reindex-dismiss-button"
            className="flex size-[24px] shrink-0 items-center justify-center rounded-[6px] border border-[#00e599]/25 bg-[#0a2921] text-[#94a3b8] transition-colors hover:bg-[#0d382d] hover:text-[#f8fafc] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#00e599]"
          >
            <X className="size-[12px]" aria-hidden="true" />
          </button>
        </div>
      </section>
    );
  }

  return null;
}
