"use client";

import { useCallback, useMemo, useState } from "react";
import { SprintBurndownChart } from "@/components/sprint-wip/sprint-burndown-chart";
import { WipCongestion } from "@/components/sprint-wip/wip-congestion";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { WipByDeveloper } from "@/components/sprint-wip/wip-by-developer";
import { buildWipItems, getWipStatus } from "@/lib/sprint-wip";
import { useProjectSprints } from "@/hooks/use-project-sprints";
import { SprintSummary } from "@/components/sprint-wip/sprint-summary";
import { SprintHealthRecommendation } from "@/components/sprint-wip/sprint-health-recommendation";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  ArrowUpRight,
  CalendarDays,
  ChevronDown,
  Clock3,
  GitBranch,
  Info,
  Lock,
  Network,
  RefreshCw,
  ChevronsUpDown,
  Rocket,
  Sparkles,
} from "lucide-react";
import { useAuth } from "@/providers/auth-provider";
import { usePermissions } from "@/hooks/use-permissions";
import { useProjectsQuery } from "@/hooks/use-projects";
import type { ProjectItem } from "@/types/project";
import { useSprintWip } from "@/hooks/use-sprint-wip";
import type {
  FlowMetricsResponse,
  SprintWipStatus,
  BurndownPoint,
  WipItem,
  WipTone,
} from "@/types/sprint-wip";

const PANEL = "rounded-[10px] border border-[#202738] bg-[#151b27]";
const LABEL =
  "font-mono text-[11px] font-semibold uppercase tracking-[0.1em] text-[#8997ad]";

const BUTTON =
  "inline-flex h-9 items-center justify-center gap-2 rounded-md border border-[#2a3447] bg-[#1b2432] px-3 text-xs font-semibold text-[#cbd5e1] transition hover:border-[#00d9a0]/60 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00d9a0]";

