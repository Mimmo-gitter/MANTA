"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  AlertCircle,
  CheckCircle2,
  Copy,
  FolderPlus,
  MoreVertical,
  Plus,
  RefreshCw,
  Search,
  Settings,
  Trash2,
} from "lucide-react";
import { useAuth } from "@/providers/auth-provider";
import { usePermissions } from "@/hooks/use-permissions";
import { useProjectsQuery } from "@/hooks/use-projects";
import { getProjectUiMeta } from "@/lib/projects-api";
import type { ProjectItem } from "@/types/project";
import { CreateProjectModal } from "@/components/projects/create-project-modal";
import {
  FilterDropdown,
  type FilterDropdownOption,
} from "@/components/ui/filter-dropdown";
import { cn } from "@/lib/utils";

type HealthFilter = "all" | "active" | "setup" | "inactive";
type RepoFilter = "all" | "with-repos" | "no-repos";

const PAGE_SIZE = 6;

const SIGNAL_SHORT_LABELS: Record<string, string> = {
  github_deployments_api: "GitHub Deployments",
  argocd_postsync_webhook: "ArgoCD PostSync",
  direct_deployment_api: "Direct API",
};

function getProjectInitials(name: string): string {
  const words = name.trim().split(/[\s-_]+/).filter(Boolean);
  if (words.length >= 2) {
    return `${words[0][0]}${words[1][0]}`.toUpperCase();
  }
  return name.slice(0, 2).toUpperCase() || "PR";
}

function getProjectStatus(project: ProjectItem): "active" | "setup" | "inactive" {
  if (!project.is_active) return "inactive";
  if (project.repositories.length === 0) return "setup";
  return "active";
}

function ProjectsDirectorySkeleton() {
  return (
    <div className="flex w-full flex-col gap-[10px]" data-testid="projects-loading-skeleton">
      <div className="h-[38px] animate-pulse rounded-[8px] border border-[#1e2636] bg-[#111622]" />
      <div className="rounded-[12px] border border-[#262d3d] bg-[#131722] p-[16px]">
        <div className="mb-[14px] h-[30px] animate-pulse rounded-[6px] bg-[#171b26]" />
        <div className="grid grid-cols-1 gap-[14px] md:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }).map((_, idx) => (
            <div
              key={idx}
              className="h-[158px] animate-pulse rounded-[10px] border border-[#262d3d] bg-[#111622]"
            />
          ))}
        </div>
      </div>
    </div>
  );
}

