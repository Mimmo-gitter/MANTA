export type DeploymentSignalType =
  | "github_deployments_api"
  | "argocd_postsync_webhook"
  | "direct_deployment_api";

export type HealthProbeSourceId =
  | "generic"
  | "prometheus"
  | "datadog"
  | "pagerduty";

export type ProjectHealthStatus = "HEALTHY" | "FAILING";
export type FailureSessionStatus = "OPEN" | "RESOLVED";

export interface RepositoryItem {
  id: string;
  tenant_id: string;
  project_id: string;
  integration_id: string | null;
  external_repo_id: number;
  full_name: string;
  default_branch: string;
  is_private: boolean;
  created_at: string;
}

export interface ProjectItem {
  id: string;
  tenant_id: string;
  name: string;
  slug: string;
  description?: string | null;
  tier_sla_hours: number;
  authoritative_cd_signal: string;
  authoritative_deployment_signal: DeploymentSignalType | string | null;
  lead_time_first_commit_strategy: string;
  wip_limit_per_dev: number;
  pr_stale_threshold_hours: number;
  health_probe_enabled?: boolean;
  health_probe_source?: string;
  health_probe_url?: string | null;
  health_probe_interval_seconds?: number;
  health_status?: ProjectHealthStatus | string;
  is_active: boolean;
  created_by: string | null;
  created_at: string;
  updated_at: string;
  repositories: RepositoryItem[];
}

export interface ProjectCreatePayload {
  name: string;
  slug?: string;
  description?: string;
  tier_sla_hours?: number;
  authoritative_cd_signal?: DeploymentSignalType;
  authoritative_deployment_signal?: DeploymentSignalType;
  lead_time_first_commit_strategy?: string;
  wip_limit_per_dev?: number;
  pr_stale_threshold_hours?: number;
}

export interface ProjectUpdatePayload {
  name?: string;
  slug?: string;
  description?: string | null;
  tier_sla_hours?: number;
  wip_limit_per_dev?: number;
  pr_stale_threshold_hours?: number;
  lead_time_first_commit_strategy?: string;
  is_active?: boolean;
}

export interface ProjectIncidentItem {
  id: string;
  project_id: string;
  deployment_id: string | null;
  severity: string;
  started_at: string;
  resolved_at: string | null;
  recovery_time_seconds: number | null;
  created_at: string;
}

export interface RepositoryLinkPayload {
  repository_url?: string;
  full_name?: string;
  default_branch?: string;
  is_private?: boolean;
  integration_id?: string;
}

export interface DeploymentSignalConfig {
  project_id: string;
  tenant_id: string;
  authoritative_deployment_signal: DeploymentSignalType | string;
  updated_at: string;
}

export interface DeploymentSignalUpdatePayload {
  authoritative_deployment_signal: DeploymentSignalType;
}

export interface HealthProbeConfig {
  project_id: string;
  tenant_id: string;
  health_probe_enabled: boolean;
  health_probe_source: string;
  health_probe_url: string | null;
  health_probe_interval_seconds: number;
  health_status: ProjectHealthStatus | string;
  webhook_url: string;
  project_health_signals_url: string;
  webhook_secret_masked: string;
  webhook_secret: string | null;
  updated_at: string;
}

export interface HealthProbeConfigUpdatePayload {
  health_probe_enabled?: boolean;
  health_probe_source?: string;
  health_probe_url?: string | null;
  health_probe_interval_seconds?: number;
  health_webhook_secret?: string;
}

export interface ProductionFailureSession {
  id: string;
  tenant_id: string;
  project_id: string;
  incident_id: string | null;
  status: FailureSessionStatus | string;
  probe_source: string;
  severity: string;
  started_at: string;
  resolved_at: string | null;
  recovery_time_seconds: number | null;
  created_at: string;
  updated_at: string;
}
