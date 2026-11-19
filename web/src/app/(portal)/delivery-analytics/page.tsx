"use client";

import { useCallback, useEffect } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { AlertTriangle, FolderOpen, Lock, PlugZap } from "lucide-react";
import { useAuth } from "@/providers/auth-provider";
import { usePermissions } from "@/hooks/use-permissions";
import { useProjectsQuery } from "@/hooks/use-projects";
import {
  useDoraDashboard,
  useDoraSignal,
  useNow,
} from "@/hooks/use-dora-dashboard";
import {
  buildEmptyDashboard,
  classifyTelemetry,
  formatClockTime,
  formatRelativeTime,
} from "@/lib/dora/dora-utils";
import {
  DEFAULT_DORA_TIMEFRAME,
  DORA_METRIC_IDS,
  DORA_TIMEFRAMES,
  type DoraDashboardViewModel,
  type DoraEmptyVariant,
  type DoraTimeframe,
} from "@/types/dora";
import {
  DeploymentSignalCard,
  DoraFilters,
  DoraPageHeader,
  DoraSkeleton,
  DoraStatePanel,
  MetricCard,
  TrendChartCard,
  type DataStatus,
} from "@/components/dora/dora-cards";

type PageMode =
  | "loading"
  | "error"
  | "api_unavailable"
  | "cold_state"
  | "partial"
  | "populated";

function parseTimeframe(raw: string | null): DoraTimeframe {
  const parsed = Number(raw);
  return (DORA_TIMEFRAMES as readonly number[]).includes(parsed)
    ? (parsed as DoraTimeframe)
    : DEFAULT_DORA_TIMEFRAME;
}

const BUTTON_CLASS =
  "inline-flex h-[34px] items-center rounded-[6px] border border-[#252b3b] bg-[#1f2433] px-4 text-[12px] font-medium text-[#e2e8f0] transition-colors hover:border-[#374151] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[#00e599]";

