
export type SprintWipStatus = "healthy" | "warning" | "critical";

export type PreviewScenario = SprintWipStatus | "upcoming";

export type WipTone = "green" | "cyan" | "blue" | "red";

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

export interface SprintWipData {
  sprint: string;
  dates: string;
  day: string;
  dayCount: string;
  status: SprintWipStatus;

  remaining: number;
  ideal: number;
  variance: string;
  blocked: number;

  completion: string;
  completionNote: string;

  recommendation: string;
  recommendationDetail: string;

  wip: WipItem[];
  chart: BurndownPoint[];
}

export interface PreviewScenarioOption {
  id: PreviewScenario;
  label: string;
}

export type SprintWipFetchResult =
  | {
      kind: "ok";
      data: SprintWipData;
    }
  | {
      kind: "unavailable";
      reason: string;
    };