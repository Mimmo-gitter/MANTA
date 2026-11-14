export type IntegrationProvider = "github" | "jira" | "slack";
export type IntegrationStatus = "connected" | "needs_reconnect";

export interface Integration {
  id: string;
  tenant_id?: string;
  provider: IntegrationProvider;
  external_account_id: string | null;
  webhook_url: string;
  webhook_secret?: string | null;
  webhook_secret_masked?: string | null;
  is_active: boolean;
  status?: IntegrationStatus;
  created_at: string;
  updated_at?: string;
}

export interface GitHubConnectRequest {
  access_token: string;
  webhook_secret?: string;
}

export interface JiraConnectRequest {
  base_url: string;
  email: string;
  api_token: string;
  webhook_secret?: string;
}

export interface SlackConnectRequest {
  bot_token: string;
  signing_secret: string;
}