export default function DeliveryAnalyticsPage() {
  const { isAuthenticated } = useAuth();
  const { can } = usePermissions();
  const canView = can("VIEW_PROJECT_DORA");
  const canConfigure = can("MANAGE_WEBHOOK_INTEGRATIONS");

  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const nowMs = useNow();
  const timeframe = parseTimeframe(searchParams.get("timeframe"));

  const projectsQuery = useProjectsQuery(isAuthenticated && canView);
  const projects = projectsQuery.data ?? [];
  const requestedProjectId = searchParams.get("project");
  const selectedProject =
    projects.find((p) => p.id === requestedProjectId) ?? projects[0] ?? null;
  const projectId = selectedProject?.id ?? null;

  const dashboardQuery = useDoraDashboard({
    projectId,
    days: timeframe,
    enabled: isAuthenticated && canView,
  });
  const dashboardResult = dashboardQuery.data ?? null;
  const apiViewModel: DoraDashboardViewModel | null =
    dashboardResult?.kind === "ok" ? dashboardResult.data : null;

  const signal = useDoraSignal({
    projectId,
    enabled: isAuthenticated && canView,
    dashboard: apiViewModel,
  });

  const updateParams = useCallback(
    (patch: Record<string, string | null>) => {
      const next = new URLSearchParams(searchParams.toString());
      for (const [key, value] of Object.entries(patch)) {
        if (value === null) next.delete(key);
        else next.set(key, value);
      }
      const qs = next.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [pathname, router, searchParams],
  );

  // ---- Access and project-level states -------------------------------------
  if (!canView) {
    return (
      <div className="mx-auto w-full max-w-[1600px] p-6">
        <DoraStatePanel
          testId="dora-no-access-state"
          icon={<Lock className="size-5" aria-hidden="true" />}
          title="You don't have access to Delivery Analytics"
          message="Your role does not include permission to view project DORA metrics. Contact a workspace administrator if you need access."
        />
      </div>
    );
  }

  if (projectsQuery.isLoading) {
    return (
      <div className="mx-auto w-full max-w-[1600px] p-6">
        <DoraSkeleton />
      </div>
    );
  }

  if (projectsQuery.isError) {
    return (
      <div className="mx-auto w-full max-w-[1600px] p-6">
        <DoraStatePanel
          testId="dora-projects-error-state"
          tone="error"
          icon={<AlertTriangle className="size-5" aria-hidden="true" />}
          title="Unable to load projects"
          message={
            projectsQuery.error instanceof Error
              ? projectsQuery.error.message
              : "The project list could not be loaded."
          }
        >
          <button
            type="button"
            className={BUTTON_CLASS}
            onClick={() => void projectsQuery.refetch()}
          >
            Retry
          </button>
        </DoraStatePanel>
      </div>
    );
  }

  if (!selectedProject || projectId === null) {
    return (
      <div className="mx-auto w-full max-w-[1600px] p-6">
        <DoraStatePanel
          testId="dora-no-projects-state"
          icon={<FolderOpen className="size-5" aria-hidden="true" />}
          title="No projects yet"
          message="Delivery analytics are calculated per project. Create or import a project to get started."
        >
          <Link href="/projects" className={BUTTON_CLASS}>
            Go to Projects
          </Link>
        </DoraStatePanel>
      </div>
    );
  }

  // ---- Resolve the dashboard mode ------------------------------------------
  const signalNotConfigured =
    signal.state.status === "ready" && !signal.state.signal.configured;

  let mode: PageMode;
  let viewModel: DoraDashboardViewModel | null = null;

  if (signalNotConfigured) {
    mode = "cold_state";
    viewModel = buildEmptyDashboard({
      projectId,
      timeframeDays: timeframe,
      source: "api",
    });
  } else if (dashboardQuery.isLoading) {
    mode = "loading";
  } else if (dashboardQuery.isError) {
    mode = "error";
  } else if (dashboardResult?.kind === "unavailable") {
    mode = "api_unavailable";
  } else if (apiViewModel) {
    viewModel = apiViewModel;
    const telemetry = classifyTelemetry(apiViewModel);
    mode = telemetry === "empty" ? "cold_state" : telemetry;
  } else {
    mode = "loading";
  }

  const emptyVariant: DoraEmptyVariant =
    mode === "cold_state" ? "not_configured" : "no_data";

  const verification = viewModel?.verification ?? {
    status: "unavailable" as const,
    detail: null,
  };
  const thresholdUsed = viewModel
    ? DORA_METRIC_IDS.some((id) => viewModel!.trends[id].threshold !== null)
    : false;

  const dataStatus: DataStatus = (() => {
    if (mode === "cold_state") {
      return { label: "Awaiting deployment integration", tone: "muted" };
    }
    switch (mode) {
      case "loading":
        return { label: "Loading…", tone: "muted" };
      case "error":
        return { label: "Failed to load", tone: "error" };
      case "api_unavailable":
        return { label: "Metrics API unavailable", tone: "muted" };
      default: {
        const rel = viewModel?.generatedAt
          ? formatRelativeTime(viewModel.generatedAt, nowMs)
          : null;
        if (rel) return { label: `Updated ${rel}`, tone: "ok" };
        if (dashboardQuery.dataUpdatedAt > 0) {
          return {
            label: `Fetched ${formatClockTime(dashboardQuery.dataUpdatedAt)}`,
            tone: "muted",
          };
        }
        return {
          label: "Status unavailable",
          tone: "muted",
        };
      }
    }
  })();

  const showSections =
    viewModel !== null &&
    mode !== "loading" &&
    mode !== "error" &&
    mode !== "api_unavailable";
  const isColdState = mode === "cold_state";

  return (
    <div className="relative min-h-[calc(100vh-48px)] w-full">
      <section
        data-testid="dora-page"
        data-mode={mode}
        data-cold-state={isColdState}
        aria-label="Delivery Analytics"
        className="mx-auto flex w-full max-w-[1600px] flex-col gap-6 p-6"
      >

        <DoraPageHeader verification={verification} />

        <DoraFilters
          projects={projects.map((p) => ({
            id: p.id,
            name: p.name,
            repoCount: p.repositories?.length ?? 0,
          }))}
          selectedProjectId={projectId}
          onProjectChange={(id) => updateParams({ project: id })}
          timeframe={timeframe}
          onTimeframeChange={(days) =>
            updateParams({ timeframe: String(days) })
          }
          dataStatus={dataStatus}
        />

        {mode === "loading" && <DoraSkeleton />}

        {mode === "error" && (
          <DoraStatePanel
            testId="dora-error-state"
            tone="error"
            icon={<AlertTriangle className="size-5" aria-hidden="true" />}
            title="Unable to load delivery metrics"
            message={
              dashboardQuery.error instanceof Error
                ? dashboardQuery.error.message
                : "The delivery metrics request failed. Please try again."
            }
          >
            <button
              type="button"
              className={BUTTON_CLASS}
              onClick={() => void dashboardQuery.refetch()}
            >
              Retry
            </button>
          </DoraStatePanel>
        )}

        {mode === "api_unavailable" &&
          dashboardResult?.kind === "unavailable" && (
            <DoraStatePanel
              testId="dora-api-unavailable-state"
              tone="warning"
              icon={<PlugZap className="size-5" aria-hidden="true" />}
              title="Delivery metrics API not yet available"
              message={`${dashboardResult.reason} This is not an error with your project or an absence of telemetry.`}
            />
          )}

        {showSections && viewModel && (
          <>
            <div
              data-testid="dora-metric-grid"
              className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4 [&>*]:min-w-0"
            >
              {DORA_METRIC_IDS.map((id) => (
                <MetricCard
                  key={id}
                  metric={viewModel.metrics[id]}
                  emptyVariant={emptyVariant}
                />
              ))}
            </div>

            <div className="flex flex-col gap-4">
              <div className="flex flex-wrap items-end justify-between gap-3">
                <div>
                  <h2 className="text-[16px] font-medium text-[#e2e8f0]">
                    Daily Performance Trends
                  </h2>
                  <p className="text-[12px] text-[#7c839b]">
                    {isColdState
                      ? "Detailed telemetry breakdown with zero-false-zero architectural honesty."
                      : `Daily telemetry for the last ${timeframe} days. Days without data are shown as gaps, never as zero.`}
                  </p>
                </div>
                {isColdState ? (
                  <div
                    data-testid="dora-telemetry-offline"
                    role="status"
                    className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.04em] text-[#87918b]"
                  >
                    <span
                      className="size-2 rounded-[2px] bg-[#657468]"
                      aria-hidden="true"
                    />
                    <span>Telemetry Offline</span>
                    <span className="text-[#4f5b54]">/</span>
                    <span>0 data streams active</span>
                  </div>
                ) : (
                  <ul
                    className="flex flex-wrap items-center gap-4 font-mono text-[10px] text-[#7c839b]"
                    aria-label="Chart legend"
                  >
                    <li className="flex items-center gap-2">
                      <span
                        className="size-2 rounded-[2px] bg-[#00e599]"
                        aria-hidden="true"
                      />
                      Production Signal
                    </li>
                    <li className="flex items-center gap-2">
                      <span
                        className="size-2 rounded-[2px] bg-[#22d3ee]"
                        aria-hidden="true"
                      />
                      Incident / Volume
                    </li>
                    {thresholdUsed && (
                      <li className="flex items-center gap-2">
                        <span
                          className="h-0 w-4 border-t border-dashed border-[#ff4d4d]"
                          aria-hidden="true"
                        />
                        Failure Threshold
                      </li>
                    )}
                  </ul>
                )}
              </div>

              <div
                data-testid="dora-trend-grid"
                className="grid grid-cols-1 gap-4 xl:grid-cols-2 [&>*]:min-w-0"
              >
                {DORA_METRIC_IDS.map((id) => (
                  <TrendChartCard
                    key={id}
                    trend={viewModel.trends[id]}
                    emptyVariant={emptyVariant}
                  />
                ))}
              </div>
            </div>
          </>
        )}

        <DeploymentSignalCard
          state={signal.state}
          canConfigure={canConfigure}
          nowMs={nowMs}
          onRetry={signal.refetch}
        />
      </section>
    </div>
  );
}
