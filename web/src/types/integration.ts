export type IntegrationProvider = "github" | "jira" | "slack";

export interface Integration {
  id: string;
  provider: IntegrationProvider;
  external_account_id: string | null;
  webhook_url: string;
  is_active: boolean;
  created_at: string;
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