export function ProjectsDirectory() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { tenantName } = useAuth();
  const { can } = usePermissions();
  const isTenantAdmin = can("MANAGE_WEBHOOK_INTEGRATIONS");

  const projectsQuery = useProjectsQuery();
  const initialQuery = searchParams.get("q") ?? "";

  const [searchTerm, setSearchTerm] = useState(initialQuery);
  const [statusFilter, setStatusFilter] = useState<HealthFilter>("all");
  const [repoFilter, setRepoFilter] = useState<RepoFilter>("all");
  const [page, setPage] = useState(1);
  const [createOpen, setCreateOpen] = useState(false);
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);
  const [copiedNotice, setCopiedNotice] = useState<string | null>(null);

  const searchInputId = useId();
  const statusSelectId = useId();
  const repoSelectId = useId();
  const menuContainerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setSearchTerm(searchParams.get("q") ?? "");
    setPage(1);
  }, [searchParams]);

  useEffect(() => {
    function handleOutsideClick(event: MouseEvent) {
      if (
        openMenuId &&
        menuContainerRef.current &&
        !menuContainerRef.current.contains(event.target as Node)
      ) {
        setOpenMenuId(null);
      }
    }
    document.addEventListener("mousedown", handleOutsideClick);
    return () => document.removeEventListener("mousedown", handleOutsideClick);
  }, [openMenuId]);

  useEffect(() => {
    if (!copiedNotice) return;
    const timer = window.setTimeout(() => setCopiedNotice(null), 3500);
    return () => window.clearTimeout(timer);
  }, [copiedNotice]);

  const projects = useMemo(
    () => projectsQuery.data ?? [],
    [projectsQuery.data],
  );

  const summary = useMemo(() => {
    let activeCount = 0;
    let setupCount = 0;
    let inactiveCount = 0;
    let totalRepos = 0;
    let withReposCount = 0;
    let noReposCount = 0;

    for (const project of projects) {
      totalRepos += project.repositories.length;
      if (project.repositories.length > 0) withReposCount += 1;
      else noReposCount += 1;

      const st = getProjectStatus(project);
      if (st === "active") activeCount += 1;
      else if (st === "setup") setupCount += 1;
      else inactiveCount += 1;
    }

    return {
      total: projects.length,
      activeCount,
      setupCount,
      inactiveCount,
      totalRepos,
      withReposCount,
      noReposCount,
    };
  }, [projects]);

  const statusOptions = useMemo<FilterDropdownOption<HealthFilter>[]>(
    () => [
      {
        value: "all",
        label: "All statuses",
        triggerText: `All statuses (${summary.total})`,
        count: summary.total,
        dotClassName: "bg-[#64748b]",
      },
      {
        value: "active",
        label: "Active",
        count: summary.activeCount,
        dotClassName: "bg-[#00e599]",
      },
      {
        value: "setup",
        label: "Setup required",
        count: summary.setupCount,
        dotClassName: "bg-[#fbbf24]",
      },
      {
        value: "inactive",
        label: "Inactive",
        count: summary.inactiveCount,
        dotClassName: "bg-[#ef4444]",
      },
    ],
    [
      summary.activeCount,
      summary.inactiveCount,
      summary.setupCount,
      summary.total,
    ],
  );

  const repoOptions = useMemo<FilterDropdownOption<RepoFilter>[]>(
    () => [
      {
        value: "all",
        label: "All repos",
        triggerText: `All repos (${summary.total})`,
        count: summary.total,
        dotClassName: "bg-[#64748b]",
      },
      {
        value: "with-repos",
        label: "With linked repos",
        count: summary.withReposCount,
        dotClassName: "bg-[#00e599]",
      },
      {
        value: "no-repos",
        label: "No repos linked",
        count: summary.noReposCount,
        dotClassName: "bg-[#60a5fa]",
      },
    ],
    [summary.noReposCount, summary.total, summary.withReposCount],
  );

  const activeStatusOption =
    statusOptions.find((opt) => opt.value === statusFilter) ?? statusOptions[0];
  const activeRepoOption =
    repoOptions.find((opt) => opt.value === repoFilter) ?? repoOptions[0];

  const filteredProjects = useMemo(() => {
    const query = searchTerm.trim().toLowerCase();
    return projects.filter((project) => {
      const status = getProjectStatus(project);
      if (statusFilter !== "all" && status !== statusFilter) {
        return false;
      }
      if (repoFilter === "with-repos" && project.repositories.length === 0) {
        return false;
      }
      if (repoFilter === "no-repos" && project.repositories.length > 0) {
        return false;
      }
      if (!query) return true;

      const repoMatch = project.repositories.some((repo) =>
        repo.full_name.toLowerCase().includes(query),
      );
      return (
        project.name.toLowerCase().includes(query) ||
        project.slug.toLowerCase().includes(query) ||
        repoMatch
      );
    });
  }, [projects, repoFilter, searchTerm, statusFilter]);

  const totalPages = Math.max(1, Math.ceil(filteredProjects.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const paginatedProjects = filteredProjects.slice(
    (safePage - 1) * PAGE_SIZE,
    safePage * PAGE_SIZE,
  );

  const resetFilters = () => {
    setSearchTerm("");
    setStatusFilter("all");
    setRepoFilter("all");
    setPage(1);
    if (searchParams.get("q")) {
      router.replace("/projects");
    }
  };

  const handleCopySlug = async (slug: string) => {
    setOpenMenuId(null);
    try {
      await navigator.clipboard.writeText(slug);
      setCopiedNotice(`Copied slug '${slug}' to clipboard.`);
    } catch {
      setCopiedNotice(`Slug: ${slug}`);
    }
  };

  return (
    <div
      ref={menuContainerRef}
      className="flex w-full flex-col gap-[10px] px-[24px] py-[14px]"
    >
      {/* Breadcrumb (Matches Access & Roles layout) */}
      <nav
        aria-label="Page Breadcrumb"
        className="flex items-center gap-[6px] font-mono text-[11px] leading-normal text-[#94a3b8]"
      >
        <span>Workspace</span>
        <span className="text-[#475569]" aria-hidden="true">
          /
        </span>
        <span className="text-[#00e599]">Projects</span>
      </nav>

      {/* Page Header */}
      <header className="flex w-full flex-wrap items-center justify-between gap-[16px]">
        <div className="flex flex-col items-start gap-[2px]">
          <h1 className="font-sans text-[20px] font-bold leading-[26px] text-[#f8fafc]">
            Projects
          </h1>
          <p className="font-sans text-[12px] font-normal leading-[16px] text-[#94a3b8]">
            Directory of engineering workspaces, connected GitHub repositories,
            and deployment signal settings.
          </p>
        </div>

        <button
          type="button"
          onClick={() => setCreateOpen(true)}
          disabled={!isTenantAdmin}
          aria-disabled={!isTenantAdmin}
          title={
            !isTenantAdmin
              ? "Only Tenant Administrators can create projects"
              : "Create a new project workspace"
          }
          data-testid="create-project-button"
          className="inline-flex h-[44px] shrink-0 items-center justify-center gap-[8px] rounded-[12px] bg-[#00e599] px-[18px] font-sans text-[14px] font-semibold leading-normal text-[#05080f] transition-colors hover:bg-[#2df0b0] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00e599] focus-visible:ring-offset-2 focus-visible:ring-offset-[#0f131d] disabled:cursor-not-allowed disabled:opacity-45"
        >
          <Plus className="size-[15px]" aria-hidden="true" />
          <span>Create Project</span>
        </button>
      </header>

      {projectsQuery.isLoading ? (
        <ProjectsDirectorySkeleton />
      ) : projectsQuery.isError ? (
        <div
          role="alert"
          data-testid="projects-error-state"
          className="flex flex-col items-center justify-center rounded-[12px] border border-[#ef4444]/35 bg-[#131722] px-[24px] py-[48px] text-center"
        >
          <div className="flex size-[42px] items-center justify-center rounded-full border border-[#ef4444]/40 bg-[#ef4444]/10 text-[#ef4444]">
            <AlertCircle className="size-[20px]" aria-hidden="true" />
          </div>
          <h2 className="mt-[14px] text-[16px] font-semibold text-[#f8fafc]">
            Unable to load workspace projects
          </h2>
          <p className="mt-[6px] max-w-[440px] text-[12px] text-[#94a3b8]">
            {projectsQuery.error instanceof Error
              ? projectsQuery.error.message
              : "Failed to fetch /api/v1/projects from backend."}
          </p>
          <button
            type="button"
            onClick={() => projectsQuery.refetch()}
            data-testid="projects-retry-button"
            className="mt-[16px] inline-flex h-[34px] items-center gap-1.5 rounded-[8px] border border-[#262d3d] bg-[#171b26] px-[14px] text-[12px] font-medium text-[#f8fafc] transition-colors hover:border-[#00e599]/50"
          >
            <RefreshCw className="size-[13px]" aria-hidden="true" />
            <span>Retry</span>
          </button>
        </div>
      ) : (
        <>
          {/* Summary Strip (Matches Access & Roles KPI strip) */}
          <div
            data-testid="projects-summary-strip"
            className="flex min-h-[38px] w-full shrink-0 flex-wrap items-center justify-between gap-[12px] rounded-[8px] border border-[#1e2636] bg-[#111622] px-[16px] py-[6px]"
          >
            <div className="flex flex-wrap items-center gap-[14px] text-[12px] leading-normal sm:gap-[16px]">
              <span className="font-sans font-semibold text-[#f8fafc]">
                {summary.total} {summary.total === 1 ? "project" : "projects"}
              </span>
              <span
                className="size-[3px] shrink-0 rounded-full bg-[#475569]"
                aria-hidden="true"
              />
              <span className="font-sans font-medium text-[#00e599]">
                {summary.activeCount} active
              </span>
              <span
                className="size-[3px] shrink-0 rounded-full bg-[#475569]"
                aria-hidden="true"
              />
              <span className="font-sans font-medium text-[#00c2cb]">
                {summary.setupCount} awaiting repos
              </span>
              <span
                className="size-[3px] shrink-0 rounded-full bg-[#475569]"
                aria-hidden="true"
              />
              <span className="font-sans font-medium text-[#f59e0b]">
                {summary.inactiveCount} inactive
              </span>
            </div>
          </div>

          {/* Directory Card */}
          <div className="flex w-full flex-col overflow-visible rounded-[12px] border border-[#262d3d] bg-[#131722]">
            {/* Toolbar */}
            <div className="flex min-h-[46px] w-full shrink-0 flex-wrap items-center justify-between gap-[12px] rounded-t-[12px] border-b border-[#262d3d] bg-[#171b26] px-[16px] py-[8px]">
              <div className="flex items-center gap-[8px]">
                <h2 className="font-mono text-[11px] font-bold uppercase leading-normal text-[#94a3b8]">
                  WORKSPACE PROJECTS
                </h2>
                <span className="flex items-start rounded-[99px] border border-[rgba(0,229,153,0.35)] bg-[rgba(0,229,153,0.12)] px-[7px] py-[2px] font-mono text-[11px] font-bold leading-normal text-[#00e599]">
                  {filteredProjects.length}
                </span>
              </div>

              <div className="flex flex-wrap items-center gap-[8px]">
                <div className="relative flex h-[30px] w-[236px] shrink-0 items-center gap-[8px] rounded-[6px] border border-[#262d3d] bg-[#0a0e18] px-[10px] transition-colors focus-within:border-[#00e599]">
                  <Search
                    className="size-[14px] shrink-0 text-[#64748b]"
                    aria-hidden="true"
                  />
                  <label htmlFor={searchInputId} className="sr-only">
                    Search projects or repositories
                  </label>
                  <input
                    id={searchInputId}
                    type="text"
                    value={searchTerm}
                    onChange={(e) => {
                      setSearchTerm(e.target.value);
                      setPage(1);
                    }}
                    placeholder="Search projects or repos..."
                    data-testid="projects-filter-input"
                    className="w-full border-0 border-none bg-transparent p-0 font-sans text-[12px] font-normal leading-normal text-[#f8fafc] placeholder-[#64748b] shadow-none outline-none ring-0 focus:border-none focus:outline-none focus:ring-0"
                    style={{ border: "none", outline: "none", boxShadow: "none" }}
                  />
                  {searchTerm && (
                    <button
                      type="button"
                      onClick={() => {
                        setSearchTerm("");
                        setPage(1);
                      }}
                      aria-label="Clear search"
                      className="text-[#64748b] hover:text-[#e2e8f0]"
                    >
                      &times;
                    </button>
                  )}
                </div>

                <div>
                  <label htmlFor={statusSelectId} className="sr-only">
                    Filter by project status
                  </label>
                  <FilterDropdown
                    selectId={statusSelectId}
                    testId="projects-status-filter"
                    triggerTestId="projects-status-filter-trigger"
                    popoverTestId="projects-status-filter-popover"
                    ariaLabel="Filter projects by status"
                    headerLabel="FILTER BY PROJECT STATUS"
                    value={statusFilter}
                    onChange={(next) => {
                      setStatusFilter(next);
                      setPage(1);
                    }}
                    options={statusOptions}
                    triggerLabel={
                      activeStatusOption.triggerText ?? activeStatusOption.label
                    }
                    isActive={statusFilter !== "all"}
                    popoverWidthClassName="w-[236px]"
                  />
                </div>

                <div>
                  <label htmlFor={repoSelectId} className="sr-only">
                    Filter by linked repositories
                  </label>
                  <FilterDropdown
                    selectId={repoSelectId}
                    testId="projects-repo-filter"
                    triggerTestId="projects-repo-filter-trigger"
                    popoverTestId="projects-repo-filter-popover"
                    ariaLabel="Filter projects by linked repositories"
                    headerLabel="FILTER BY REPOSITORIES"
                    value={repoFilter}
                    onChange={(next) => {
                      setRepoFilter(next);
                      setPage(1);
                    }}
                    options={repoOptions}
                    triggerLabel={
                      activeRepoOption.triggerText ?? activeRepoOption.label
                    }
                    isActive={repoFilter !== "all"}
                    popoverWidthClassName="w-[236px]"
                  />
                </div>
              </div>
            </div>

            {/* Cards / Empty States */}
            {projects.length === 0 ? (
              <div className="p-[24px]">
                <div
                  data-testid="projects-empty-state"
                  className="flex flex-col items-center justify-center rounded-[12px] border border-dashed border-[#252b3b] bg-[#0a0e18] px-[24px] py-[56px] text-center"
                >
                  <div className="flex size-[46px] items-center justify-center rounded-full border border-[#00e599]/30 bg-[#00e599]/10 text-[#00e599]">
                    <FolderPlus className="size-[20px]" aria-hidden="true" />
                  </div>
                  <h3 className="mt-[14px] text-[16px] font-semibold text-[#f8fafc]">
                    No projects in this workspace yet
                  </h3>
                  <p className="mt-[6px] max-w-[440px] text-[12px] leading-[19px] text-[#94a3b8]">
                    Create your first project workspace in{" "}
                    {tenantName && tenantName !== "Unknown Tenant"
                      ? tenantName
                      : "this workspace"}{" "}
                    to connect GitHub repositories, ingest webhook events, and
                    track DORA engineering metrics.
                  </p>
                  <button
                    type="button"
                    onClick={() => setCreateOpen(true)}
                    disabled={!isTenantAdmin}
                    aria-disabled={!isTenantAdmin}
                    title={
                      !isTenantAdmin
                        ? "Only Tenant Administrators can create projects"
                        : undefined
                    }
                    data-testid="create-first-project-button"
                    className="mt-[18px] inline-flex h-[38px] items-center gap-1.5 rounded-[8px] bg-[#00e599] px-[18px] text-[13px] font-semibold text-[#07111a] transition-colors hover:bg-[#1ef5ab] disabled:cursor-not-allowed disabled:opacity-45"
                  >
                    <Plus className="size-[14px]" aria-hidden="true" />
                    <span>Create First Project</span>
                  </button>
                </div>
              </div>
            ) : filteredProjects.length === 0 ? (
              <div
                data-testid="projects-filter-empty-state"
                className="flex flex-col items-center justify-center px-[24px] py-[48px] text-center"
              >
                <p className="text-[14px] font-medium text-[#f8fafc]">
                  No projects match your current filters
                </p>
                <p className="mt-[4px] text-[12px] text-[#94a3b8]">
                  Try clearing search keywords or switching status filters.
                </p>
                <button
                  type="button"
                  onClick={resetFilters}
                  data-testid="projects-reset-filters-button"
                  className="mt-[14px] rounded-[8px] border border-[#252b3b] bg-[#0a0e18] px-[14px] py-[7px] text-[12px] font-medium text-[#cbd5e1] hover:border-[#00e599]/50 hover:text-[#f8fafc]"
                >
                  Reset filters
                </button>
              </div>
            ) : (
              <>
                <div className="grid grid-cols-1 gap-[14px] p-[16px] md:grid-cols-2 xl:grid-cols-3">
                  {paginatedProjects.map((project) => {
                    const status = getProjectStatus(project);
                    const repoCount = project.repositories.length;
                    const signalKey =
                      project.authoritative_deployment_signal ||
                      project.authoritative_cd_signal;
                    const signalShort =
                      SIGNAL_SHORT_LABELS[signalKey] || signalKey;
                    const uiMeta =
                      getProjectUiMeta(project.id) ||
                      getProjectUiMeta(project.slug);
                    const summaryCopy =
                      uiMeta.description ||
                      getProjectUiMeta(project.slug).description ||
                      `SLA ${project.tier_sla_hours}h · WIP limit ${project.wip_limit_per_dev}/dev · PR stale ${project.pr_stale_threshold_hours}h`;

                    const cardBorder =
                      status === "inactive"
                        ? "border-[#ef4444]/40 hover:border-[#ef4444]/70"
                        : status === "setup"
                          ? "border-[#f59e0b]/40 hover:border-[#f59e0b]/70"
                          : "border-[#1e293b] hover:border-[#00e599]/45";

                    return (
                      <article
                        key={project.id}
                        data-testid={`project-card-${project.slug}`}
                        className={`relative flex flex-col justify-between rounded-[12px] border bg-[#111622] p-[16px] transition-colors ${cardBorder}`}
                      >
                        <div>
                          <div className="flex items-start justify-between gap-2">
                            <Link
                              href={`/projects/${project.slug}/settings`}
                              className="group flex min-w-0 items-center gap-[10px]"
                            >
                              <div className="flex size-[34px] shrink-0 items-center justify-center rounded-full border border-[#00e599]/35 bg-[#0e2425] font-mono text-[11px] font-semibold text-[#00e599]">
                                {getProjectInitials(project.name)}
                              </div>
                              <div className="min-w-0">
                                <h3 className="truncate text-[14px] font-semibold text-[#f8fafc] group-hover:text-[#00e599]">
                                  {project.name}
                                </h3>
                                <p className="truncate font-mono text-[11px] text-[#64748b]">
                                  /projects/{project.slug}
                                </p>
                              </div>
                            </Link>

                            <div className="relative shrink-0">
                              <button
                                type="button"
                                onClick={() =>
                                  setOpenMenuId((prev) =>
                                    prev === project.id ? null : project.id,
                                  )
                                }
                                aria-label={`Actions for ${project.name}`}
                                aria-expanded={openMenuId === project.id}
                                aria-haspopup="menu"
                                data-testid={`project-card-menu-${project.slug}`}
                                className="flex size-[28px] items-center justify-center rounded-[6px] border border-[#1e293b] bg-[#0a0e18] text-[#94a3b8] hover:border-[#334155] hover:text-[#f8fafc]"
                              >
                                <MoreVertical
                                  className="size-[13px]"
                                  aria-hidden="true"
                                />
                              </button>

                              {openMenuId === project.id && (
                                <div
                                  role="menu"
                                  className="absolute right-0 top-[calc(100%+4px)] z-20 w-[200px] rounded-[8px] border border-[#252b3b] bg-[#161b26] p-[4px] shadow-xl"
                                >
                                  <Link
                                    href={`/projects/${project.slug}/settings`}
                                    role="menuitem"
                                    onClick={() => setOpenMenuId(null)}
                                    className="flex items-center gap-2 rounded-[6px] px-[8px] py-[7px] text-[12px] text-[#e2e8f0] hover:bg-white/[0.04]"
                                  >
                                    <Settings
                                      className="size-[13px] text-[#00e599]"
                                      aria-hidden="true"
                                    />
                                    <span>Open Settings &amp; Repos</span>
                                  </Link>
                                  <button
                                    type="button"
                                    role="menuitem"
                                    onClick={() => handleCopySlug(project.slug)}
                                    className="flex w-full items-center gap-2 rounded-[6px] px-[8px] py-[7px] text-left text-[12px] text-[#e2e8f0] hover:bg-white/[0.04]"
                                  >
                                    <Copy
                                      className="size-[13px] text-[#94a3b8]"
                                      aria-hidden="true"
                                    />
                                    <span>Copy Project Slug</span>
                                  </button>
                                  <button
                                    type="button"
                                    role="menuitem"
                                    disabled
                                    aria-disabled="true"
                                    title="Archiving projects requires PATCH/DELETE /api/v1/projects/{id} backend support"
                                    data-testid={`archive-project-action-${project.slug}`}
                                    className="flex w-full cursor-not-allowed items-center gap-2 rounded-[6px] px-[8px] py-[7px] text-left text-[12px] text-[#64748b] opacity-55"
                                  >
                                    <Trash2
                                      className="size-[13px]"
                                      aria-hidden="true"
                                    />
                                    <span>Archive Project</span>
                                  </button>
                                </div>
                              )}
                            </div>
                          </div>

                          <p className="mt-[10px] line-clamp-2 text-[12px] leading-[18px] text-[#94a3b8]">
                            {summaryCopy}
                          </p>
                        </div>

                        <div className="mt-[14px] flex flex-wrap items-center justify-between gap-2">
                          <span className="inline-flex items-center gap-1.5 rounded-[5px] border border-[#1e293b] bg-[#0a0e18] px-[8px] py-[3px] font-mono text-[10px] text-[#cbd5e1]">
                            <span
                              className="size-[5px] rounded-full bg-[#60a5fa]"
                              aria-hidden="true"
                            />
                            <span>
                              {repoCount}{" "}
                              {repoCount === 1 ? "repository" : "repositories"}{" "}
                              · {signalShort}
                            </span>
                          </span>

                          {status === "active" ? (
                            <span className="inline-flex items-center gap-1.5 rounded-[5px] border border-[#00e599]/35 bg-[#00e599]/10 px-[8px] py-[3px] font-mono text-[10px] font-medium text-[#00e599]">
                              <span
                                className="size-[5px] rounded-full bg-[#00e599]"
                                aria-hidden="true"
                              />
                              <span>Active</span>
                            </span>
                          ) : status === "setup" ? (
                            <span className="inline-flex items-center gap-1.5 rounded-[5px] border border-[#f59e0b]/40 bg-[#f59e0b]/10 px-[8px] py-[3px] font-mono text-[10px] font-medium text-[#fbbf24]">
                              <span
                                className="size-[5px] rounded-full bg-[#fbbf24]"
                                aria-hidden="true"
                              />
                              <span>Setup</span>
                            </span>
                          ) : (
                            <span className="inline-flex items-center gap-1.5 rounded-[5px] border border-[#ef4444]/40 bg-[#ef4444]/10 px-[8px] py-[3px] font-mono text-[10px] font-medium text-[#fca5a5]">
                              <span
                                className="size-[5px] rounded-full bg-[#ef4444]"
                                aria-hidden="true"
                              />
                              <span>Inactive</span>
                            </span>
                          )}
                        </div>
                      </article>
                    );
                  })}
                </div>

                {/* Pagination Footer (Matches Access & Roles footer) */}
                <div className="flex min-h-[34px] w-full shrink-0 flex-wrap items-center justify-between gap-2 rounded-b-[12px] border-t border-[#262d3d] bg-[#0e121b] px-[16px] py-[6px] font-sans text-[11px] font-normal leading-normal text-[#64748b]">
                  <span>
                    Showing {(safePage - 1) * PAGE_SIZE + 1}-
                    {Math.min(safePage * PAGE_SIZE, filteredProjects.length)} of{" "}
                    {filteredProjects.length} workspace projects
                  </span>

                  <div className="flex items-center gap-[4px] font-mono text-[10px] leading-normal">
                    <button
                      type="button"
                      onClick={() => setPage((p) => Math.max(1, p - 1))}
                      disabled={safePage <= 1}
                      className={cn(
                        "flex h-[22px] items-center justify-center rounded-[5px] border px-[8px] font-medium transition-colors",
                        safePage <= 1
                          ? "cursor-not-allowed border-[rgba(38,45,61,0.6)] bg-[rgba(10,14,24,0.5)] text-[#475569]"
                          : "cursor-pointer border-[#262d3d] bg-[#111622] text-[#cbd5e1] hover:bg-[#1a2130] hover:text-[#f8fafc]",
                      )}
                    >
                      Prev
                    </button>
                    {Array.from({ length: totalPages }, (_, i) => i + 1).map(
                      (p) => {
                        const isCurrent = p === safePage;
                        return (
                          <button
                            key={p}
                            type="button"
                            onClick={() => setPage(p)}
                            className={cn(
                              "flex h-[22px] min-w-[22px] cursor-pointer items-center justify-center rounded-[5px] border px-[7px] font-bold transition-colors",
                              isCurrent
                                ? "border-[rgba(0,229,153,0.4)] bg-[rgba(0,229,153,0.14)] text-[#00e599]"
                                : "border-[rgba(38,45,61,0.6)] bg-[rgba(10,14,24,0.5)] text-[#64748b] hover:bg-[#1a2130] hover:text-[#f8fafc]",
                            )}
                          >
                            {p}
                          </button>
                        );
                      },
                    )}
                    <button
                      type="button"
                      onClick={() =>
                        setPage((p) => Math.min(totalPages, p + 1))
                      }
                      disabled={safePage >= totalPages}
                      className={cn(
                        "flex h-[22px] items-center justify-center rounded-[5px] border px-[8px] font-medium transition-colors",
                        safePage >= totalPages
                          ? "cursor-not-allowed border-[rgba(38,45,61,0.6)] bg-[rgba(10,14,24,0.5)] text-[#475569]"
                          : "cursor-pointer border-[#262d3d] bg-[#111622] text-[#cbd5e1] hover:bg-[#1a2130] hover:text-[#f8fafc]",
                      )}
                    >
                      Next
                    </button>
                  </div>
                </div>
              </>
            )}
          </div>
        </>
      )}

      <div aria-live="polite" className="min-h-[1px]">
        {copiedNotice && (
          <div
            role="status"
            className="flex items-center gap-2 rounded-[10px] border border-[#00e599]/35 bg-[#0e1a24] px-[14px] py-[10px] text-[12px] text-[#e2e8f0]"
          >
            <CheckCircle2
              className="size-[15px] shrink-0 text-[#00e599]"
              aria-hidden="true"
            />
            <span>{copiedNotice}</span>
          </div>
        )}
      </div>

      <CreateProjectModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        existingProjects={projects}
      />
    </div>
  );
}
