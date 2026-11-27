import { getCurrentUser } from "@/lib/auth";
import { getApiBaseUrl } from "@/lib/runtime-config";

import type {
  FlowMetricsResponse,
  ProjectSprint,
} from "@/types/sprint-wip";

interface FetchSprintWipParams {
  projectId: string;
  sprintId: string | null;
}

export async function fetchSprintWipFromApi({
  projectId,
  sprintId,
}: FetchSprintWipParams): Promise<FlowMetricsResponse> {
  const user = await getCurrentUser();

  if (!user?.access_token) {
    throw new Error("You must be authenticated to load sprint metrics.");
  }

  const params = new URLSearchParams();

  if (sprintId) {
    params.set("sprint_id", sprintId);
  }

  const query = params.toString();

  const url =
    `${getApiBaseUrl()}/api/v1/projects/${encodeURIComponent(projectId)}/flow-metrics` +
    (query ? `?${query}` : "");

  const response = await fetch(url, {
    method: "GET",
    headers: {
      Authorization: `Bearer ${user.access_token}`,
      Accept: "application/json",
    },
    cache: "no-store",
  });

  if (!response.ok) {
    let message = `Failed to load sprint metrics (${response.status}).`;

    try {
      const errorBody: { detail?: unknown } = await response.json();

      if (typeof errorBody.detail === "string") {
        message = errorBody.detail;
      }
    } catch {
      // Keep the default HTTP error message.
    }

    throw new Error(message);
  }

  return (await response.json()) as FlowMetricsResponse;
}

interface FetchProjectSprintsParams {
  projectId: string;
}

export async function fetchProjectSprintsFromApi({
  projectId,
}: FetchProjectSprintsParams): Promise<ProjectSprint[]> {
  const user = await getCurrentUser();

  if (!user?.access_token) {
    throw new Error("You must be authenticated to load project sprints.");
  }

  const url =
    `${getApiBaseUrl()}/api/v1/projects/${encodeURIComponent(projectId)}/sprints`;

  const response = await fetch(url, {
    method: "GET",
    headers: {
      Authorization: `Bearer ${user.access_token}`,
      Accept: "application/json",
    },
    cache: "no-store",
  });

  if (!response.ok) {
    let message = `Failed to load project sprints (${response.status}).`;

    try {
      const errorBody: { detail?: unknown } = await response.json();

      if (typeof errorBody.detail === "string") {
        message = errorBody.detail;
      }
    } catch {
      // Keep the default HTTP error message.
    }

    throw new Error(message);
  }

  return (await response.json()) as ProjectSprint[];
}