"use client";

import { useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  AlertCircle,
  ArrowLeft,
  Clock,
  Plus,
  RefreshCw,
} from "lucide-react";
import { usePermissions } from "@/hooks/use-permissions";
import { useProjectQuery, useProjectsQuery } from "@/hooks/use-projects";
import { getProjectUiMeta, isUuid } from "@/lib/projects-api";
import { LinkedRepositoriesTab } from "@/components/projects/linked-repositories-tab";
import { DeploymentSignalTab } from "@/components/projects/deployment-signal-tab";

type SettingsTabId = "repositories" | "deployment-signal" | "health-sources" | "general";

const TAB_LABELS: Record<SettingsTabId, string> = {
  repositories: "Linked Repositories",
  "deployment-signal": "Deployment Signal",
  "health-sources": "Health Sources",
  general: "General Settings",
};

export default function ProjectSettingsPage() {
  const params = useParams<{ slug: string }>();
  const slugParam = params?.slug ?? "";
  const { can } = usePermissions();
  const isTenantAdmin = can("MANAGE_WEBHOOK_INTEGRATIONS");

  const [activeTab, setActiveTab] = useState<SettingsTabId>("repositories");
  const [linkModalOpen, setLinkModalOpen] = useState(false);

  const projectsQuery = useProjectsQuery();
  const projectFromList = projectsQuery.data?.find(
    (item) => item.slug === slugParam || item.id === slugParam,
  );

  const directQuery = useProjectQuery(
    slugParam,
    !projectFromList && isUuid(slugParam),
  );

  const project = projectFromList || directQuery.data;
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
      <div className="flex w-full flex-col gap-[10px] px-[24px] py-[14px]">
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
      <div className="flex w-full flex-col gap-[10px] px-[24px] py-[14px]">
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

  const uiDescription =
    getProjectUiMeta(project.id).description ||
    getProjectUiMeta(project.slug).description;

  return (
    <div className="flex w-full flex-col gap-[10px] px-[24px] py-[14px]">
      {/* Breadcrumb */}
      <nav
        aria-label="Project Settings Breadcrumb"
        className="flex flex-wrap items-center gap-[6px] font-mono text-[11px] leading-normal text-[#94a3b8]"
      >
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
              Manage connected GitHub integration, linked repositories, and
              deployment signals.
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
              onClick={() => setActiveTab(tab.id)}
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
        />
      )}

      {activeTab === "deployment-signal" && (
        <DeploymentSignalTab project={project} />
      )}

      {activeTab === "health-sources" && (
        <div
          data-testid="health-sources-coming-soon"
          className="flex flex-col items-center justify-center rounded-[12px] border border-[#1e293b] bg-[#0d121f] px-[24px] py-[56px] text-center"
        >
          <div className="flex size-[44px] items-center justify-center rounded-full border border-[#60a5fa]/30 bg-[#60a5fa]/10 text-[#60a5fa]">
            <Clock className="size-[20px]" aria-hidden="true" />
          </div>
          <div className="mt-[12px] inline-flex items-center rounded-[4px] border border-[#60a5fa]/35 bg-[#60a5fa]/10 px-[8px] py-[2px] font-mono text-[10px] font-semibold uppercase tracking-[0.5px] text-[#60a5fa]">
            Coming Soon
          </div>
          <h2 className="mt-[10px] text-[16px] font-semibold text-[#f8fafc]">
            Project Health Sources
          </h2>
          <p className="mt-[6px] max-w-[460px] text-[12px] leading-[19px] text-[#94a3b8]">
            Automated Jira/Linear sprint health connectors and custom SLO alert
            thresholds will be configurable here once backend health-source APIs
            are released.
          </p>
        </div>
      )}

      {activeTab === "general" && (
        <div
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

          <dl className="grid grid-cols-1 gap-[12px] sm:grid-cols-2 lg:grid-cols-3">
            <div className="rounded-[8px] border border-[#1e293b] bg-[#0a0e18] p-[12px]">
              <dt className="font-mono text-[10px] uppercase tracking-[0.6px] text-[#64748b]">
                Project Name
              </dt>
              <dd className="mt-[4px] text-[13px] font-medium text-[#f8fafc]">
                {project.name}
              </dd>
            </div>
            <div className="rounded-[8px] border border-[#1e293b] bg-[#0a0e18] p-[12px]">
              <dt className="font-mono text-[10px] uppercase tracking-[0.6px] text-[#64748b]">
                URL Slug
              </dt>
              <dd className="mt-[4px] font-mono text-[12px] text-[#00e599]">
                {project.slug}
              </dd>
            </div>
            <div className="rounded-[8px] border border-[#1e293b] bg-[#0a0e18] p-[12px]">
              <dt className="font-mono text-[10px] uppercase tracking-[0.6px] text-[#64748b]">
                Tier SLA Hours
              </dt>
              <dd className="mt-[4px] font-mono text-[12px] text-[#e2e8f0]">
                {project.tier_sla_hours}h
              </dd>
            </div>
            <div className="rounded-[8px] border border-[#1e293b] bg-[#0a0e18] p-[12px]">
              <dt className="font-mono text-[10px] uppercase tracking-[0.6px] text-[#64748b]">
                WIP Limit / Developer
              </dt>
              <dd className="mt-[4px] font-mono text-[12px] text-[#e2e8f0]">
                {project.wip_limit_per_dev}
              </dd>
            </div>
            <div className="rounded-[8px] border border-[#1e293b] bg-[#0a0e18] p-[12px]">
              <dt className="font-mono text-[10px] uppercase tracking-[0.6px] text-[#64748b]">
                PR Stale Threshold
              </dt>
              <dd className="mt-[4px] font-mono text-[12px] text-[#e2e8f0]">
                {project.pr_stale_threshold_hours}h
              </dd>
            </div>
            <div className="rounded-[8px] border border-[#1e293b] bg-[#0a0e18] p-[12px]">
              <dt className="font-mono text-[10px] uppercase tracking-[0.6px] text-[#64748b]">
                Lead Time First-Commit Strategy
              </dt>
              <dd className="mt-[4px] font-mono text-[12px] text-[#e2e8f0]">
                {project.lead_time_first_commit_strategy}
              </dd>
            </div>
            <div className="rounded-[8px] border border-[#1e293b] bg-[#0a0e18] p-[12px] sm:col-span-2 lg:col-span-3">
              <dt className="font-mono text-[10px] uppercase tracking-[0.6px] text-[#64748b]">
                Project UUID
              </dt>
              <dd className="mt-[4px] font-mono text-[12px] text-[#94a3b8]">
                {project.id}
              </dd>
            </div>
            {uiDescription && (
              <div className="rounded-[8px] border border-[#1e293b] bg-[#0a0e18] p-[12px] sm:col-span-2 lg:col-span-3">
                <dt className="font-mono text-[10px] uppercase tracking-[0.6px] text-[#64748b]">
                  Notes (Local UI)
                </dt>
                <dd className="mt-[4px] text-[12px] text-[#cbd5e1]">
                  {uiDescription}
                </dd>
              </div>
            )}
          </dl>

          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-[#1e293b] pt-[14px]">
            <span className="text-[11px] text-[#64748b]">
              Updating general metadata or archiving projects requires PATCH /
              DELETE /api/v1/projects/{"{id}"} backend support.
            </span>

            <div className="flex items-center gap-[8px]">
              <button
                type="button"
                disabled
                aria-disabled="true"
                title="Updating project settings awaits PATCH /api/v1/projects/{id} backend support"
                data-testid="general-save-disabled-button"
                className="h-[34px] cursor-not-allowed rounded-[8px] border border-[#252b3b] bg-[#161b26] px-[14px] text-[12px] font-medium text-[#64748b] opacity-60"
              >
                Save Changes
              </button>
              <button
                type="button"
                disabled
                aria-disabled="true"
                title="Archiving projects awaits DELETE /api/v1/projects/{id} backend support"
                data-testid="general-archive-disabled-button"
                className="h-[34px] cursor-not-allowed rounded-[8px] border border-[#ef4444]/30 bg-[#ef4444]/10 px-[14px] text-[12px] font-medium text-[#fca5a5] opacity-55"
              >
                Archive Project
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
