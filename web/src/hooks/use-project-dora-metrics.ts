"use client";

import {
  useQuery,
  type QueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";

import { fetchDoraMetricsFromApi } from "@/lib/dora/dora-api";

import type {
  DoraFetchResult,
  DoraTimeframe,
} from "@/types/dora";

/** Supported DORA reporting windows. */
export type DoraMetricsWindow = "14d" | "30d";

function windowToDays(window: DoraMetricsWindow): DoraTimeframe {
  return Number(window.slice(0, -1)) as DoraTimeframe;
}

/**
 * Centralized query keys:
 * each project and reporting window has its own cache entry.
 */
export const projectDoraMetricsKeys = {
  all: ["dora-metrics"] as const,

  project: (projectId: string) =>
    [...projectDoraMetricsKeys.all, projectId] as const,

  detail: (projectId: string | null, window: DoraMetricsWindow) =>
    [...projectDoraMetricsKeys.all, projectId, window] as const,
};

/**
 * Invalidate all cached DORA windows, or only one project's windows.
 * Call this after a project configuration change that affects DORA metrics.
 */
export function invalidateProjectDoraMetrics(
  queryClient: QueryClient,
  projectId?: string,
): Promise<void> {
  return queryClient.invalidateQueries({
    queryKey: projectId
      ? projectDoraMetricsKeys.project(projectId)
      : projectDoraMetricsKeys.all,
  });
}

/**
 * Fetch DORA metrics for a selected project and reporting window.
 */
export function useProjectDoraMetrics(
  projectId: string | null,
  window: DoraMetricsWindow,
  enabled = true,
): UseQueryResult<DoraFetchResult, Error> {
  return useQuery<DoraFetchResult, Error>({
    queryKey: projectDoraMetricsKeys.detail(projectId, window),

    queryFn: () => {
      if (!projectId) {
        throw new Error("A project must be selected to load DORA metrics.");
      }

      return fetchDoraMetricsFromApi({
        projectId,
        days: windowToDays(window),
      });
    },

    enabled: enabled && Boolean(projectId),

    // Reuse recently fetched data when switching back to a project/window.
    staleTime: 30_000,
    gcTime: 5 * 60_000,

    retry: 1,
    refetchOnWindowFocus: true,
  });
}