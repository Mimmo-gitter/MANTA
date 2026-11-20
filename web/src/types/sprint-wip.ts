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
  limit: number;
  over_limit: boolean;
}

export interface FlowMetricsResponse {
  sprint: FlowMetricsSprint | null;
  burndown: FlowMetricsBurndownPoint[];
  wip: FlowMetricsWipItem[];
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