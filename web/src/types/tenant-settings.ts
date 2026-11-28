export type AIProcessingMode = "local_only" | "cloud_zdr";

export type ReindexStatus =
  | "idle"
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | (string & {});

export interface TenantSettingsResponse {
  tenant_id: string;
  slug: string;
  name: string;
  allowed_domain: string | null;
  industry: string | null;
  tier: string;
  status: string;
  max_projects: number;
  ai_processing_mode: AIProcessingMode;
  retention_days: number;
  active_embedding_dimensions: number;
  active_embedding_model: string;
  active_llm_model: string;
  reindexing: boolean;
  reindex_status: ReindexStatus;
  reindexed_count: number;
  reindex_total_count: number;
  reindex_job_id: string | null;
  reindex_error: string | null;
  updated_at: string;
}

/**
 * Partial update payload for PATCH /api/v1/tenants/settings.
 * Backend enforces `extra="forbid"` and rejects explicit `null` for
 * `name`, `ai_processing_mode`, and `retention_days`.
 */
export interface TenantSettingsUpdatePayload {
  name?: string;
  allowed_domain?: string | null;
  industry?: string | null;
  ai_processing_mode?: AIProcessingMode;
  retention_days?: number;
}

export interface TenantSettingsDraft {
  name: string;
  allowed_domain: string;
  industry: string;
  ai_processing_mode: AIProcessingMode;
  retention_days_input: string;
}

export interface TenantSettingsValidationErrors {
  name?: string;
  allowed_domain?: string;
  industry?: string;
  retention_days?: string;
}
