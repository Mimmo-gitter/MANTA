"use client";

/**
 * Data and timing hooks for the Delivery Analytics (DORA) dashboard:
 * real API dashboard query, deployment-signal state, and a clock.
 */

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { useDeploymentSignalQuery } from "@/hooks/use-projects";
import { fetchDoraMetricsFromApi } from "../lib/dora/dora-api";
import { readSignalType } from "@/lib/dora/dora-utils";

import type {
  DoraDashboardViewModel,
  DoraFetchResult,
  DoraSignalState,
  DoraTimeframe,
} from "../types/dora";

// ==========================================================================
// Dashboard query — real API only
// ==========================================================================

async function fetchDoraDashboard(params: {
  projectId: string;
  days: DoraTimeframe;
}): Promise<DoraFetchResult> {
  return fetchDoraMetricsFromApi({
    projectId: params.projectId,
    days: params.days,
  });
}

// ==========================================================================
// TanStack Query keys
// ==========================================================================

export const doraQueryKeys = {
  dashboard: (projectId: string, days: DoraTimeframe) =>
    ["projects", projectId, "dora", days] as const,
};

// ==========================================================================
// Dashboard hook
// ==========================================================================

export function useDoraDashboard(params: {
  projectId: string | null;
  days: DoraTimeframe;
  enabled: boolean;
}) {
  const { projectId, days, enabled } = params;

  return useQuery({
    queryKey: doraQueryKeys.dashboard(projectId ?? "", days),

    queryFn: () => {
      if (!projectId) {
        throw new Error("A project must be selected to load DORA metrics.");
      }

      return fetchDoraDashboard({
        projectId,
        days,
      });
    },

    enabled: enabled && projectId !== null,
    staleTime: 30_000,
    retry: 1,
  });
}

// ==========================================================================
// Deployment signal state
// ==========================================================================

/**
 * Resolves the deployment-signal card state using real API data.
 */
export function useDoraSignal(params: {
  projectId: string | null;
  enabled: boolean;
  dashboard: DoraDashboardViewModel | null;
}): { state: DoraSignalState; refetch: () => void } {
  const { projectId, enabled, dashboard } = params;

  const query = useDeploymentSignalQuery(
    projectId ?? undefined,
    enabled && projectId !== null,
  );

  const refetch = () => {
    void query.refetch();
  };

  if (query.isLoading) {
    return {
      state: { status: "loading" },
      refetch,
    };
  }

  if (query.isError) {
    return {
      state: {
        status: "error",
        message:
          query.error instanceof Error
            ? query.error.message
            : "Unable to load the deployment signal.",
      },
      refetch,
    };
  }

  if (!query.isSuccess) {
    return {
      state: { status: "loading" },
      refetch,
    };
  }

  const type = readSignalType(query.data);

  return {
    state: {
      status: "ready",
      signal: {
        configured: type !== null,
        type,
        sync: dashboard?.signal?.sync ?? null,
      },
    },
    refetch,
  };
}

// ==========================================================================
// Clock
// ==========================================================================

/**
 * Re-renders on an interval so relative timestamps stay fresh.
 */
export function useNow(intervalMs = 60_000): number {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()));

    return () => window.clearInterval(id);
  }, [intervalMs]);

  return now;
}