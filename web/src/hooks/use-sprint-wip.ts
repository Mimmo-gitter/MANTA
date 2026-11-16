
"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchSprintWipFromApi } from "@/lib/sprint-wip/sprint-wip-api";

import type {
  PreviewScenario,
  SprintWipFetchResult,
} from "@/types/sprint-wip";

// ==========================================================================
// Source switch: mock (development only) or production API
// ==========================================================================

async function fetchSprintWip(params: {
  projectId: string;
  mockScenario: PreviewScenario | null;
}): Promise<SprintWipFetchResult> {
  const { projectId, mockScenario } = params;

  if (mockScenario !== null) {
    if (process.env.NODE_ENV !== "production") {
      const {
        SPRINT_WIP_MOCK_DATA,
      } = await import(
        "@/lib/sprint-wip/mocks/sprint-wip-mock-data"
      );

      if (mockScenario === "upcoming") {
        return {
          kind: "unavailable",
          reason: "There is no active sprint for this project.",
        };
      }

      return {
        kind: "ok",
        data: SPRINT_WIP_MOCK_DATA[mockScenario],
      };
    }

    throw new Error(
      "Sprint & WIP mock data is not available in production.",
    );
  }

  return fetchSprintWipFromApi({ projectId });
}

// ==========================================================================
// Query keys
// ==========================================================================

export const sprintWipQueryKeys = {
  dashboard: (
    projectId: string,
    mockScenario: PreviewScenario | null,
  ) =>
    [
      "projects",
      projectId,
      "sprint-wip",
      mockScenario ?? "api",
    ] as const,
};

// ==========================================================================
// Sprint & WIP dashboard query
// ==========================================================================

export function useSprintWip(params: {
  projectId: string | null;
  enabled: boolean;
  mockScenario: PreviewScenario | null;
}) {
  const { projectId, enabled, mockScenario } = params;

  return useQuery<SprintWipFetchResult, Error>({
    queryKey: sprintWipQueryKeys.dashboard(
      projectId ?? "",
      mockScenario,
    ),

    queryFn: () => {
      if (!projectId) {
        throw new Error("Project ID is required.");
      }

      return fetchSprintWip({
        projectId,
        mockScenario,
      });
    },

    enabled: enabled && projectId !== null,
    staleTime: 30_000,
    retry: mockScenario === null ? 1 : false,
  });
}
