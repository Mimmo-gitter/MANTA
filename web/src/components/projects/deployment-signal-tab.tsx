"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  AlertCircle,
  Check,
  CheckCircle2,
  Copy,
  Loader2,
  RotateCcw,
} from "lucide-react";
import { isApiHttpError } from "@/lib/api-client";
import {
  useDeploymentSignalQuery,
  useUpdateDeploymentSignalMutation,
} from "@/hooks/use-projects";
import { usePermissions } from "@/hooks/use-permissions";
import { getPrimaryRoleLabel } from "@/lib/permissions";
import type { DeploymentSignalType, ProjectItem } from "@/types/project";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const SIGNAL_LABELS: Record<DeploymentSignalType, string> = {
  github_deployments_api: "GitHub Deployments API",
  argocd_postsync_webhook: "ArgoCD / GitOps Webhook",
  direct_deployment_api: "Direct Deployment Ingestion API",
};

function normalizeSignal(value?: string | null): DeploymentSignalType {
  if (
    value === "argocd_postsync_webhook" ||
    value === "direct_deployment_api"
  ) {
    return value;
  }
  return "github_deployments_api";
}

export function DeploymentSignalTab({ project }: { project: ProjectItem }) {
  const { can, roles } = usePermissions();
  const isTenantAdmin = can("MANAGE_WEBHOOK_INTEGRATIONS");
  const roleLabel = getPrimaryRoleLabel(roles);

  const signalQuery = useDeploymentSignalQuery(project.id);
  const updateMutation = useUpdateDeploymentSignalMutation(project.id);

  const authoritativeSignal = normalizeSignal(
    signalQuery.data?.authoritative_deployment_signal ||
      project.authoritative_deployment_signal ||
      project.authoritative_cd_signal,
  );

  const [selectedSignal, setSelectedSignal] =
    useState<DeploymentSignalType>(authoritativeSignal);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [toastInfo, setToastInfo] = useState<{
    message: string;
    previousSignal: DeploymentSignalType | null;
  } | null>(null);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  useEffect(() => {
    setSelectedSignal(authoritativeSignal);
  }, [authoritativeSignal]);

  useEffect(() => {
    if (!toastInfo) return;
    const timer = window.setTimeout(() => setToastInfo(null), 6000);
    return () => window.clearTimeout(timer);
  }, [toastInfo]);

  const isDirty = selectedSignal !== authoritativeSignal;
  const gitopsWebhookUrl = `${API_URL}/api/v1/webhooks/gitops`;
  const directApiEndpoint = `${API_URL}/api/v1/projects/${project.slug}/deployments`;
  const directApiCurl = `curl -X POST ${directApiEndpoint} -H "Authorization: Bearer <SERVICE_TOKEN>" -H "Content-Type: application/json" -d '{"commit_sha":"'$SHA'"}'`;

  const handleCopy = async (key: string, value: string) => {
    try {
      await navigator.clipboard.writeText(value);
      setCopiedKey(key);
      window.setTimeout(() => setCopiedKey(null), 2000);
    } catch {
      setErrorMessage("Clipboard access unavailable in this browser context.");
    }
  };

  const handleSave = async () => {
    if (!isTenantAdmin || !isDirty) return;
    setErrorMessage(null);
    const previousSignal = authoritativeSignal;

    try {
      await updateMutation.mutateAsync({
        authoritative_deployment_signal: selectedSignal,
      });
      setToastInfo({
        message: `Authoritative deployment signal saved (${SIGNAL_LABELS[selectedSignal]}).`,
        previousSignal,
      });
    } catch (error) {
      if (isApiHttpError(error) && error.status === 403) {
        setErrorMessage(
          "Only Tenant Administrators can update the authoritative deployment signal.",
        );
        return;
      }
      setErrorMessage(
        error instanceof Error
          ? error.message
          : "Failed to save deployment signal configuration.",
      );
    }
  };

  const handleUndo = async () => {
    if (!toastInfo?.previousSignal || !isTenantAdmin) return;
    const revertTarget = toastInfo.previousSignal;
    setToastInfo(null);
    setErrorMessage(null);
    try {
      await updateMutation.mutateAsync({
        authoritative_deployment_signal: revertTarget,
      });
      setSelectedSignal(revertTarget);
      setToastInfo({
        message: `Reverted deployment signal to ${SIGNAL_LABELS[revertTarget]}.`,
        previousSignal: null,
      });
    } catch (error) {
      setErrorMessage(
        error instanceof Error
          ? error.message
          : "Failed to revert deployment signal.",
      );
    }
  };

  return (
    <div className="space-y-[16px]">
      <div className="rounded-[12px] border border-[#1e293b] bg-[#0d121f] p-[20px]">
        <h2 className="text-[16px] font-semibold text-[#f8fafc]">
          Authoritative Production Deployment Signal
        </h2>
        <p className="mt-[4px] text-[12px] text-[#94a3b8]">
          Configure the authoritative delivery pipeline source used to calculate
          verified DORA Lead Time and Change Failure Rate.
        </p>

        {/* Read-only banner for non-admins (Figma 4F) OR DORA accuracy callout (4A) */}
        {!isTenantAdmin ? (
          <div
            role="status"
            data-testid="deployment-signal-readonly-banner"
            className="mt-[14px] flex flex-wrap items-center gap-[10px] rounded-[8px] border border-[#f59e0b]/45 bg-[#f59e0b]/10 px-[12px] py-[10px] text-[12px] text-[#fde68a]"
          >
            <span className="rounded-[4px] border border-[#f59e0b]/60 bg-[#f59e0b]/15 px-[7px] py-[2px] font-mono text-[10px] font-semibold uppercase tracking-[0.5px] text-[#fbbf24]">
              Read-Only
            </span>
            <span className="rounded-[4px] border border-[#334155] bg-[#0f131d] px-[7px] py-[2px] font-mono text-[10px] uppercase text-[#cbd5e1]">
              Role: {roleLabel}
            </span>
            <span>
              Only Tenant Administrators can configure authoritative production
              deployment signals. Contact your workspace administrator to request
              changes.
            </span>
          </div>
        ) : (
          <div className="mt-[14px] flex flex-wrap items-center gap-[10px] rounded-[8px] border border-[#1e293b] bg-[#0a0e18] px-[12px] py-[10px] text-[12px] text-[#cbd5e1]">
            <span className="rounded-[4px] border border-[#00e599]/30 bg-[#00e599]/10 px-[7px] py-[2px] font-mono text-[10px] font-semibold uppercase tracking-[0.5px] text-[#00e599]">
              DORA Accuracy
            </span>
            <span>
              Pull request merges are never treated as production deployments.
              M.A.N.T.A. calculates lead time and failure rates strictly from
              verified production deployment events.
            </span>
          </div>
        )}

        {signalQuery.isError && (
          <div
            role="alert"
            className="mt-[12px] flex items-center gap-2 rounded-[8px] border border-[#ef4444]/40 bg-[#ef4444]/10 px-[12px] py-[8px] text-[12px] text-[#fca5a5]"
          >
            <AlertCircle className="size-[14px] shrink-0 text-[#ef4444]" aria-hidden="true" />
            <span>
              Could not refresh deployment signal status from server; displaying
              cached project value.
            </span>
          </div>
        )}

        {/* Radio Option Cards */}
        <fieldset className="mt-[14px] space-y-[10px]" disabled={!isTenantAdmin}>
          <legend className="sr-only">
            Authoritative production deployment signal source
          </legend>

          {/* Option 1: GitHub Deployments API */}
          <label
            className={`block rounded-[10px] border p-[14px] transition-colors ${
              !isTenantAdmin ? "cursor-not-allowed opacity-85" : "cursor-pointer"
            } ${
              selectedSignal === "github_deployments_api"
                ? "border-[#00e599] bg-[#0a141d]"
                : "border-[#1e293b] bg-[#0a0e18] hover:border-[#334155]"
            }`}
          >
            <div className="flex items-start gap-[12px]">
              <input
                type="radio"
                name="deployment-signal"
                value="github_deployments_api"
                checked={selectedSignal === "github_deployments_api"}
                onChange={() => {
                  setSelectedSignal("github_deployments_api");
                  setErrorMessage(null);
                }}
                disabled={!isTenantAdmin}
                aria-disabled={!isTenantAdmin}
                data-testid="signal-option-github_deployments_api"
                className="mt-[3px] size-[15px] accent-[#00e599]"
              />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-[8px]">
                  <span className="text-[14px] font-semibold text-[#f8fafc]">
                    GitHub Deployments API
                  </span>
                  <span className="rounded-[4px] border border-[#00e599]/35 bg-[#00e599]/10 px-[6px] py-[1px] font-mono text-[10px] font-semibold uppercase text-[#00e599]">
                    Recommended
                  </span>
                  <code className="rounded-[4px] border border-[#1e293b] bg-[#0f131d] px-[8px] py-[2px] font-mono text-[11px] text-[#00e599]">
                    environment=&quot;production&quot; · deployment_status: success
                  </code>
                </div>
                <p className="mt-[4px] text-[12px] text-[#94a3b8]">
                  Listens to verified GitHub Deployment Status events for
                  production deployments via your connected GitHub App.
                </p>
              </div>
            </div>
          </label>

          {/* Option 2: ArgoCD / GitOps Webhook */}
          <label
            className={`block rounded-[10px] border p-[14px] transition-colors ${
              !isTenantAdmin ? "cursor-not-allowed opacity-85" : "cursor-pointer"
            } ${
              selectedSignal === "argocd_postsync_webhook"
                ? "border-[#00e599] bg-[#0a141d]"
                : "border-[#1e293b] bg-[#0a0e18] hover:border-[#334155]"
            }`}
          >
            <div className="flex items-start gap-[12px]">
              <input
                type="radio"
                name="deployment-signal"
                value="argocd_postsync_webhook"
                checked={selectedSignal === "argocd_postsync_webhook"}
                onChange={() => {
                  setSelectedSignal("argocd_postsync_webhook");
                  setErrorMessage(null);
                }}
                disabled={!isTenantAdmin}
                aria-disabled={!isTenantAdmin}
                data-testid="signal-option-argocd_postsync_webhook"
                className="mt-[3px] size-[15px] accent-[#00e599]"
              />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-[8px]">
                  <span className="text-[14px] font-semibold text-[#f8fafc]">
                    ArgoCD / GitOps Webhook
                  </span>
                  <code className="rounded-[4px] border border-[#1e293b] bg-[#0f131d] px-[8px] py-[2px] font-mono text-[11px] text-[#00e599]">
                    /api/v1/webhooks/gitops · PostSync wave completion
                  </code>
                </div>
                <p className="mt-[4px] text-[12px] text-[#94a3b8]">
                  Ingests post-sync health checks and sync-wave completions
                  directly from ArgoCD notifications or GitOps receivers.
                </p>

                {selectedSignal === "argocd_postsync_webhook" && (
                  <div className="mt-[12px] space-y-[8px] rounded-[8px] border border-[#1e293b] bg-[#070a11] p-[12px]">
                    <div className="flex flex-wrap items-center gap-[8px]">
                      <span className="text-[11px] text-[#94a3b8]">
                        Webhook URL:
                      </span>
                      <code className="rounded-[5px] border border-[#1e293b] bg-[#0d121f] px-[8px] py-[3px] font-mono text-[11px] text-[#e2e8f0]">
                        {gitopsWebhookUrl}
                      </code>
                      <button
                        type="button"
                        onClick={(e) => {
                          e.preventDefault();
                          handleCopy("argocd-url", gitopsWebhookUrl);
                        }}
                        className="inline-flex h-[24px] items-center gap-1 rounded-[5px] border border-[#00e599]/35 bg-[#00e599]/10 px-[8px] font-mono text-[10px] font-medium text-[#00e599] hover:bg-[#00e599]/20"
                      >
                        {copiedKey === "argocd-url" ? (
                          <Check className="size-[11px]" aria-hidden="true" />
                        ) : (
                          <Copy className="size-[11px]" aria-hidden="true" />
                        )}
                        <span>
                          {copiedKey === "argocd-url" ? "Copied" : "Copy URL"}
                        </span>
                      </button>
                    </div>
                    <div className="flex flex-wrap items-center gap-[8px] text-[11px] text-[#94a3b8]">
                      <span>Active Filters:</span>
                      <code className="rounded-[4px] border border-[#1e293b] bg-[#0d121f] px-[6px] py-[2px] font-mono text-[10px] text-[#cbd5e1]">
                        sync_status: Succeeded
                      </code>
                      <code className="rounded-[4px] border border-[#1e293b] bg-[#0d121f] px-[6px] py-[2px] font-mono text-[10px] text-[#cbd5e1]">
                        phase: PostSync
                      </code>
                      {isTenantAdmin && (
                        <Link
                          href="/integrations"
                          className="ml-auto font-mono text-[10px] text-[#00e599] hover:underline"
                        >
                          Manage webhook secrets in Integrations -&gt;
                        </Link>
                      )}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </label>

          {/* Option 3: Direct Deployment Ingestion API */}
          <label
            className={`block rounded-[10px] border p-[14px] transition-colors ${
              !isTenantAdmin ? "cursor-not-allowed opacity-85" : "cursor-pointer"
            } ${
              selectedSignal === "direct_deployment_api"
                ? "border-[#00e599] bg-[#0a141d]"
                : "border-[#1e293b] bg-[#0a0e18] hover:border-[#334155]"
            }`}
          >
            <div className="flex items-start gap-[12px]">
              <input
                type="radio"
                name="deployment-signal"
                value="direct_deployment_api"
                checked={selectedSignal === "direct_deployment_api"}
                onChange={() => {
                  setSelectedSignal("direct_deployment_api");
                  setErrorMessage(null);
                }}
                disabled={!isTenantAdmin}
                aria-disabled={!isTenantAdmin}
                data-testid="signal-option-direct_deployment_api"
                className="mt-[3px] size-[15px] accent-[#00e599]"
              />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-[8px]">
                  <span className="text-[14px] font-semibold text-[#f8fafc]">
                    Direct Deployment Ingestion API
                  </span>
                  <code className="rounded-[4px] border border-[#1e293b] bg-[#0f131d] px-[8px] py-[2px] font-mono text-[11px] text-[#00e599]">
                    POST /api/v1/projects/{project.slug}/deployments
                  </code>
                </div>
                <p className="mt-[4px] text-[12px] text-[#94a3b8]">
                  Push deployment metadata directly from CI/CD runners (GitHub
                  Actions, GitLab CI, Jenkins) using service account tokens.
                </p>

                {selectedSignal === "direct_deployment_api" && (
                  <div className="mt-[12px] space-y-[8px] rounded-[8px] border border-[#1e293b] bg-[#070a11] p-[12px]">
                    <div className="flex flex-wrap items-center gap-[8px]">
                      <span className="text-[11px] text-[#94a3b8]">
                        Endpoint:
                      </span>
                      <code className="rounded-[5px] border border-[#1e293b] bg-[#0d121f] px-[8px] py-[3px] font-mono text-[11px] text-[#e2e8f0]">
                        POST {directApiEndpoint}
                      </code>
                      <button
                        type="button"
                        onClick={(e) => {
                          e.preventDefault();
                          handleCopy("direct-url", directApiEndpoint);
                        }}
                        className="inline-flex h-[24px] items-center gap-1 rounded-[5px] border border-[#00e599]/35 bg-[#00e599]/10 px-[8px] font-mono text-[10px] font-medium text-[#00e599] hover:bg-[#00e599]/20"
                      >
                        {copiedKey === "direct-url" ? (
                          <Check className="size-[11px]" aria-hidden="true" />
                        ) : (
                          <Copy className="size-[11px]" aria-hidden="true" />
                        )}
                        <span>
                          {copiedKey === "direct-url" ? "Copied" : "Copy Endpoint"}
                        </span>
                      </button>
                    </div>
                    <div className="flex flex-wrap items-center justify-between gap-[8px] rounded-[6px] border border-[#1e293b] bg-[#0d121f] px-[10px] py-[6px]">
                      <code className="truncate font-mono text-[11px] text-[#94a3b8]">
                        {directApiCurl}
                      </code>
                      <button
                        type="button"
                        onClick={(e) => {
                          e.preventDefault();
                          handleCopy("direct-curl", directApiCurl);
                        }}
                        className="inline-flex h-[24px] shrink-0 items-center gap-1 rounded-[5px] border border-[#00e599]/35 bg-[#00e599]/10 px-[8px] font-mono text-[10px] font-medium text-[#00e599] hover:bg-[#00e599]/20"
                      >
                        <span>
                          {copiedKey === "direct-curl" ? "Copied" : "Copy cURL"}
                        </span>
                      </button>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </label>
        </fieldset>

        {errorMessage && (
          <div
            role="alert"
            data-testid="deployment-signal-error"
            className="mt-[12px] flex items-start gap-[8px] rounded-[8px] border border-[#ef4444]/40 bg-[#ef4444]/10 px-[12px] py-[8px] text-[12px] text-[#fca5a5]"
          >
            <AlertCircle
              className="mt-[2px] size-[14px] shrink-0 text-[#ef4444]"
              aria-hidden="true"
            />
            <span>{errorMessage}</span>
          </div>
        )}

        {/* Footer Action Bar */}
        <div className="mt-[16px] flex flex-wrap items-center justify-between gap-3 rounded-[10px] border border-[#1e293b] bg-[#0a0e18] px-[14px] py-[10px]">
          <div className="flex items-center gap-2 font-mono text-[11px] text-[#00e599]">
            <span
              className="size-[6px] rounded-full bg-[#00e599]"
              aria-hidden="true"
            />
            <span>
              Telemetry Ingestion Engine: Active ·{" "}
              {isDirty ? "Unsaved change selected" : "Authoritative signal verified"}
            </span>
          </div>

          <div className="flex items-center gap-[8px]">
            <button
              type="button"
              onClick={() => {
                setSelectedSignal(authoritativeSignal);
                setErrorMessage(null);
              }}
              disabled={!isTenantAdmin || !isDirty || updateMutation.isPending}
              aria-disabled={!isTenantAdmin || !isDirty || updateMutation.isPending}
              title={
                !isTenantAdmin
                  ? "Only Tenant Administrators can modify deployment signals"
                  : undefined
              }
              data-testid="deployment-signal-cancel-button"
              className="h-[32px] rounded-[7px] px-[12px] text-[12px] font-medium text-[#cbd5e1] transition-colors hover:bg-white/[0.04] disabled:cursor-not-allowed disabled:opacity-45"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleSave}
              disabled={!isTenantAdmin || !isDirty || updateMutation.isPending}
              aria-disabled={!isTenantAdmin || !isDirty || updateMutation.isPending}
              title={
                !isTenantAdmin
                  ? "Only Tenant Administrators can modify deployment signals"
                  : undefined
              }
              data-testid="deployment-signal-save-button"
              className="inline-flex h-[32px] items-center gap-1.5 rounded-[7px] bg-[#00e599] px-[14px] text-[12px] font-semibold text-[#07111a] transition-colors hover:bg-[#1ef5ab] disabled:cursor-not-allowed disabled:bg-[#1f2937] disabled:text-[#64748b]"
            >
              {updateMutation.isPending ? (
                <>
                  <Loader2 className="size-[13px] animate-spin" aria-hidden="true" />
                  <span>Saving...</span>
                </>
              ) : (
                <span>Save Changes</span>
              )}
            </button>
          </div>
        </div>
      </div>

      {/* Live Toast with Undo (Figma 4E) */}
      <div aria-live="polite" className="min-h-[1px]">
        {toastInfo && (
          <div
            role="status"
            data-testid="deployment-signal-toast"
            className="flex flex-wrap items-center justify-between gap-3 rounded-[10px] border border-[#00e599]/35 bg-[#0e1a24] px-[14px] py-[10px] text-[12px] text-[#e2e8f0]"
          >
            <div className="flex items-center gap-2">
              <CheckCircle2
                className="size-[15px] shrink-0 text-[#00e599]"
                aria-hidden="true"
              />
              <span>{toastInfo.message}</span>
            </div>
            <div className="flex items-center gap-3">
              {toastInfo.previousSignal && isTenantAdmin && (
                <button
                  type="button"
                  onClick={handleUndo}
                  disabled={updateMutation.isPending}
                  data-testid="deployment-signal-undo-button"
                  className="inline-flex items-center gap-1 font-mono text-[11px] font-medium text-[#00e599] hover:underline"
                >
                  <RotateCcw className="size-[12px]" aria-hidden="true" />
                  <span>Undo</span>
                </button>
              )}
              <button
                type="button"
                onClick={() => setToastInfo(null)}
                className="font-mono text-[11px] text-[#94a3b8] hover:text-[#f8fafc]"
              >
                Dismiss
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
