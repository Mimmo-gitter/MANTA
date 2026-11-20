"use client";

import { useQuery } from "@tanstack/react-query";

import { fetchSprintWipFromApi } from "@/lib/sprint-wip/sprint-wip-api";

import type { FlowMetricsResponse } from "@/types/sprint-wip";

// Query keys

export const sprintWipQueryKeys = {
  dashboard: (projectId: string, sprintId: string | null) =>
    [
      "projects",
      projectId,
      "flow-metrics",
      sprintId ?? "active",
    ] as const,
};

// Sprint & WIP query

interface UseSprintWipParams {
  projectId: string | null;
  sprintId: string | null;
  enabled: boolean;
}

export function useSprintWip({
  projectId,
  sprintId,
  enabled,
}: UseSprintWipParams) {
  return useQuery<FlowMetricsResponse, Error>({
    queryKey: sprintWipQueryKeys.dashboard(
      projectId ?? "",
      sprintId,
    ),

    queryFn: () => {
      if (!projectId) {
        throw new Error("Project ID is required.");
      }

      return fetchSprintWipFromApi({
        projectId,
        sprintId,
      });
    },

    enabled: enabled && Boolean(projectId),

    staleTime: 30_000,

    retry: 1,
  });
}