import { apiRequest } from "@/lib/api-client";

import type {
  Integration,
  GitHubConnectRequest,
  JiraConnectRequest,
  SlackConnectRequest,
} from "@/types/integration";

const INTEGRATIONS_ENDPOINT = "/api/v1/integrations";


export function getIntegrations(): Promise<Integration[]> {
  return apiRequest<Integration[]>(INTEGRATIONS_ENDPOINT);
}

export function connectGitHub(
  data: GitHubConnectRequest,
): Promise<Integration> {
  return apiRequest<Integration>(`${INTEGRATIONS_ENDPOINT}/github/connect`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function connectJira(data: JiraConnectRequest): Promise<Integration> {
  return apiRequest<Integration>(`${INTEGRATIONS_ENDPOINT}/jira/connect`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function connectSlack(data: SlackConnectRequest): Promise<Integration> {
  return apiRequest<Integration>(`${INTEGRATIONS_ENDPOINT}/slack/connect`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function deleteIntegration(id: string): Promise<void> {
  return apiRequest<void>(
    `${INTEGRATIONS_ENDPOINT}/${encodeURIComponent(id)}`,
    {
      method: "DELETE",
    },
  );
}
