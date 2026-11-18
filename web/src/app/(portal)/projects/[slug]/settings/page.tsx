"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  Loader2,
  Plus,
  RefreshCw,
} from "lucide-react";
import { usePermissions } from "@/hooks/use-permissions";
import {
  useProjectIncidentsQuery,
  useProjectQuery,
  useProjectsQuery,
  useUpdateProjectMutation,
} from "@/hooks/use-projects";
import { isUuid } from "@/lib/projects-api";
import { ArchiveProjectModal } from "@/components/projects/archive-project-modal";
import { LinkedRepositoriesTab } from "@/components/projects/linked-repositories-tab";
import { DeploymentSignalTab } from "@/components/projects/deployment-signal-tab";

type SettingsTabId = "repositories" | "deployment-signal" | "health-sources" | "general";

const TAB_LABELS: Record<SettingsTabId, string> = {
  repositories: "Linked Repositories",
  "deployment-signal": "Deployment Signal",
  "health-sources": "Health Sources",
  general: "General Settings",
};

const TAB_SUBTITLES: Record<SettingsTabId, string> = {
  repositories:
    "Manage linked GitHub repositories, webhook listeners, and project configuration.",
  "deployment-signal":
    "Configure which authoritative signal defines production deployments for DORA calculation.",
  "health-sources":
    "Inspect production incident history and recovery times tracked for MTTR & Change Failure Rate.",
  general:
    "Edit project metadata, SLA thresholds, and workspace lifecycle.",
};

function parseSettingsTab(rawTab: string | null): SettingsTabId {
  if (
    rawTab === "repositories" ||
    rawTab === "deployment-signal" ||
    rawTab === "health-sources" ||
    rawTab === "general"
  ) {
    return rawTab;
  }
  return "repositories";
}

