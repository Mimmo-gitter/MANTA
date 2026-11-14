/* eslint-disable @next/next/no-img-element */
"use client";

import { useState } from "react";
import Link from "next/link";
import { useAuth } from "@/providers/auth-provider";
import { usePermissions } from "@/hooks/use-permissions";
import { useProjectsQuery } from "@/hooks/use-projects";
import { CreateProjectModal } from "@/components/projects/create-project-modal";
import { cn } from "@/lib/utils";

export default function OverviewPage() {
  const { isAuthenticated, tenantName } = useAuth();
  const { can } = usePermissions();
  const isTenantAdmin = can("MANAGE_WEBHOOK_INTEGRATIONS");

  const {
    data: projects,
    isLoading,
    isFetching,
    isError,
    error,
    refetch,
  } = useProjectsQuery(isAuthenticated);

  const [errorTooltipOpen, setErrorTooltipOpen] = useState(false);
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);

  const errorDetailsText =
    error instanceof Error
      ? error.message
      : "The overview metrics could not be loaded. Retry the request. If the problem persists, contact your administrator.";

  if (isLoading) {
    return (
      <div className="relative min-h-[calc(100vh-48px)] w-full">
        <section
          data-testid="overview-loading-state"
          aria-label="Loading Overview Telemetry"
          aria-busy="true"
          className="max-w-[1600px] w-full mx-auto flex flex-col gap-[24px] p-[24px] animate-pulse"
        >
          <div className="flex flex-wrap items-center justify-between gap-4 w-full">
            <div className="flex flex-col gap-[8px] items-start">
              <div className="bg-[#161b26] border border-[#1f2433] border-solid h-[24px] rounded-[6px] w-[192px]" />
              <div className="bg-[rgba(22,27,38,0.6)] h-[14px] rounded-[4px] w-[320px] max-w-full" />
            </div>
            <div className="flex gap-[8px] items-center">
              <div className="bg-[#161b26] border border-[#1f2433] border-solid h-[32px] rounded-[6px] w-[112px]" />
              <div className="bg-[rgba(0,229,153,0.1)] border border-[rgba(0,229,153,0.2)] border-solid h-[32px] rounded-[6px] w-[128px]" />
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-[16px] w-full">
            {[1, 2, 3].map((idx) => (
              <div
                key={idx}
                className="bg-[#161b26] border border-[#1f2433] border-solid flex flex-col gap-[16px] items-start p-[21px] rounded-[12px]"
              >
                <div className="flex items-center justify-between w-full">
                  <div className="bg-[#1f2433] h-[14px] rounded-[4px] w-[96px]" />
                  <div className="bg-[rgba(31,36,51,0.6)] rounded-[8px] size-[32px]" />
                </div>
                <div className="flex flex-col gap-[8px] items-start w-full">
                  <div className="bg-[#1f2433] h-[28px] rounded-[6px] w-[64px]" />
                  <div className="bg-[rgba(31,36,51,0.5)] h-[12px] rounded-[4px] w-[128px]" />
                </div>
                <div className="border-t border-[rgba(31,36,51,0.6)] border-solid pt-[9px] flex flex-col items-start w-full">
                  <div className="bg-[rgba(31,36,51,0.4)] h-[10px] rounded-[4px] w-full" />
                </div>
              </div>
            ))}
          </div>

          <div className="bg-[#161b26] border border-[#1f2433] border-solid flex flex-col items-start p-px rounded-[12px] w-full overflow-hidden">
            <div className="border-b border-[#1f2433] border-solid px-[20px] py-[16px] flex items-center justify-between w-full">
              <div className="flex gap-[12px] items-center">
                <div className="bg-[#1f2433] h-[16px] rounded-[4px] w-[144px]" />
                <div className="bg-[rgba(31,36,51,0.6)] h-[20px] rounded-full w-[48px]" />
              </div>
              <div className="bg-[#0f131d] border border-[#1f2433] border-solid h-[28px] rounded-[6px] w-[160px]" />
            </div>
            {[1, 2, 3].map((row) => (
              <div
                key={row}
                className="border-b last:border-b-0 border-[rgba(31,36,51,0.6)] border-solid p-[20px] flex items-center justify-between w-full"
              >
                <div className="flex gap-[16px] items-center">
                  <div className="bg-[#1f2433] rounded-[8px] size-[40px] shrink-0" />
                  <div className="flex flex-col gap-[8px] items-start">
                    <div className="bg-[#1f2433] h-[16px] rounded-[4px] w-[176px]" />
                    <div className="bg-[rgba(31,36,51,0.5)] h-[12px] rounded-[4px] w-[256px]" />
                  </div>
                </div>
                <div className="hidden sm:flex gap-[24px] items-center">
                  <div className="bg-[rgba(31,36,51,0.6)] h-[24px] rounded-[4px] w-[64px]" />
                  <div className="bg-[rgba(31,36,51,0.6)] rounded-[6px] size-[28px]" />
                </div>
              </div>
            ))}
          </div>
        </section>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="relative min-h-[calc(100vh-48px)] w-full">
        <section
          data-testid="overview-error-state"
          aria-label="Error State: Unable to load overview"
          className="flex min-h-[calc(100vh-48px)] w-full items-center justify-center p-[24px]"
        >
          <div className="bg-[#161b26] border border-[#1f2433] border-solid flex flex-col items-center max-w-[512px] w-full p-[33px] relative rounded-[12px] shadow-[0px_20px_25px_-5px_rgba(0,0,0,0.4),0px_8px_10px_-6px_rgba(0,0,0,0.4)]">
            <div className="pb-[20px] flex flex-col items-start">
              <div className="bg-[rgba(239,68,68,0.1)] border border-[rgba(239,68,68,0.2)] border-solid flex items-center justify-center p-px rounded-full shadow-[0px_0px_20px_0px_rgba(239,68,68,0.15)] size-[48px]">
                <img
                  src="/icons/shell/a1d53.svg"
                  alt=""
                  aria-hidden="true"
                  className="size-[24px]"
                />
              </div>
            </div>

            <div className="pb-[8px] w-full text-center">
              <h1 className="font-semibold text-[#e2e8f0] text-[18px] leading-[28px] tracking-[-0.45px]">
                Unable to load overview
              </h1>
            </div>

            <div className="pb-[24px] max-w-[384px] w-full text-center">
              <p className="font-normal text-[#7c839b] text-[14px] leading-[22.75px]">
                We couldn&apos;t load your project metrics. Please try again.
              </p>
            </div>

            <div className="flex gap-[12px] items-center justify-center w-full">
              <button
                type="button"
                data-testid="error-retry-button"
                onClick={() => void refetch()}
                disabled={isFetching}
                className="bg-[#1f2433] border border-[rgba(124,131,155,0.2)] border-solid flex gap-[8px] items-center justify-center px-[17px] py-[9px] rounded-[8px] shadow-[0px_1px_2px_0px_rgba(0,0,0,0.05)] hover:border-[rgba(124,131,155,0.4)] transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[#00e599] cursor-pointer"
              >
                <img
                  src="/icons/shell/91fa3.svg"
                  alt=""
                  aria-hidden="true"
                  className={cn("size-[14px] shrink-0", isFetching && "animate-spin")}
                />
                <span className="font-medium text-[#e2e8f0] text-[12px] leading-[16px] text-center">
                  {isFetching ? "Retrying..." : "Retry"}
                </span>
              </button>

              <div className="group relative flex flex-col items-start">
                <button
                  type="button"
                  data-testid="error-details-button"
                  aria-describedby="error-details-tooltip"
                  aria-expanded={errorTooltipOpen}
                  onClick={() => setErrorTooltipOpen((prev) => !prev)}
                  onMouseEnter={() => setErrorTooltipOpen(true)}
                  onMouseLeave={() => setErrorTooltipOpen(false)}
                  onFocus={() => setErrorTooltipOpen(true)}
                  onBlur={() => setErrorTooltipOpen(false)}
                  className={cn(
                    "border border-solid flex gap-[6px] items-center px-[13px] py-[9px] rounded-[8px] transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[#00e599] cursor-pointer",
                    errorTooltipOpen
                      ? "bg-[rgba(31,36,51,0.8)] border-[#1f2433] text-[#e2e8f0]"
                      : "border-transparent text-[#7c839b] hover:bg-[rgba(31,36,51,0.8)] hover:border-[#1f2433] hover:text-[#e2e8f0]"
                  )}
                >
                  <img
                    src={
                      errorTooltipOpen
                        ? "/icons/shell/52480.svg"
                        : "/icons/shell/7dae7.svg"
                    }
                    alt=""
                    aria-hidden="true"
                    className="size-[14px] shrink-0"
                  />
                  <span className="font-medium text-[12px] leading-[16px] text-center whitespace-nowrap">
                    Error details
                  </span>
                </button>

                <div
                  id="error-details-tooltip"
                  role="tooltip"
                  data-testid="error-details-tooltip"
                  className={cn(
                    "absolute left-1/2 top-[calc(100%+8px)] -translate-x-1/2 w-[288px] bg-[#0f131d] border border-[#1f2433] border-solid rounded-[8px] p-[13px] shadow-[0px_20px_25px_-5px_rgba(0,0,0,0.6),0px_8px_10px_-6px_rgba(0,0,0,0.6)] z-50 text-left",
                    errorTooltipOpen
                      ? "block"
                      : "hidden group-hover:block group-focus-within:block"
                  )}
                >
                  <div
                    className="absolute -top-[5px] left-1/2 -translate-x-1/2 bg-[#0f131d] border-[#1f2433] border-l border-solid border-t rotate-45 size-[8px]"
                    aria-hidden="true"
                  />
                  <p className="relative z-10 font-normal text-[#e2e8f0] text-[12px] leading-[19.5px] break-words">
                    {errorDetailsText}
                  </p>
                </div>
              </div>
            </div>
          </div>
        </section>
      </div>
    );
  }

  const projectList = projects ?? [];

  if (projectList.length === 0) {
    return (
      <div className="relative min-h-[calc(100vh-48px)] w-full">
        <section
          data-testid="overview-empty-state"
          aria-label="Empty State: No projects created"
          className="flex min-h-[calc(100vh-48px)] w-full items-center justify-center p-[24px]"
        >
          <div className="flex flex-col items-center max-w-[448px] w-full">
            <div className="pb-[20px] flex flex-col items-start">
              <div className="bg-[#161b26] border border-[#1f2433] border-solid flex items-center justify-center p-px rounded-[12px] shadow-[0px_1px_2px_0px_rgba(0,0,0,0.05)] size-[48px]">
                <img
                  src="/icons/shell/16065.svg"
                  alt=""
                  aria-hidden="true"
                  className="size-[20px]"
                />
              </div>
            </div>

            <div className="pb-[6px] w-full text-center">
              <h1 className="font-semibold text-[#e2e8f0] text-[16px] leading-[24px] tracking-[-0.4px]">
                No projects yet
              </h1>
            </div>

            <div className="pb-[24px] max-w-[340px] w-full text-center">
              <p className="font-normal text-[#7c839b] text-[12px] leading-[19.5px]">
                Create a project or import an existing repository to get started.
              </p>
            </div>

            <button
              type="button"
              data-testid="create-project-button"
              disabled={!isTenantAdmin}
              aria-disabled={!isTenantAdmin}
              title={
                isTenantAdmin
                  ? "Create a new engineering project"
                  : "Only Tenant Administrators can create projects"
              }
              onClick={() => {
                if (isTenantAdmin) {
                  setIsCreateModalOpen(true);
                }
              }}
              className={cn(
                "flex items-center justify-center px-[16px] py-[8px] rounded-[6px] font-semibold text-[12px] leading-[16px] text-center transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00e599]",
                isTenantAdmin
                  ? "bg-[#00e599] text-[#0f131d] shadow-[0px_0px_16px_0px_rgba(0,229,153,0.2)] hover:brightness-105 cursor-pointer"
                  : "bg-[#1f2433] text-[#7c839b] border border-[#262d3d] opacity-60 cursor-not-allowed"
              )}
            >
              + Create Project
            </button>

            <div className="pt-[12px] flex flex-col items-start">
              <Link
                href="/integrations"
                className="font-medium text-[#7c839b] text-[11px] leading-[16.5px] text-center underline decoration-[rgba(124,131,155,0.4)] underline-offset-4 hover:text-[#e2e8f0] transition-colors"
              >
                or import existing repositories
              </Link>
            </div>
          </div>
        </section>

        <CreateProjectModal
          open={isCreateModalOpen}
          onClose={() => setIsCreateModalOpen(false)}
          existingProjects={projectList}
        />
      </div>
    );
  }

  const totalRepos = projectList.reduce(
    (sum, proj) => sum + (proj.repositories?.length ?? 0),
    0,
  );

  return (
    <div className="relative min-h-[calc(100vh-48px)] w-full">
      <section
        data-testid="overview-default-state"
        aria-label="Workspace Canvas"
        className="max-w-[1600px] w-full mx-auto flex flex-col gap-6 p-6"
      >
        <div className="flex flex-wrap items-center justify-between gap-4 border-b border-[#1f2433] pb-5">
          <div>
            <h1 className="text-[20px] font-semibold tracking-[-0.4px] text-[#e2e8f0]">
              Engineering Overview
            </h1>
            <p className="mt-1 text-[13px] text-[#7c839b]">
              Active workspace summary for{" "}
              <span className="font-medium text-[#cbd5e1]">
                {tenantName || "your organization"}
              </span>
              .
            </p>
          </div>
          <div className="flex items-center gap-3">
            <Link
              href="/projects"
              className="inline-flex h-[34px] items-center rounded-[6px] border border-[#252b3b] bg-[#161b26] px-3.5 text-[12px] font-medium text-[#e2e8f0] transition-colors hover:border-[#374151] hover:bg-[#1e2433]"
            >
              View All Projects
            </Link>
            <button
              type="button"
              data-testid="create-project-button"
              disabled={!isTenantAdmin}
              aria-disabled={!isTenantAdmin}
              title={
                isTenantAdmin
                  ? "Create a new engineering project"
                  : "Only Tenant Administrators can create projects"
              }
              onClick={() => {
                if (isTenantAdmin) {
                  setIsCreateModalOpen(true);
                }
              }}
              className={cn(
                "inline-flex h-[34px] items-center rounded-[6px] px-3.5 text-[12px] font-semibold transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00e599]",
                isTenantAdmin
                  ? "bg-[#00e599] text-[#0f131d] hover:brightness-105 cursor-pointer"
                  : "bg-[#1f2433] text-[#7c839b] border border-[#262d3d] opacity-60 cursor-not-allowed"
              )}
            >
              + Create Project
            </button>
          </div>
        </div>

        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          <div className="rounded-[12px] border border-[#1f2433] bg-[#161b26] p-5">
            <span className="font-mono text-[11px] uppercase tracking-[0.06em] text-[#7c839b]">
              Active Projects
            </span>
            <p className="mt-2 font-mono text-[28px] font-semibold text-[#e2e8f0]">
              {projectList.length}
            </p>
          </div>
          <div className="rounded-[12px] border border-[#1f2433] bg-[#161b26] p-5">
            <span className="font-mono text-[11px] uppercase tracking-[0.06em] text-[#7c839b]">
              Linked Repositories
            </span>
            <p className="mt-2 font-mono text-[28px] font-semibold text-[#e2e8f0]">
              {totalRepos}
            </p>
          </div>
          <div className="rounded-[12px] border border-[#1f2433] bg-[#161b26] p-5">
            <span className="font-mono text-[11px] uppercase tracking-[0.06em] text-[#7c839b]">
              DORA Telemetry
            </span>
            <p className="mt-2 text-[14px] text-[#94a3b8]">
              Configure deployment signals per project in{" "}
              <Link
                href="/projects"
                className="font-medium text-[#00e599] hover:underline"
              >
                Projects Directory
              </Link>
              .
            </p>
          </div>
        </div>
      </section>

      <CreateProjectModal
        open={isCreateModalOpen}
        onClose={() => setIsCreateModalOpen(false)}
        existingProjects={projectList}
      />
    </div>
  );
}
