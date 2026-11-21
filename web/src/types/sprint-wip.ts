export type SprintWipStatus = "healthy" | "warning" | "critical";

export type WipTone = "green" | "cyan" | "blue" | "red";

export interface FlowMetricsSprint {
  id: string;
  name: string;
  state: string;
}

export interface FlowMetricsBurndownPoint {
  date: string;
  remaining_sp: number | null;
  ideal_sp: number;
}

export interface FlowMetricsWipItem {
  status: string;
  count: number;
  limit: number | null;
  over_limit: boolean;
}

export interface FlowMetricsDeveloperItem {
  developer_label: string;
  count: number;
  limit: number;
  over_limit: boolean;
}

export interface FlowMetricsWipByDeveloper {
  limit_per_developer: number;
  developers: FlowMetricsDeveloperItem[];
  unassigned_count: number;
}

export interface FlowMetricsResponse {
  sprint: FlowMetricsSprint | null;
  burndown: FlowMetricsBurndownPoint[];
  wip: FlowMetricsWipItem[];
  wip_by_developer: FlowMetricsWipByDeveloper;
}

// Types used by existing UI components

export interface BurndownPoint {
  day: string;
  actual: number | null;
  ideal: number;
}

export interface WipItem {
  name: string;
  count: number;
  limit: number;
  tone: WipTone;
}

export interface ProjectSprint {
  id: string;
  external_sprint_id: string;
  name: string;
  state: string;
  start_date: string | null;
  end_date: string | null;
  complete_date: string | null;
  goal: string | null;
}