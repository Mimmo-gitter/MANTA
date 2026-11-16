
import type {
  PreviewScenario,
  PreviewScenarioOption,
  SprintWipData,
} from "@/types/sprint-wip";

export const PREVIEW_SCENARIOS: PreviewScenarioOption[] = [
  { id: "healthy", label: "Healthy" },
  { id: "warning", label: "At risk" },
  { id: "critical", label: "WIP critical" },
  { id: "upcoming", label: "Upcoming" },
];

export const SPRINT_WIP_MOCK_DATA: Record<
  Exclude<PreviewScenario, "upcoming">,
  SprintWipData
> = {
  healthy: {
    sprint: "Sprint 24 · Core Engine",
    dates: "Oct 14, 2026 – Oct 28, 2026",
    day: "Day 8 of 14",
    dayCount: "57% elapsed",
    status: "healthy",

    remaining: 48,
    ideal: 52,
    variance: "+4 SP",
    blocked: 2,

    completion: "Oct 28",
    completionNote: "On target",

    recommendation: "Sprint health looks good",
    recommendationDetail:
      "The team is tracking close to the ideal burndown. Keep the current focus and review the two blocked issues.",

    wip: [
      { name: "To Do", count: 14, limit: 20, tone: "green" },
      { name: "In Progress", count: 5, limit: 6, tone: "green" },
      { name: "In Review", count: 4, limit: 6, tone: "cyan" },
      { name: "Testing / QA", count: 3, limit: 4, tone: "blue" },
      { name: "Done", count: 22, limit: 24, tone: "green" },
    ],

    chart: [
      { day: "Oct 14", actual: 120, ideal: 120 },
      { day: "Oct 15", actual: 108, ideal: 111 },
      { day: "Oct 16", actual: 99, ideal: 103 },
      { day: "Oct 17", actual: 88, ideal: 94 },
      { day: "Oct 18", actual: 80, ideal: 86 },
      { day: "Oct 19", actual: 72, ideal: 77 },
      { day: "Oct 20", actual: 65, ideal: 69 },
      { day: "Oct 21", actual: 48, ideal: 60 },
      { day: "Oct 22", actual: null, ideal: 51 },
      { day: "Oct 23", actual: null, ideal: 43 },
      { day: "Oct 24", actual: null, ideal: 34 },
      { day: "Oct 25", actual: null, ideal: 26 },
      { day: "Oct 26", actual: null, ideal: 17 },
      { day: "Oct 27", actual: null, ideal: 9 },
      { day: "Oct 28", actual: null, ideal: 0 },
    ],
  },

  warning: {
    sprint: "Sprint 24 · Core Engine",
    dates: "Oct 14, 2026 – Oct 28, 2026",
    day: "Day 11 of 14",
    dayCount: "79% elapsed",
    status: "warning",

    remaining: 72,
    ideal: 43,
    variance: "+28 SP",
    blocked: 4,

    completion: "Nov 02",
    completionNote: "5 days late",

    recommendation: "Sprint is falling behind schedule",
    recommendationDetail:
      "The actual burndown is above the ideal trajectory. Review scope and unblock work currently waiting for review.",

    wip: [
      { name: "To Do", count: 19, limit: 24, tone: "green" },
      { name: "In Progress", count: 7, limit: 6, tone: "cyan" },
      { name: "In Review", count: 4, limit: 6, tone: "cyan" },
      { name: "Testing / QA", count: 3, limit: 4, tone: "blue" },
      { name: "Done", count: 12, limit: 24, tone: "green" },
    ],

    chart: [
      { day: "Oct 14", actual: 120, ideal: 120 },
      { day: "Oct 15", actual: 116, ideal: 111 },
      { day: "Oct 16", actual: 108, ideal: 103 },
      { day: "Oct 17", actual: 104, ideal: 94 },
      { day: "Oct 18", actual: 98, ideal: 86 },
      { day: "Oct 19", actual: 91, ideal: 77 },
      { day: "Oct 20", actual: 85, ideal: 69 },
      { day: "Oct 21", actual: 79, ideal: 60 },
      { day: "Oct 22", actual: 72, ideal: 51 },
      { day: "Oct 23", actual: null, ideal: 43 },
      { day: "Oct 24", actual: null, ideal: 34 },
      { day: "Oct 25", actual: null, ideal: 26 },
      { day: "Oct 26", actual: null, ideal: 17 },
      { day: "Oct 27", actual: null, ideal: 9 },
      { day: "Oct 28", actual: null, ideal: 0 },
    ],
  },

  critical: {
    sprint: "Sprint 24 · Core Engine",
    dates: "Oct 14, 2026 – Oct 28, 2026",
    day: "Day 11 of 14",
    dayCount: "79% elapsed",
    status: "critical",

    remaining: 72,
    ideal: 43,
    variance: "+8 SP",
    blocked: 2,

    completion: "Oct 29",
    completionNote: "1 day late",

    recommendation:
      "Automated remediation suggested: rebalance reviewer queue",
    recommendationDetail:
      "The In Review stage has exceeded its configured WIP limit. Reassign review work to restore flow and reduce queue age.",

    wip: [
      { name: "To Do", count: 11, limit: 20, tone: "green" },
      { name: "In Progress", count: 6, limit: 6, tone: "green" },
      { name: "In Review", count: 8, limit: 4, tone: "red" },
      { name: "Testing / QA", count: 2, limit: 4, tone: "blue" },
      { name: "Done", count: 19, limit: 24, tone: "green" },
    ],

    chart: [
      { day: "Oct 14", actual: 120, ideal: 120 },
      { day: "Oct 15", actual: 112, ideal: 111 },
      { day: "Oct 16", actual: 105, ideal: 103 },
      { day: "Oct 17", actual: 96, ideal: 94 },
      { day: "Oct 18", actual: 91, ideal: 86 },
      { day: "Oct 19", actual: 84, ideal: 77 },
      { day: "Oct 20", actual: 78, ideal: 69 },
      { day: "Oct 21", actual: 72, ideal: 60 },
      { day: "Oct 22", actual: 72, ideal: 51 },
      { day: "Oct 23", actual: null, ideal: 43 },
      { day: "Oct 24", actual: null, ideal: 34 },
      { day: "Oct 25", actual: null, ideal: 26 },
      { day: "Oct 26", actual: null, ideal: 17 },
      { day: "Oct 27", actual: null, ideal: 9 },
      { day: "Oct 28", actual: null, ideal: 0 },
    ],
  },
};
