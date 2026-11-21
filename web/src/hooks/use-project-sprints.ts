"use client";

import { useQuery } from "@tanstack/react-query";

import { fetchProjectSprintsFromApi } from "@/lib/sprint-wip/sprint-wip-api";

import type { ProjectSprint } from "@/types/sprint-wip";

// Query keys
export const projectSprintsQueryKeys = {
  list: (projectId: string) =>
    ["projects", projectId, "sprints"] as const,
};

// Hook parameters
interface UseProjectSprintsParams {
  projectId: string | null;
  enabled: boolean;
}

// Project sprints query
export function useProjectSprints({
  projectId,
  enabled,
}: UseProjectSprintsParams) {
  return useQuery<ProjectSprint[], Error>({
    queryKey: projectSprintsQueryKeys.list(projectId ?? ""),

    queryFn: () => {
      if (!projectId) {
        throw new Error("Project ID is required.");
      }

      return fetchProjectSprintsFromApi({ projectId });
    },

    enabled: enabled && Boolean(projectId),

    staleTime: 60_000,

    retry: 1,
  });
}