function ProjectSettingsContent() {
  const params = useParams<{ slug: string }>();
  const router = useRouter();
  const searchParams = useSearchParams();
  const slugParam = params?.slug ?? "";
  const urlTab = parseSettingsTab(searchParams.get("tab"));
  const { can } = usePermissions();
  const isTenantAdmin = can("MANAGE_WEBHOOK_INTEGRATIONS");

  const [activeTab, setActiveTab] = useState<SettingsTabId>(urlTab);
  const [linkModalOpen, setLinkModalOpen] = useState(false);
  const [archiveModalOpen, setArchiveModalOpen] = useState(false);

  useEffect(() => {
    setActiveTab(urlTab);
  }, [urlTab]);

  const handleTabChange = useCallback(
    (nextTab: SettingsTabId) => {
      setActiveTab(nextTab);
      if (slugParam) {
        router.replace(`/projects/${slugParam}/settings?tab=${nextTab}`, {
          scroll: false,
        });
      }
    },
    [router, slugParam],
  );

  const projectsQuery = useProjectsQuery();
  const projectFromList = projectsQuery.data?.find(
    (item) => item.slug === slugParam || item.id === slugParam,
  );

  const directQuery = useProjectQuery(
    slugParam,
    !projectFromList && isUuid(slugParam),
  );

  const project = projectFromList || directQuery.data;
  const updateProjectMutation = useUpdateProjectMutation(project?.id ?? "");
  const incidentsQuery = useProjectIncidentsQuery(
    project?.id,
    activeTab === "health-sources",
  );

  const [formName, setFormName] = useState("");
  const [formSlug, setFormSlug] = useState("");
  const [formDescription, setFormDescription] = useState("");
  const [formSlaHours, setFormSlaHours] = useState("24");
  const [formWipLimit, setFormWipLimit] = useState("5");
  const [formPrStaleHours, setFormPrStaleHours] = useState("48");
  const [formLeadTimeStrategy, setFormLeadTimeStrategy] = useState("feature_branch_root");
  const [saveFeedback, setSaveFeedback] = useState<{
    type: "success" | "error";
    message: string;
  } | null>(null);

  const lastSyncedKeyRef = useRef<string | null>(null);

  useEffect(() => {
    if (!project) return;
    const syncKey = `${project.id}:${project.updated_at}`;
    if (lastSyncedKeyRef.current === syncKey) return;
    lastSyncedKeyRef.current = syncKey;
    setFormName(project.name);
    setFormSlug(project.slug);
    setFormDescription(project.description ?? "");
    setFormSlaHours(String(project.tier_sla_hours));
    setFormWipLimit(String(project.wip_limit_per_dev));
    setFormPrStaleHours(String(project.pr_stale_threshold_hours));
    setFormLeadTimeStrategy(project.lead_time_first_commit_strategy);
    setSaveFeedback(null);
  }, [project]);

  const isLoading =
    projectsQuery.isLoading || (!projectFromList && directQuery.isLoading);
  const error =
    projectsQuery.error || (!projectFromList ? directQuery.error : null);

  if (isLoading) {
    return (
      <div
        className="flex w-full flex-col gap-[10px] px-[24px] py-[14px]"
        data-testid="project-settings-loading"
      >
        <div className="h-[64px] animate-pulse rounded-[10px] border border-[#1e2636] bg-[#111622]" />
        <div className="h-[46px] animate-pulse rounded-[8px] border border-[#1e2636] bg-[#111622]" />
        <div className="h-[280px] animate-pulse rounded-[12px] border border-[#262d3d] bg-[#131722]" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex min-w-0 w-full flex-col gap-[10px] px-[24px] py-[14px]">
        <div
          role="alert"
          className="flex flex-col items-center justify-center rounded-[12px] border border-[#ef4444]/35 bg-[#131722] px-[24px] py-[48px] text-center"
        >
          <AlertCircle className="size-[24px] text-[#ef4444]" aria-hidden="true" />
          <h1 className="mt-[12px] text-[16px] font-semibold text-[#f8fafc]">
            Failed to load project settings
          </h1>
          <p className="mt-[6px] max-w-[440px] text-[12px] text-[#94a3b8]">
            {error instanceof Error ? error.message : "Unexpected API error."}
          </p>
          <div className="mt-[16px] flex items-center gap-[8px]">
            <Link
              href="/projects"
              className="inline-flex h-[34px] items-center gap-1.5 rounded-[8px] border border-[#262d3d] bg-[#0a0e18] px-[14px] text-[12px] font-medium text-[#cbd5e1] hover:border-[#3b455e]"
            >
              <ArrowLeft className="size-[13px]" aria-hidden="true" />
              <span>Back to Projects</span>
            </Link>
            <button
              type="button"
              onClick={() => {
                projectsQuery.refetch();
                if (isUuid(slugParam)) directQuery.refetch();
              }}
              className="inline-flex h-[34px] items-center gap-1.5 rounded-[8px] bg-[#00e599] px-[14px] text-[12px] font-semibold text-[#05080f] hover:bg-[#2df0b0]"
            >
              <RefreshCw className="size-[13px]" aria-hidden="true" />
              <span>Retry</span>
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (!project) {
    return (
      <div className="flex min-w-0 w-full flex-col gap-[10px] px-[24px] py-[14px]">
        <div
          data-testid="project-not-found"
          className="flex flex-col items-center justify-center rounded-[12px] border border-[#262d3d] bg-[#131722] px-[24px] py-[56px] text-center"
        >
          <h1 className="text-[18px] font-semibold text-[#f8fafc]">
            Project not found
          </h1>
          <p className="mt-[6px] max-w-[420px] text-[12px] text-[#94a3b8]">
            No project matching &ldquo;{slugParam}&rdquo; exists in this workspace.
          </p>
          <Link
            href="/projects"
            className="mt-[16px] inline-flex h-[34px] items-center gap-1.5 rounded-[8px] bg-[#00e599] px-[16px] text-[12px] font-semibold text-[#05080f] hover:bg-[#2df0b0]"
          >
            <ArrowLeft className="size-[13px]" aria-hidden="true" />
            <span>Back to Projects</span>
          </Link>
        </div>
      </div>
    );
  }

  const handleSaveGeneral = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!project || !isTenantAdmin) return;
    setSaveFeedback(null);

    const trimmedName = formName.trim();
    const trimmedSlug = formSlug.trim();
    const parsedSla = Number.parseInt(formSlaHours, 10);
    const parsedWip = Number.parseInt(formWipLimit, 10);
    const parsedStale = Number.parseInt(formPrStaleHours, 10);

    if (!trimmedName || !trimmedSlug) {
      setSaveFeedback({
        type: "error",
        message: "Project name and slug are required.",
      });
      return;
    }

    if (
      Number.isNaN(parsedSla) ||
      parsedSla < 1 ||
      parsedSla > 720 ||
      Number.isNaN(parsedWip) ||
      parsedWip < 1 ||
      parsedWip > 50 ||
      Number.isNaN(parsedStale) ||
      parsedStale < 1 ||
      parsedStale > 720
    ) {
      setSaveFeedback({
        type: "error",
        message: "Thresholds must stay within valid ranges (SLA 1-720h, WIP 1-50, PR stale 1-720h).",
      });
      return;
    }

    try {
      const updated = await updateProjectMutation.mutateAsync({
        name: trimmedName,
        slug: trimmedSlug,
        description: formDescription.trim() || null,
        tier_sla_hours: parsedSla,
        wip_limit_per_dev: parsedWip,
        pr_stale_threshold_hours: parsedStale,
        lead_time_first_commit_strategy:
          formLeadTimeStrategy.trim() || "feature_branch_root",
      });
      setSaveFeedback({
        type: "success",
        message: "Project metadata and engineering thresholds saved.",
      });
      if (updated.slug !== slugParam) {
        router.replace(`/projects/${updated.slug}/settings?tab=general`);
      }
    } catch (err: unknown) {
      setSaveFeedback({
        type: "error",
        message:
          err instanceof Error ? err.message : "Failed to save project settings.",
      });
    }
  };

  return (
    <div className="flex min-w-0 w-full flex-col gap-[10px] px-[24px] py-[14px]">
      {/* Breadcrumb */}
      <nav
        aria-label="Project Settings Breadcrumb"
        className="flex flex-wrap items-center gap-[6px] font-mono text-[11px] leading-normal text-[#94a3b8]"
      >
        <span>Workspace</span>
        <span className="text-[#475569]" aria-hidden="true">
          /
        </span>
        <Link href="/projects" className="text-[#94a3b8] hover:text-[#e2e8f0]">
          Projects
        </Link>
        <span className="text-[#475569]" aria-hidden="true">
          /
        </span>
        <span className="text-[#94a3b8]">{project.name}</span>
        <span className="text-[#475569]" aria-hidden="true">
          /
        </span>
        <span className="text-[#00e599]">
          Settings ({TAB_LABELS[activeTab]})
        </span>
      </nav>

      {/* Page Header */}
      <header className="flex w-full flex-wrap items-center justify-between gap-[16px]">
        <div className="flex flex-col items-start gap-[2px]">
          <h1 className="font-sans text-[20px] font-bold leading-[26px] text-[#f8fafc]">
            {project.name} · Settings
          </h1>
          <p className="font-mono text-[11px] leading-[16px] text-[#64748b]">
            /projects/{project.slug}/settings ·{" "}
            <span className="font-sans text-[12px] text-[#94a3b8]">
              {TAB_SUBTITLES[activeTab]}
            </span>
          </p>
        </div>

        {activeTab === "repositories" && (
          <button
            type="button"
            onClick={() => setLinkModalOpen(true)}
            disabled={!isTenantAdmin}
            aria-disabled={!isTenantAdmin}
            title={
              !isTenantAdmin
                ? "Only Tenant Administrators can link GitHub repositories"
                : "Link GitHub repositories to this project"
            }
            data-testid="header-link-repositories-button"
            className="inline-flex h-[44px] shrink-0 items-center justify-center gap-[8px] rounded-[12px] bg-[#00e599] px-[18px] font-sans text-[14px] font-semibold leading-normal text-[#05080f] transition-colors hover:bg-[#2df0b0] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00e599] focus-visible:ring-offset-2 focus-visible:ring-offset-[#0f131d] disabled:cursor-not-allowed disabled:opacity-45"
          >
            <Plus className="size-[15px]" aria-hidden="true" />
            <span>Link Repositories</span>
          </button>
        )}
      </header>

      {/* Tab Strip */}
      <div
        role="tablist"
        aria-label="Project Settings Sections"
        className="flex flex-wrap items-center gap-[6px] rounded-[8px] border border-[#1e2636] bg-[#111622] px-[14px] py-[6px]"
      >
        {(
          [
            {
              id: "repositories",
              label: `Linked Repositories (${project.repositories.length})`,
            },
            { id: "deployment-signal", label: "Deployment Signal" },
            { id: "health-sources", label: "Health Sources" },
            { id: "general", label: "General Settings" },
          ] as const
        ).map((tab) => {
          const isSelected = activeTab === tab.id;
          return (
            <button
              key={tab.id}
              type="button"
              role="tab"
              aria-selected={isSelected}
              onClick={() => handleTabChange(tab.id)}
              data-testid={`project-tab-${tab.id}`}
              className={`inline-flex items-center gap-[8px] rounded-[7px] px-[12px] py-[6px] text-[13px] font-medium transition-colors ${
                isSelected
                  ? "text-[#00e599]"
                  : "text-[#94a3b8] hover:bg-white/[0.03] hover:text-[#f8fafc]"
              }`}
            >
              <span>{tab.label}</span>
              {isSelected && (
                <span className="rounded-[4px] border border-[#00e599]/40 bg-[#00e599]/10 px-[6px] py-[1px] font-mono text-[9px] font-semibold uppercase tracking-[0.5px] text-[#00e599]">
                  Active
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* Tab Panels */}
      {activeTab === "repositories" && (
        <LinkedRepositoriesTab
          project={project}
          linkModalOpen={linkModalOpen}
          onOpenLinkModal={() => setLinkModalOpen(true)}
          onCloseLinkModal={() => setLinkModalOpen(false)}
          onOpenDeploymentSignal={() => handleTabChange("deployment-signal")}
        />
      )}

      {activeTab === "deployment-signal" && (
        <DeploymentSignalTab project={project} />
      )}

      {activeTab === "health-sources" && (
        <div
          data-testid="health-sources-panel"
          className="space-y-[14px] rounded-[12px] border border-[#1e293b] bg-[#0d121f] p-[20px]"
        >
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-[16px] font-semibold text-[#f8fafc]">
                Project Health &amp; Production Incidents
              </h2>
              <p className="mt-[4px] text-[12px] text-[#94a3b8]">
                Recent production incidents and recovery times tracked for MTTR &amp; Change Failure Rate.
              </p>
            </div>
            <button
              type="button"
              onClick={() => void incidentsQuery.refetch()}
              disabled={incidentsQuery.isFetching}
              className="inline-flex h-[32px] items-center gap-1.5 rounded-[8px] border border-[#1e293b] bg-[#0a0e18] px-[12px] text-[12px] font-medium text-[#cbd5e1] transition-colors hover:border-[#00e599]/50 hover:text-[#f8fafc] disabled:opacity-60"
            >
              <RefreshCw
                className={`size-[13px] ${incidentsQuery.isFetching ? "animate-spin" : ""}`}
                aria-hidden="true"
              />
              <span>Refresh</span>
            </button>
          </div>

          {incidentsQuery.isLoading ? (
            <div className="flex h-[160px] items-center justify-center rounded-[10px] border border-[#1e293b] bg-[#0a0e18] text-[12px] text-[#94a3b8]">
              <Loader2 className="mr-2 size-4 animate-spin text-[#00e599]" aria-hidden="true" />
              Loading production incidents...
            </div>
          ) : incidentsQuery.error ? (
            <div
              role="alert"
              className="rounded-[10px] border border-[#ef4444]/35 bg-[#ef4444]/10 p-[14px] text-[12px] text-[#fca5a5]"
            >
              {incidentsQuery.error instanceof Error
                ? incidentsQuery.error.message
                : "Failed to load project incidents."}
            </div>
          ) : (incidentsQuery.data?.length ?? 0) === 0 ? (
            <div className="flex flex-col items-center justify-center rounded-[10px] border border-[#1e293b] bg-[#0a0e18] px-[20px] py-[40px] text-center">
              <CheckCircle2 className="size-[22px] text-[#00e599]" aria-hidden="true" />
              <p className="mt-[8px] text-[13px] font-medium text-[#f8fafc]">
                No production incidents recorded
              </p>
              <p className="mt-[4px] max-w-[420px] text-[12px] text-[#94a3b8]">
                No production failures have been ingested for this project yet. Tier SLA target is {project.tier_sla_hours}h.
              </p>
            </div>
          ) : (
            <div className="overflow-x-auto rounded-[10px] border border-[#1e293b] bg-[#0a0e18]">
              <table className="w-full border-collapse text-left text-[12px]">
                <thead>
                  <tr className="border-b border-[#1e293b] font-mono text-[10px] uppercase tracking-[0.6px] text-[#64748b]">
                    <th className="px-[12px] py-[10px]">Severity</th>
                    <th className="px-[12px] py-[10px]">Started</th>
                    <th className="px-[12px] py-[10px]">Resolved</th>
                    <th className="px-[12px] py-[10px]">Recovery Time</th>
                    <th className="px-[12px] py-[10px]">Deployment ID</th>
                  </tr>
                </thead>
                <tbody>
                  {incidentsQuery.data!.map((incident) => {
                    const recoveryMins =
                      incident.recovery_time_seconds != null
                        ? Math.round(incident.recovery_time_seconds / 60)
                        : null;
                    return (
                      <tr key={incident.id} className="border-b border-[#1e293b]/70 last:border-b-0">
                        <td className="px-[12px] py-[10px] font-mono text-[11px] uppercase text-[#f8fafc]">
                          {incident.severity}
                        </td>
                        <td className="px-[12px] py-[10px] text-[#cbd5e1]">
                          {new Date(incident.started_at).toLocaleString()}
                        </td>
                        <td className="px-[12px] py-[10px] text-[#cbd5e1]">
                          {incident.resolved_at
                            ? new Date(incident.resolved_at).toLocaleString()
                            : "Open"}
                        </td>
                        <td className="px-[12px] py-[10px] font-mono text-[#e2e8f0]">
                          {recoveryMins != null ? `${recoveryMins}m` : "—"}
                        </td>
                        <td className="px-[12px] py-[10px] font-mono text-[11px] text-[#64748b]">
                          {incident.deployment_id ? incident.deployment_id.slice(0, 8) : "—"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {activeTab === "general" && (
        <form
          onSubmit={handleSaveGeneral}
          data-testid="general-settings-panel"
          className="space-y-[16px] rounded-[12px] border border-[#1e293b] bg-[#0d121f] p-[20px]"
        >
          <div>
            <h2 className="text-[16px] font-semibold text-[#f8fafc]">
              Project Configuration Overview
            </h2>
            <p className="mt-[4px] text-[12px] text-[#94a3b8]">
              Live configuration persisted for this project in PostgreSQL.
            </p>
          </div>

          {saveFeedback && (
            <div
              role={saveFeedback.type === "error" ? "alert" : "status"}
              className={`rounded-[8px] border px-[12px] py-[9px] text-[12px] ${
                saveFeedback.type === "error"
                  ? "border-[#ef4444]/40 bg-[#ef4444]/10 text-[#fca5a5]"
                  : "border-[#00e599]/40 bg-[#00e599]/10 text-[#00e599]"
              }`}
            >
              {saveFeedback.message}
            </div>
          )}

          <div className="grid grid-cols-1 gap-[12px] sm:grid-cols-2 lg:grid-cols-3">
            <label className="block rounded-[8px] border border-[#1e293b] bg-[#0a0e18] p-[12px]">
              <span className="font-mono text-[10px] uppercase tracking-[0.6px] text-[#64748b]">
                Project Name
              </span>
              <input
                type="text"
                value={formName}
                onChange={(e) => setFormName(e.target.value)}
                disabled={!isTenantAdmin}
                className="mt-[6px] w-full rounded-[6px] border border-[#1e293b] border-solid bg-[#111622] px-[10px] py-[6px] text-[13px] font-medium text-[#f8fafc] shadow-none outline-none focus:border-[#00e599] focus:ring-0 disabled:opacity-60"
              />
            </label>

            <label className="block rounded-[8px] border border-[#1e293b] bg-[#0a0e18] p-[12px]">
              <span className="font-mono text-[10px] uppercase tracking-[0.6px] text-[#64748b]">
                URL Slug
              </span>
              <input
                type="text"
                value={formSlug}
                onChange={(e) => setFormSlug(e.target.value)}
                disabled={!isTenantAdmin}
                className="mt-[6px] w-full rounded-[6px] border border-[#1e293b] border-solid bg-[#111622] px-[10px] py-[6px] font-mono text-[12px] text-[#00e599] shadow-none outline-none focus:border-[#00e599] focus:ring-0 disabled:opacity-60"
              />
            </label>

            <label className="block rounded-[8px] border border-[#1e293b] bg-[#0a0e18] p-[12px]">
              <span className="font-mono text-[10px] uppercase tracking-[0.6px] text-[#64748b]">
                Tier SLA Hours
              </span>
              <input
                type="number"
                min={1}
                max={720}
                value={formSlaHours}
                onChange={(e) => setFormSlaHours(e.target.value)}
                disabled={!isTenantAdmin}
                className="mt-[6px] w-full rounded-[6px] border border-[#1e293b] border-solid bg-[#111622] px-[10px] py-[6px] font-mono text-[12px] text-[#e2e8f0] shadow-none outline-none focus:border-[#00e599] focus:ring-0 disabled:opacity-60"
              />
            </label>

            <label className="block rounded-[8px] border border-[#1e293b] bg-[#0a0e18] p-[12px]">
              <span className="font-mono text-[10px] uppercase tracking-[0.6px] text-[#64748b]">
                WIP Limit / Developer
              </span>
              <input
                type="number"
                min={1}
                max={50}
                value={formWipLimit}
                onChange={(e) => setFormWipLimit(e.target.value)}
                disabled={!isTenantAdmin}
                className="mt-[6px] w-full rounded-[6px] border border-[#1e293b] border-solid bg-[#111622] px-[10px] py-[6px] font-mono text-[12px] text-[#e2e8f0] shadow-none outline-none focus:border-[#00e599] focus:ring-0 disabled:opacity-60"
              />
            </label>

            <label className="block rounded-[8px] border border-[#1e293b] bg-[#0a0e18] p-[12px]">
              <span className="font-mono text-[10px] uppercase tracking-[0.6px] text-[#64748b]">
                PR Stale Threshold (Hours)
              </span>
              <input
                type="number"
                min={1}
                max={720}
                value={formPrStaleHours}
                onChange={(e) => setFormPrStaleHours(e.target.value)}
                disabled={!isTenantAdmin}
                className="mt-[6px] w-full rounded-[6px] border border-[#1e293b] border-solid bg-[#111622] px-[10px] py-[6px] font-mono text-[12px] text-[#e2e8f0] shadow-none outline-none focus:border-[#00e599] focus:ring-0 disabled:opacity-60"
              />
            </label>

            <label className="block rounded-[8px] border border-[#1e293b] bg-[#0a0e18] p-[12px]">
              <span className="font-mono text-[10px] uppercase tracking-[0.6px] text-[#64748b]">
                Lead Time First-Commit Strategy
              </span>
              <input
                type="text"
                value={formLeadTimeStrategy}
                onChange={(e) => setFormLeadTimeStrategy(e.target.value)}
                disabled={!isTenantAdmin}
                className="mt-[6px] w-full rounded-[6px] border border-[#1e293b] border-solid bg-[#111622] px-[10px] py-[6px] font-mono text-[12px] text-[#e2e8f0] shadow-none outline-none focus:border-[#00e599] focus:ring-0 disabled:opacity-60"
              />
            </label>

            <label className="block rounded-[8px] border border-[#1e293b] bg-[#0a0e18] p-[12px] sm:col-span-2 lg:col-span-3">
              <span className="font-mono text-[10px] uppercase tracking-[0.6px] text-[#64748b]">
                Description
              </span>
              <textarea
                rows={2}
                value={formDescription}
                onChange={(e) => setFormDescription(e.target.value)}
                disabled={!isTenantAdmin}
                placeholder="Describe this project's service ownership, delivery scope, and SLA expectations..."
                className="mt-[6px] w-full rounded-[6px] border border-[#1e293b] border-solid bg-[#111622] px-[10px] py-[6px] text-[12px] text-[#cbd5e1] shadow-none outline-none focus:border-[#00e599] focus:ring-0 disabled:opacity-60"
              />
            </label>

            <div className="rounded-[8px] border border-[#1e293b] bg-[#0a0e18] p-[12px] sm:col-span-2 lg:col-span-3">
              <div className="font-mono text-[10px] uppercase tracking-[0.6px] text-[#64748b]">
                Project UUID
              </div>
              <div className="mt-[4px] font-mono text-[12px] text-[#94a3b8]">
                {project.id}
              </div>
            </div>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-[#1e293b] pt-[14px]">
            <span className="text-[11px] text-[#64748b]">
              {isTenantAdmin
                ? "Changes are saved directly to PostgreSQL."
                : "Only Tenant Administrators can edit or archive projects."}
            </span>

            <div className="flex items-center gap-[8px]">
              <button
                type="submit"
                disabled={!isTenantAdmin || updateProjectMutation.isPending}
                data-testid="general-save-button"
                className="h-[34px] rounded-[8px] bg-[#00e599] px-[14px] text-[12px] font-semibold text-[#05080f] transition-colors hover:bg-[#2df0b0] disabled:cursor-not-allowed disabled:opacity-55"
              >
                {updateProjectMutation.isPending ? "Saving..." : "Save Changes"}
              </button>
              <button
                type="button"
                disabled={!isTenantAdmin || !project.is_active}
                onClick={() => setArchiveModalOpen(true)}
                data-testid="general-archive-button"
                className="h-[34px] rounded-[8px] border border-[#ef4444]/40 bg-[#ef4444]/15 px-[14px] text-[12px] font-medium text-[#fca5a5] transition-colors hover:bg-[#ef4444]/25 disabled:cursor-not-allowed disabled:opacity-55"
              >
                {!project.is_active ? "Archived" : "Archive Project"}
              </button>
            </div>
          </div>
        </form>
      )}

      <ArchiveProjectModal
        open={archiveModalOpen}
        onClose={() => setArchiveModalOpen(false)}
        project={project}
        onArchived={() => {
          router.push("/projects");
        }}
      />
    </div>
  );
}

export default function ProjectSettingsPage() {
  return (
    <Suspense fallback={null}>
      <ProjectSettingsContent />
    </Suspense>
  );
}
