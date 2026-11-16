
import type { SprintWipFetchResult } from "@/types/sprint-wip";

/**
 * Production Sprint & WIP API adapter.
 *
 * The endpoint is currently marked as [Stub]
 * in the MANTA Backend API Reference.
 *
 * Do not invent the response structure or display mock data
 * as real backend telemetry.
 *
 * Once the backend contract is implemented and confirmed:
 * 1. Define the backend response type.
 * 2. Call apiRequest<WireResponse>().
 * 3. Map the response to SprintWipData.
 * 4. Return { kind: "ok", data }.
 */
export async function fetchSprintWipFromApi(_params: {
  projectId: string;
}): Promise<SprintWipFetchResult> {
  return {
    kind: "unavailable",
    reason:
      "The Sprint & WIP monitoring endpoint has not been implemented by the backend yet.",
  };
}
