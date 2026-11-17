export type DeadLetterProvider =
  | "github"
  | "jira"
  | "slack"
  | "argocd"
  | "generic"
  | string;

export interface DeadLetterItem {
  id: string;
  original_event_id: string;
  provider: DeadLetterProvider;
  error_class: string;
  stack_trace: string;
  total_attempts: number;
  payload: Record<string, unknown>;
  quarantined_at: string;
  is_resolved: boolean;
  resolved_by?: string | null;
  resolved_at?: string | null;
}

export interface DeadLetterListResponse {
  items: DeadLetterItem[];
  unresolved_count: number;
  total: number;
  limit: number;
  offset: number;
}

export interface RedriveResponse {
  id: string;
  event_id: string;
  status: "queued";
  queue: string;
  enqueued: boolean;
  resolved_at: string;
}