function StatusPill({
  status,
}: {
  status: "healthy" | "warning" | "critical" | "upcoming";
}) {
  const map = {
    healthy: {
      label: "ACTIVE",
      cls: "border-[#145b4b] bg-[#0d332d] text-[#42e6b2]",
      dot: "bg-[#42e6b2]",
    },
    warning: {
      label: "BEHIND SCHEDULE",
      cls: "border-[#70551e] bg-[#332a17] text-[#f6c453]",
      dot: "bg-[#f6c453]",
    },
    critical: {
      label: "WIP CRITICAL",
      cls: "border-[#71333a] bg-[#3a2028] text-[#ff7b83]",
      dot: "bg-[#ff7b83]",
    },
    upcoming: {
      label: "INITIALIZED",
      cls: "border-[#34465b] bg-[#1b2a3b] text-[#9bb7d5]",
      dot: "bg-[#9bb7d5]",
    },
  }[status];

  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-1 font-mono text-[8px] font-bold tracking-[.08em] ${map.cls}`}
    >
      <span className={`size-1.5 rounded-full ${map.dot}`} />
      {map.label}
    </span>
  );
}

function PanelTitle({
  icon,
  title,
  meta,
}: {
  icon?: React.ReactNode;
  title: string;
  meta?: string;
}) {
  return (
    <div className="mb-3 flex items-center justify-between gap-3">
      <div className="flex items-center gap-2 text-[#e3eaf4]">
        {icon && icon}
        <h2 className="text-base font-semibold">{title}</h2>
      </div>
      {meta && <span className={LABEL}>{meta}</span>}
    </div>
  );
}

function BurndownTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: Array<{
    dataKey: string;
    value: number | null;
    payload?: {
      actual: number | null;
      ideal: number;
    };
  }>;
  label?: string;
}) {
  if (!active || !payload?.length) return null;

  const point = payload.find((item) => item.payload)?.payload;

  if (!point) return null;

  const actual = point.actual;
  const ideal = point.ideal;
  const variance = actual !== null ? actual - ideal : null;

  return (
    <div className="min-w-[210px] overflow-hidden rounded-xl border border-[#34445a] bg-[#111a28] shadow-[0_12px_40px_rgba(0,0,0,0.5)]">
      <div className="flex items-center justify-between border-b border-[#29364a] bg-[#192536] px-3 py-2.5">
        <div>
          <p className="text-[11px] font-semibold text-white">{label}</p>
          <p className="mt-0.5 text-[8px] uppercase tracking-wider text-[#8293aa]">
            Sprint progress
          </p>
        </div>
        <span className="rounded-md border border-[#17624e] bg-[#10372e] px-2 py-1 text-[8px] font-bold text-[#40e2b0]">
          SPRINT DAY
        </span>
      </div>

      <div className="space-y-3 p-3">
        <div className="flex items-center justify-between gap-5">
          <div className="flex items-center gap-2">
            <span className="h-[3px] w-4 rounded-full bg-[#00d9a0]" />
            <span className="text-[10px] text-[#aab7c9]">Actual remaining</span>
          </div>
          <span className="font-mono text-[11px] font-semibold text-[#40e2b0]">
            {actual === null ? "—" : `${actual} SP`}
          </span>
        </div>

        <div className="flex items-center justify-between gap-5">
          <div className="flex items-center gap-2">
            <span className="w-4 border-t-2 border-dashed border-[#55c9d5]" />
            <span className="text-[10px] text-[#aab7c9]">Ideal target</span>
          </div>
          <span className="font-mono text-[11px] font-semibold text-[#70d5df]">
            {ideal} SP
          </span>
        </div>

        <div className="border-t border-[#29364a] pt-2.5">
          <div className="flex items-center justify-between">
            <span className="text-[9px] font-medium uppercase tracking-wider text-[#8293aa]">
              Variance
            </span>
            {variance !== null ? (
              <span
                className={`font-mono text-[11px] font-bold ${
                  variance <= 0 ? "text-[#40e2b0]" : "text-[#f6c453]"
                }`}
              >
                {variance > 0 ? "+" : ""}
                {variance} SP
                <span className="ml-1 text-[9px] font-normal">
                  (
                  {ideal === 0
                    ? "—"
                    : `${((variance / ideal) * 100).toFixed(1)}%`}
                  )
                </span>
              </span>
            ) : (
              <span className="text-[10px] text-[#8293aa]">Not recorded</span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function Metric({
  label,
  value,
  note,
  tone = "normal",
  icon,
}: {
  label: string;
  value: string;
  note: string;
  tone?: "normal" | "good" | "warn" | "bad";
  icon: React.ReactNode;
}) {
  const color =
    tone === "good"
      ? "text-[#43e0ae]"
      : tone === "warn"
        ? "text-[#f6c453]"
        : tone === "bad"
          ? "text-[#ff7b83]"
          : "text-[#e4eaf3]";

  return (
    <div className="min-w-0 border-r border-[#242d3d] px-3 py-3 last:border-r-0">
      <div className="mb-2 flex items-center gap-1.5 text-[#78869e]">
        {icon}
        <span className={LABEL}>{label}</span>
      </div>
      <div
        className={`text-[20px] font-semibold leading-none tracking-tight ${color}`}
      >
        {value}
      </div>
      <p className="mt-1.5 text-[9px] leading-4 text-[#78869e]">{note}</p>
    </div>
  );
}

function UpcomingEmpty({ projectName }: { projectName: string }) {
  return (
    <div className={`${PANEL} grid min-h-[390px] place-items-center p-6`}>
      <div className="max-w-md text-center">
        <div className="mx-auto mb-4 flex size-12 items-center justify-center rounded-xl border border-[#2c4054] bg-[#1a2a3b] text-[#65cce5]">
          <CalendarDays className="size-5" />
        </div>
        <p className={LABEL}>NO ACTIVE SPRINT DATA</p>
        <h2 className="mt-2 text-lg font-semibold text-[#e5ebf4]">
          Your next sprint starts here
        </h2>
        <p className="mt-2 text-[11px] leading-5 text-[#8b9ab0]">
          There is no active sprint data available for {projectName}. Connect
          your Jira workspace and configure sprint scope to start tracking
          burndown and workflow congestion.
        </p>
        <div className="mt-5 flex flex-wrap justify-center gap-2">
          <Link href="/integrations" className={BUTTON}>
            Connect Jira <ArrowUpRight className="size-3" />
          </Link>
          <Link href="/projects" className={BUTTON}>
            Explore projects <ArrowRight className="size-3" />
          </Link>
        </div>
      </div>
    </div>
  );
}

function formatSprintDate(value: string): string {
  const date = new Date(`${value.slice(0, 10)}T12:00:00`);

  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(date);
}

function ActiveDashboard({
  data,
  wipItems,
  sprintEndDate,
}: {
  data: FlowMetricsResponse;
  wipItems: WipItem[];
  sprintEndDate: string | null;
}) {
  const wipStatus = getWipStatus(wipItems);

  const actualPoints = data.burndown.filter(
    (point) => point.remaining_sp !== null,
  );

  const firstActualPoint = actualPoints[0] ?? null;
  const latestActualPoint = actualPoints[actualPoints.length - 1] ?? null;

  // Backlog Delta: actual remaining SP minus ideal remaining SP.
  const variance =
    latestActualPoint?.remaining_sp !== null && latestActualPoint !== null
      ? Number(
          (
            latestActualPoint.remaining_sp! - latestActualPoint.ideal_sp
          ).toFixed(1),
        )
      : null;

  const varianceLabel =
    variance === null ? "—" : `${variance > 0 ? "+" : ""}${variance} SP`;

  // Overall status combines burndown variance and WIP congestion.
  const outsideTolerance = variance !== null && Math.abs(variance) > 8;

  const summaryStatus: SprintWipStatus =
    wipStatus === "critical"
      ? "critical"
      : wipStatus === "warning" || outsideTolerance
        ? "warning"
        : "healthy";

  // Count unfinished issues in statuses containing "blocked".
  const blockedCount = data.wip
    .filter((item) => item.status.toLowerCase().includes("blocked"))
    .reduce((total, item) => total + item.count, 0);

  // Estimate completion from observed story-point throughput.
  let velocity: number | null = null;
  let estimatedCompletionDate: Date | null = null;

  if (
    firstActualPoint &&
    latestActualPoint &&
    firstActualPoint.remaining_sp !== null &&
    latestActualPoint.remaining_sp !== null
  ) {
    const firstTime = new Date(
      `${firstActualPoint.date.slice(0, 10)}T00:00:00Z`,
    ).getTime();

    const latestTime = new Date(
      `${latestActualPoint.date.slice(0, 10)}T00:00:00Z`,
    ).getTime();

    const elapsedDays = Math.round((latestTime - firstTime) / 86_400_000);

    const completedSP =
      firstActualPoint.remaining_sp - latestActualPoint.remaining_sp;

    if (elapsedDays > 0 && completedSP > 0) {
      velocity = completedSP / elapsedDays;
    }

    if (latestActualPoint.remaining_sp <= 0) {
      estimatedCompletionDate = new Date(latestTime);
    } else if (velocity !== null && velocity > 0) {
      const daysRemaining = Math.ceil(
        latestActualPoint.remaining_sp / velocity,
      );

      estimatedCompletionDate = new Date(latestTime);
      estimatedCompletionDate.setUTCDate(
        estimatedCompletionDate.getUTCDate() + daysRemaining,
      );
    }
  }

  const deadline = sprintEndDate?.slice(0, 10) ?? null;

  const estimatedDateLabel = estimatedCompletionDate
    ? new Intl.DateTimeFormat("en-US", {
        month: "short",
        day: "numeric",
        timeZone: "UTC",
      }).format(estimatedCompletionDate)
    : "—";

  const estimatedDateISO = estimatedCompletionDate?.toISOString().slice(0, 10);

  const isOnTarget =
    estimatedDateISO && deadline ? estimatedDateISO <= deadline : null;

  const completionNote = !estimatedCompletionDate
    ? "Insufficient data"
    : isOnTarget === null
      ? "Forecast"
      : isOnTarget
        ? "On Target"
        : "Behind Deadline";

  const completionDetail =
    velocity !== null
      ? `Velocity ${velocity.toFixed(1)} SP/day ${
          isOnTarget === true
            ? "meets"
            : isOnTarget === false
              ? "may miss"
              : "projects"
        } the sprint deadline.`
      : "Not enough completed work to estimate a reliable finish date.";

  const recommendationDetail = [
    variance !== null
      ? `Current burndown variance is ${varianceLabel}.`
      : "Burndown variance is not available yet.",
    wipStatus === "critical"
      ? "One or more workflow stages exceed their estimated WIP limits."
      : wipStatus === "warning"
        ? "One or more workflow stages have reached their estimated WIP limits."
        : "All active workflow stages remain within their automatically estimated WIP limits.",
    blockedCount > 0
      ? `${blockedCount} blocked issue(s) require attention.`
      : "",
  ]
    .filter(Boolean)
    .join(" ");

  const accent =
    summaryStatus === "healthy"
      ? "#00d9a0"
      : summaryStatus === "critical"
        ? "#fb7185"
        : "#f6b94b";

  const chart: BurndownPoint[] = data.burndown.map((point) => ({
    day: point.date,
    actual: point.remaining_sp,
    ideal: point.ideal_sp,
  }));

  return (
    <div className="space-y-3">
      <div className="grid gap-3 xl:grid-cols-[minmax(0,1.7fr)_minmax(280px,.9fr)]">
        <section className={`${PANEL} min-w-0 p-4`}>
          {chart.length > 0 ? (
            <SprintBurndownChart
              data={chart}
              accent={accent}
              scenario={summaryStatus}
            />
          ) : (
            <div className="grid min-h-[250px] place-items-center text-center text-xs text-[#8290a6]">
              No burndown data available yet.
            </div>
          )}
        </section>

        <WipCongestion items={wipItems} status={wipStatus} />
      </div>

      <WipByDeveloper data={data.wip_by_developer} />

      <SprintSummary
        variance={varianceLabel}
        blocked={blockedCount}
        completion={estimatedDateLabel}
        completionNote={completionNote}
        completionDetail={completionDetail}
        status={summaryStatus}
      />

      <SprintHealthRecommendation
        status={summaryStatus}
        recommendation="Sprint Health Recommendation"
        recommendationDetail={recommendationDetail}
      />
    </div>
  );
}

export default function SprintWipPage() {
  const { isAuthenticated } = useAuth();
  const { can } = usePermissions();
  const canView = can("VIEW_SPRINT_BURNDOWN_WIP");

  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const projectsQuery = useProjectsQuery(isAuthenticated && canView);
  const projects = projectsQuery.data ?? [];

  const requestedProjectId = searchParams.get("project");

  const selectedProject =
    projects.find((project) => project.id === requestedProjectId) ??
    projects[0] ??
    null;

  const [isProjectDropdownOpen, setIsProjectDropdownOpen] = useState(false);
  const [isSprintDropdownOpen, setIsSprintDropdownOpen] = useState(false);

  const requestedSprintId = searchParams.get("sprint");

  const sprintQuery = useSprintWip({
    projectId: selectedProject?.id ?? null,
    sprintId: requestedSprintId,
    enabled: Boolean(selectedProject?.id && isAuthenticated && canView),
  });

  const sprintsQuery = useProjectSprints({
    projectId: selectedProject?.id ?? null,
    enabled: Boolean(selectedProject?.id && isAuthenticated && canView),
  });

  const updateParams = useCallback(
    (patch: Record<string, string | null>) => {
      const next = new URLSearchParams(searchParams.toString());

      Object.entries(patch).forEach(([key, value]) =>
        value === null ? next.delete(key) : next.set(key, value),
      );

      const query = next.toString();

      router.replace(query ? `${pathname}?${query}` : pathname, {
        scroll: false,
      });
    },
    [pathname, router, searchParams],
  );

  const flowMetrics = sprintQuery.data ?? null;

  const sprint = flowMetrics?.sprint ?? null;

  const availableSprints = sprintsQuery.data ?? [];

  const selectedSprintId = requestedSprintId ?? sprint?.id ?? null;

  const selectedSprint =
    availableSprints.find((item) => item.id === selectedSprintId) ?? null;

  const burndownPoints = flowMetrics?.burndown ?? [];

  const firstSprintDay = burndownPoints[0]?.date ?? null;

  const lastSprintDay = burndownPoints[burndownPoints.length - 1]?.date ?? null;

  const lastActualIndex = burndownPoints.reduce(
    (latestIndex, point, index) =>
      point.remaining_sp !== null ? index : latestIndex,
    -1,
  );

  const currentSprintDay = lastActualIndex >= 0 ? lastActualIndex + 1 : 0;

  const totalSprintDays = burndownPoints.length;

  const elapsedPercent =
    totalSprintDays > 0
      ? Math.round((currentSprintDay / totalSprintDays) * 100)
      : 0;

  const latestActualPoint =
    lastActualIndex >= 0 ? burndownPoints[lastActualIndex] : null;

  const isOnTrack =
    latestActualPoint !== null &&
    latestActualPoint.remaining_sp !== null &&
    latestActualPoint.remaining_sp <= latestActualPoint.ideal_sp;

  const sprintDatesLabel =
    firstSprintDay && lastSprintDay
      ? `${formatSprintDate(firstSprintDay)} — ${formatSprintDate(lastSprintDay)}`
      : "Sprint dates unavailable";

  const sprintProgressLabel =
    currentSprintDay > 0
      ? `Day ${currentSprintDay} of ${totalSprintDays} (${elapsedPercent}% elapsed)`
      : "Sprint progress unavailable";

  const sprintStateLabel = sprint?.state?.toUpperCase() ?? "UNKNOWN";

  const wipItems = useMemo(
    () => (flowMetrics ? buildWipItems(flowMetrics) : []),
    [flowMetrics],
  );

  const calculatedWipStatus = getWipStatus(wipItems);

  const status: SprintWipStatus | "upcoming" = !sprint
    ? "upcoming"
    : calculatedWipStatus;

  const projectOptions = useMemo(
    () =>
      projects.map((project: ProjectItem) => ({
        id: project.id,
        name: project.name,
        repositoryCount: project.repositories.length,
      })),
    [projects],
  );

  if (!canView) {
    return (
      <div className="mx-auto w-full max-w-[1500px] p-6">
        <div
          className={`${PANEL} mx-auto flex max-w-xl flex-col items-center p-10 text-center`}
        >
          <Lock className="mb-3 size-6 text-[#f6c453]" />
          <h1 className="text-base font-semibold text-[#e3eaf4]">
            You don’t have access to Sprint & WIP Monitoring
          </h1>
          <p className="mt-2 text-xs leading-5 text-[#8290a6]">
            Your role does not include permission to view sprint burndown and
            WIP metrics. Contact a workspace administrator if you need access.
          </p>
        </div>
      </div>
    );
  }

  if (projectsQuery.isLoading) {
    return (
      <div className="mx-auto max-w-[1500px] animate-pulse p-6">
        <div className="mb-5 h-7 w-64 rounded bg-[#202a39]" />
        <div className="h-20 rounded-lg bg-[#171f2c]" />
        <div className="mt-4 grid grid-cols-2 gap-4">
          <div className="h-[420px] rounded-lg bg-[#171f2c]" />
          <div className="h-[420px] rounded-lg bg-[#171f2c]" />
        </div>
      </div>
    );
  }

  if (projectsQuery.isError) {
    return (
      <div className="mx-auto max-w-[900px] p-6">
        <div className={`${PANEL} p-6`}>
          <AlertTriangle className="mb-2 size-5 text-[#fb7185]" />
          <h1 className="text-sm font-semibold">Unable to load projects</h1>
          <p className="mt-1 text-xs text-[#8290a6]">
            {projectsQuery.error instanceof Error
              ? projectsQuery.error.message
              : "The project list could not be loaded."}
          </p>
          <button
            className={`${BUTTON} mt-4`}
            onClick={() => void projectsQuery.refetch()}
          >
            <RefreshCw className="size-3" />
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (!selectedProject) {
    return (
      <div className="mx-auto max-w-[900px] p-6">
        <div className={`${PANEL} p-8 text-center`}>
          <GitBranch className="mx-auto mb-3 size-6 text-[#00d9a0]" />
          <h1 className="text-sm font-semibold">No projects yet</h1>
          <p className="mt-1 text-xs text-[#8290a6]">
            Create or import a project to start monitoring sprint progress.
          </p>
          <Link href="/projects" className={`${BUTTON} mt-4`}>
            Go to Projects
            <ArrowRight className="size-3" />
          </Link>
        </div>
      </div>
    );
  }

  return (
    <main
      className="mx-auto w-full max-w-[1600px] space-y-4 p-4 pb-8 sm:p-5 lg:p-6"
      data-testid="sprint-wip-page"
    >
      <header className="flex flex-col gap-3 xl:flex-row xl:items-end xl:justify-between">
        <div>
          <div className="mb-1 flex flex-wrap items-center gap-2">
            <h1 className="text-2xl font-semibold tracking-[-.5px] text-[#e5ebf4]">
              Sprint &amp; WIP Monitoring
            </h1>
          </div>
          <p className="max-w-2xl text-[13px] leading-6 text-[#94a1b5]">
            Monitor sprint delivery progress and identify workflow congestion
            across your engineering team.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <label className="font-mono text-[10px] font-semibold tracking-[0.12em] text-[#8997ad]">
            PROJECT
          </label>

          <div className="relative w-[270px]">
            <button
              type="button"
              onClick={() => setIsProjectDropdownOpen((previous) => !previous)}
              className="flex h-10 w-full items-center gap-2 rounded-xl border border-[#293448] bg-[#151d2a] px-3 text-left transition hover:border-[#3c506b]"
            >
              <Network className="size-4 shrink-0 text-[#22d3ee]" />

              <span className="min-w-0 flex-1 truncate text-xs font-semibold text-[#dce5f0]">
                {selectedProject.name}
              </span>

              <span className="rounded-md bg-[#1c2b38] px-1.5 py-0.5 font-mono text-[10px] font-semibold text-[#22d3ee]">
                {selectedProject.repositories?.length ?? 0} repos
              </span>

              <ChevronDown
                className={`size-3.5 shrink-0 text-[#8290a6] transition-transform ${
                  isProjectDropdownOpen ? "rotate-180" : ""
                }`}
              />
            </button>

            {isProjectDropdownOpen && (
              <div className="absolute left-0 top-[calc(100%+5px)] z-50 w-full rounded-xl border border-[#293448] bg-[#111925] p-1.5 shadow-[0_12px_35px_rgba(0,0,0,0.45)]">
                {projectOptions.map((project) => {
                  const isSelected = project.id === selectedProject.id;

                  return (
                    <button
                      key={project.id}
                      type="button"
                      onClick={() => {
                        updateParams({
                          project: project.id,
                          sprint:
                            project.id === selectedProject.id
                              ? requestedSprintId
                              : null,
                        });

                        setIsProjectDropdownOpen(false);
                        setIsSprintDropdownOpen(false);
                      }}
                      className={`flex h-10 w-full items-center gap-3 rounded-lg px-3 text-left transition ${
                        isSelected
                          ? "bg-[#1d303c] text-[#e2eaf4]"
                          : "text-[#aab7c9] hover:bg-[#1a2735] hover:text-white"
                      }`}
                    >
                      <span className="min-w-0 flex-1 truncate text-xs font-medium">
                        {project.name}
                      </span>

                      <span className="font-mono text-[10px] font-semibold text-[#22d3ee]">
                        {project.repositoryCount} repos
                      </span>

                      {isSelected && (
                        <span className="size-2 rounded-full bg-[#22d3ee]" />
                      )}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </header>

      <section className="grid grid-cols-1 gap-3 rounded-[10px] border border-[#202a3b] bg-[#151c28] px-4 py-3 lg:grid-cols-[292px_minmax(0,1fr)_auto] lg:items-center">
        {/* Sprint Selector */}
        <div className="relative flex min-w-0 items-center gap-3 sm:w-[292px]">
          <button
            type="button"
            onClick={() => setIsSprintDropdownOpen((previous) => !previous)}
            aria-expanded={isSprintDropdownOpen}
            aria-haspopup="listbox"
            className="flex h-[64px] w-full items-center gap-3 rounded-md bg-[#191e29] px-3 text-left transition hover:bg-[#202633]"
          >
            <Rocket className="size-4 shrink-0 text-[#38d9a7]" />

            <div className="min-w-0 flex-1">
              <p className="font-mono text-[10px] font-medium uppercase tracking-[0.12em] text-[#84958d]">
                Active Sprint
              </p>

              <p className="truncate text-sm font-semibold text-[#e0e4ed]">
                {selectedSprint?.name ?? sprint?.name ?? "No sprint selected"}
              </p>
            </div>

            <ChevronsUpDown className="size-3.5 shrink-0 text-[#82908b]" />
          </button>

          {isSprintDropdownOpen && (
            <div
              role="listbox"
              aria-label="Available sprints"
              className="absolute left-0 top-[calc(100%+8px)] z-50 max-h-72 w-full overflow-y-auto rounded-xl border border-[#293448] bg-[#111925] p-1.5 shadow-[0_12px_35px_rgba(0,0,0,0.45)]"
            >
              <p className="px-3 py-2 font-mono text-[9px] font-semibold uppercase tracking-wider text-[#8290a6]">
                Select Sprint
              </p>

              {sprintsQuery.isLoading && (
                <p className="px-3 py-4 text-xs text-[#8290a6]">
                  Loading sprints...
                </p>
              )}

              {sprintsQuery.isError && (
                <div className="px-3 py-3">
                  <p className="text-xs text-[#ff9aa1]">
                    Unable to load sprints.
                  </p>
                  <button
                    type="button"
                    onClick={() => void sprintsQuery.refetch()}
                    className="mt-2 text-xs text-[#42e6b2] hover:underline"
                  >
                    Try again
                  </button>
                </div>
              )}

              {!sprintsQuery.isLoading &&
                !sprintsQuery.isError &&
                availableSprints.length === 0 && (
                  <p className="px-3 py-4 text-xs text-[#8290a6]">
                    No saved sprints found for this project.
                  </p>
                )}

              {availableSprints.map((item) => {
                const isSelected = item.id === selectedSprintId;

                return (
                  <button
                    key={item.id}
                    type="button"
                    role="option"
                    aria-selected={isSelected}
                    onClick={() => {
                      updateParams({ sprint: item.id });
                      setIsSprintDropdownOpen(false);
                    }}
                    className={`flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left transition ${
                      isSelected
                        ? "bg-[#1d303c] text-[#e2eaf4]"
                        : "text-[#aab7c9] hover:bg-[#1a2735] hover:text-white"
                    }`}
                  >
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-xs font-medium">
                        {item.name}
                      </p>
                      <p className="mt-1 font-mono text-[9px] uppercase text-[#8290a6]">
                        {item.state}
                      </p>
                    </div>

                    {isSelected && (
                      <span className="size-2 shrink-0 rounded-full bg-[#42e6b2]" />
                    )}
                  </button>
                );
              })}
            </div>
          )}
        </div>

        {/* Sprint Dates and Progress */}
        <div className="flex min-w-0 flex-wrap items-center gap-2 font-mono text-[11px] text-[#cbd5e1]">
          <CalendarDays className="size-4 shrink-0 text-[#9aa9bd]" />

          <span>{sprintDatesLabel}</span>

          <span className="text-[#536175]">•</span>

          <span className="text-[#a7bbaa]">{sprintProgressLabel}</span>
        </div>

        {/* Sprint Status */}
        <div className="flex flex-wrap items-center gap-2 lg:justify-self-end">
          <span className="inline-flex h-7 items-center gap-2 rounded-full bg-[#203a37] px-3 font-mono text-[10px] font-semibold tracking-wider text-[#42e6b2]">
            <span className="size-2 rounded-full bg-[#42e6b2]" />
            {sprintStateLabel}
          </span>

          <span
            className={`inline-flex h-7 items-center gap-2 rounded-full px-3 font-mono text-[10px] font-semibold tracking-wider ${
              !latestActualPoint
                ? "bg-[#263244] text-[#aab7c9]"
                : isOnTrack
                  ? "bg-[#203a37] text-[#42e6b2]"
                  : "bg-[#3b3020] text-[#f6c453]"
            }`}
          >
            <span
              className={`size-2 rounded-full ${
                !latestActualPoint
                  ? "bg-[#aab7c9]"
                  : isOnTrack
                    ? "bg-[#42e6b2]"
                    : "bg-[#f6c453]"
              }`}
            />

            {!latestActualPoint ? "NO DATA" : isOnTrack ? "ON TRACK" : "BEHIND"}
          </span>
        </div>
      </section>

      {sprintQuery.isLoading && (
        <div className={`${PANEL} grid min-h-[300px] place-items-center`}>
          <div className="text-center">
            <RefreshCw className="mx-auto mb-3 size-5 animate-spin text-[#00d9a0]" />
            <p className="text-xs text-[#94a1b5]">Loading sprint metrics...</p>
          </div>
        </div>
      )}

      {sprintQuery.isError && (
        <div className="flex items-start gap-2 rounded-lg border border-[#71333a] bg-[#321e27] p-3 text-[10px] text-[#ff9aa1]">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" />

          <div>
            <b>Unable to retrieve sprint data.</b>

            <p className="mt-1 text-[#c18b93]">{sprintQuery.error.message}</p>

            <button
              className="mt-2 underline"
              onClick={() => void sprintQuery.refetch()}
            >
              Try again
            </button>
          </div>
        </div>
      )}

      {!sprintQuery.isLoading && !sprintQuery.isError && !sprint && (
        <>
          <UpcomingEmpty projectName={selectedProject.name} />

          <section className="flex flex-col gap-3 rounded-[10px] border border-[#263448] bg-[#131d2a] px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-center gap-3">
              <div className="flex size-8 items-center justify-center rounded-lg bg-[#1b3545] text-[#53c9e7]">
                <Rocket className="size-4" />
              </div>

              <div>
                <p className="text-[11px] font-semibold text-[#dce5f0]">
                  Sprint initialization guide
                </p>

                <p className="text-[9px] text-[#8290a6]">
                  Connect Jira and establish sprint scope to start tracking
                  delivery.
                </p>
              </div>
            </div>

            <div className="flex flex-wrap gap-2">
              <Link href="/integrations" className={BUTTON}>
                View integrations
              </Link>

              <Link
                href="/projects"
                className="inline-flex h-8 items-center gap-1 rounded-md bg-[#00d9a0] px-3 text-[10px] font-bold text-[#061b16] hover:bg-[#34e6b4]"
              >
                Go to project planning
                <ArrowRight className="size-3" />
              </Link>
            </div>
          </section>
        </>
      )}

      {!sprintQuery.isLoading &&
        !sprintQuery.isError &&
        sprint &&
        flowMetrics && (
          <ActiveDashboard
            data={flowMetrics}
            wipItems={wipItems}
            sprintEndDate={selectedSprint?.end_date ?? null}
          />
        )}
    </main>
  );
}
