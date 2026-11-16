"use client";

import { useCallback, useMemo, useState } from "react";
import { SprintBurndownChart } from "@/components/sprint-wip/sprint-burndown-chart";
import { WipCongestion } from "@/components/sprint-wip/wip-congestion";
import { SprintHealthRecommendation } from "@/components/sprint-wip/sprint-health-recommendation";
import { SprintSummary } from "@/components/sprint-wip/sprint-summary";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
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
  Rocket,
  Sparkles,
} from "lucide-react";
import { useAuth } from "@/providers/auth-provider";
import { usePermissions } from "@/hooks/use-permissions";
import { useProjectsQuery } from "@/hooks/use-projects";
import type { ProjectItem } from "@/types/project";
import { useSprintWip } from "@/hooks/use-sprint-wip";

import { PREVIEW_SCENARIOS } from "@/lib/sprint-wip/mocks/sprint-wip-mock-data";

import type { PreviewScenario, SprintWipData } from "@/types/sprint-wip";

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

function ActiveDashboard({ data }: { data: SprintWipData }) {
  const isHealthy = data.status === "healthy";
  const isCritical = data.status === "critical";

  const accent = isHealthy ? "#00d9a0" : isCritical ? "#fb7185" : "#f6b94b";

  return (
    <>
      <div className="grid gap-3 xl:grid-cols-[minmax(0,1.7fr)_minmax(280px,.9fr)]">
        {/* Sprint Burndown */}
        <section className={`${PANEL} min-w-0 p-4`}>
          <PanelTitle title="Sprint Burndown" meta="STORY POINTS" />

          <p className="mb-4 text-xs leading-relaxed text-[#94a1b5]">
            Actual remaining story points compared with the ideal sprint
            trajectory.
          </p>

          <SprintBurndownChart
            data={data.chart}
            accent={accent}
            scenario={data.status}
          />

          <SprintSummary
            variance={data.variance}
            blocked={data.blocked}
            completion={data.completion}
            completionNote={data.completionNote}
            status={data.status}
          />
        </section>

        {/* WIP Congestion */}
        <WipCongestion items={data.wip} status={data.status} />
      </div>

      {/* Sprint Health Recommendation */}
      <SprintHealthRecommendation
        status={data.status}
        recommendation={data.recommendation}
        recommendationDetail={data.recommendationDetail}
      />
    </>
  );
}

