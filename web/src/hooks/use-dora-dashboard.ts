"use client";

/**
 * Data and timing hooks for the Delivery Analytics (DORA) dashboard:
 * dashboard query (mock/real source switch), deployment-signal state, and a clock.
 */
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useDeploymentSignalQuery } from "@/hooks/use-projects";
import { fetchDoraMetricsFromApi } from "../lib/dora/dora-api";
import { readSignalType } from "@/lib/dora/dora-utils";
import type {
  DoraDashboardViewModel,
  DoraFetchResult,
  DoraMockScenario,
  DoraSignalState,
  DoraTimeframe,
} from "../types/dora";

// ==========================================================================
// Source switch: mock (development only) or the production adapter
// ==========================================================================

async function fetchDoraDashboard(params: {
  projectId: string;
  days: DoraTimeframe;
  mockScenario: DoraMockScenario | null;
}): Promise<DoraFetchResult> {
  if (params.mockScenario !== null) {
    // The dynamic import sits inside a NODE_ENV guard so production bundles drop it.
    if (process.env.NODE_ENV !== "production") {
      const { getMockDoraResult } =
        await import("../lib/dora/mocks/dora-mock-data");
      return getMockDoraResult({
        projectId: params.projectId,
        days: params.days,
        scenario: params.mockScenario,
      });
    }
    throw new Error("Mock DORA data is not available in production builds.");
  }

  return fetchDoraMetricsFromApi({
    projectId: params.projectId,
    days: params.days,
  });
}

// ==========================================================================
// Dashboard query
// ==========================================================================

export const doraQueryKeys = {
  // Nested under ["projects", id] so project invalidations also refresh DORA data.
  dashboard: (
    projectId: string,
    days: DoraTimeframe,
    mockScenario: DoraMockScenario | null,
  ) => ["projects", projectId, "dora", days, mockScenario ?? "api"] as const,
};

export function useDoraDashboard(params: {
  projectId: string | null;
  days: DoraTimeframe;
  enabled: boolean;
  mockScenario: DoraMockScenario | null;
}) {
  const { projectId, days, enabled, mockScenario } = params;
  return useQuery({
    queryKey: doraQueryKeys.dashboard(projectId ?? "", days, mockScenario),
    queryFn: () =>
      fetchDoraDashboard({ projectId: projectId!, days, mockScenario }),
    enabled: enabled && projectId !== null,
    staleTime: 30_000,
    retry: 1,
  });
}

// ==========================================================================
// Deployment signal state
// ==========================================================================

/**
 * Resolves the deployment-signal card state.
 *  - Mock mode: taken from the mock view model; the real API is never called.
 *  - Otherwise: the real, existing deployment-signal query decides "configured",
 *    and optional sync metadata comes from the DORA view model if the backend reports it.
 */
export function useDoraSignal(params: {
  projectId: string | null;
  enabled: boolean;
  mockActive: boolean;
  dashboard: DoraDashboardViewModel | null;
}): { state: DoraSignalState; refetch: () => void } {
  const { projectId, enabled, mockActive, dashboard } = params;
  const query = useDeploymentSignalQuery(
    projectId ?? undefined,
    enabled && !mockActive,
  );

  if (mockActive) {
    if (!dashboard) return { state: { status: "loading" }, refetch: () => {} };
    return {
      state: {
        status: "ready",
        signal: dashboard.signal ?? {
          configured: false,
          type: null,
          sync: null,
        },
      },
      refetch: () => {},
    };
  }

  const refetch = () => {
    void query.refetch();
  };

  if (query.isLoading) return { state: { status: "loading" }, refetch };
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
  if (!query.isSuccess) return { state: { status: "loading" }, refetch };

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

/** Re-renders on an interval so relative timestamps stay fresh. */
export function useNow(intervalMs = 60_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(id);
  }, [intervalMs]);
  return now;
}
