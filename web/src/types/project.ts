export type DeploymentSignalType =
  | "github_deployments_api"
  | "argocd_postsync_webhook"
  | "direct_deployment_api";

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
  tier_sla_hours: number;
  authoritative_cd_signal: string;
  authoritative_deployment_signal: DeploymentSignalType | string | null;
  lead_time_first_commit_strategy: string;
  wip_limit_per_dev: number;
  pr_stale_threshold_hours: number;
  is_active: boolean;
  created_by: string | null;
  created_at: string;
  updated_at: string;
  repositories: RepositoryItem[];
}

export interface ProjectCreatePayload {
  name: string;
  slug?: string;
  tier_sla_hours?: number;
  authoritative_cd_signal?: DeploymentSignalType;
  authoritative_deployment_signal?: DeploymentSignalType;
  lead_time_first_commit_strategy?: string;
  wip_limit_per_dev?: number;
  pr_stale_threshold_hours?: number;
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

export interface ProjectUiMeta {
  description?: string;
}