export default function SprintWipPage() {
  const { isAuthenticated } = useAuth();
  const { can } = usePermissions();
  const canView = can("VIEW_SPRINT_BURNDOWN_WIP");

  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const requestedPreview = searchParams.get("preview");

  const previewEnabled =
    process.env.NODE_ENV !== "production" &&
    PREVIEW_SCENARIOS.some((s) => s.id === requestedPreview);

  const preview = previewEnabled ? (requestedPreview as PreviewScenario) : null;

  const projectsQuery = useProjectsQuery(isAuthenticated && canView);
  const projects = projectsQuery.data ?? [];

  const requestedProjectId = searchParams.get("project");

  const selectedProject =
    projects.find((project) => project.id === requestedProjectId) ??
    projects[0] ??
    null;

  const [isProjectDropdownOpen, setIsProjectDropdownOpen] = useState(false);

  const sprintQuery = useSprintWip({
    projectId: selectedProject?.id ?? null,
    enabled: Boolean(selectedProject?.id && isAuthenticated && canView),
    mockScenario: preview,
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

  const sprintData =
    sprintQuery.data?.kind === "ok" ? sprintQuery.data.data : null;

  const unavailableReason =
    sprintQuery.data?.kind === "unavailable" ? sprintQuery.data.reason : null;

  const unexpectedError = sprintQuery.isError;

  const status = sprintData?.status ?? "upcoming";

  const sprint = sprintData;

  const projectOptions = useMemo(
    () =>
      projects.map((project: ProjectItem) => ({
        id: project.id,
        name: project.name,
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
            {preview && (
              <span className="rounded border border-[#70551e] bg-[#332a17] px-2 py-1 font-mono text-[8px] font-bold tracking-wider text-[#f6c453]">
                DEMO PREVIEW
              </span>
            )}
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
                0 repos
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
                        updateParams({ project: project.id });
                        setIsProjectDropdownOpen(false);
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
                        0 repos
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

      {process.env.NODE_ENV !== "production" && (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-[#263448] bg-[#111a27] px-3 py-2">
          <span className="mr-1 flex items-center gap-1.5 text-[9px] font-semibold uppercase tracking-wider text-[#8290a6]">
            <Sparkles className="size-3 text-[#f6c453]" />
            UI state preview
          </span>

          {PREVIEW_SCENARIOS.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => updateParams({ preview: item.id })}
              className={`rounded px-2.5 py-1.5 text-[9px] font-semibold transition ${
                preview === item.id
                  ? "bg-[#00d9a0] text-[#061b16]"
                  : "border border-[#293448] bg-[#182232] text-[#aab6c8] hover:border-[#00d9a0]/60"
              }`}
            >
              {item.label}
            </button>
          ))}

          <button
            type="button"
            onClick={() => updateParams({ preview: null })}
            className="ml-auto text-[9px] text-[#8290a6] hover:text-white"
          >
            Use API data
          </button>
        </div>
      )}

      <section className="flex flex-col gap-3 rounded-[10px] border border-[#202a3b] bg-[#151c28] px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-[#21483f] bg-[#12352e] text-[#38d9a7]">
            <GitBranch className="size-4" />
          </div>

          <div className="min-w-0">
            <p className={LABEL}>Selected project</p>
            <p className="truncate text-sm font-semibold text-[#e2eaf4]">
              {selectedProject.name}
              {sprint ? (
                <span className="font-normal text-[#8290a6]">
                  {" "}
                  · {sprint.sprint}
                </span>
              ) : (
                <span className="font-normal text-[#8290a6]">
                  {" "}
                  · Upcoming sprint
                </span>
              )}
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-3 sm:justify-end">
          <span className="flex items-center gap-2 text-xs text-[#aab7c9]">
            <CalendarDays className="size-3" />
            {sprint?.dates ?? "Sprint dates not available"}
          </span>

          <span className="flex items-center gap-2 text-xs text-[#aab7c9]">
            <Clock3 className="size-3" />
            {sprint
              ? `${sprint.day} · ${sprint.dayCount}`
              : "Waiting for sprint data"}
          </span>

          <StatusPill status={status} />
        </div>
      </section>

      {preview === "upcoming" || !preview ? (
        <>
          {unexpectedError && (
            <div className="flex items-start gap-2 rounded-lg border border-[#71333a] bg-[#321e27] p-3 text-[10px] text-[#ff9aa1]">
              <AlertTriangle className="mt-0.5 size-4 shrink-0" />
              <div>
                <b>Unable to retrieve sprint data.</b>
                <p className="mt-0.5 text-[#c18b93]">
                  {sprintQuery.error instanceof Error
                    ? sprintQuery.error.message
                    : "The sprint endpoint returned an error."}
                </p>
                <button
                  className="mt-2 underline"
                  onClick={() => void sprintQuery.refetch()}
                >
                  Try again
                </button>
              </div>
            </div>
          )}

          {sprintQuery.data?.kind === "unavailable" && !preview && (
            <div className="flex items-start gap-2 rounded-lg border border-[#70551e] bg-[#2b2519] p-3 text-[10px] text-[#f6c453]">
              <Info className="mt-0.5 size-4 shrink-0" />
              <div>
                <b>Sprint data is currently unavailable.</b>
                <p className="mt-0.5 text-[#c5ae7c]">{unavailableReason}</p>
              </div>
            </div>
          )}

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
      ) : (
        <>
          {sprintData && <ActiveDashboard data={sprintData} />}

          <p className="text-right font-mono text-[8px] text-[#66758c]">
            Illustrative preview values · Not retrieved from the backend
          </p>
        </>
      )}
    </main>
  );
}
