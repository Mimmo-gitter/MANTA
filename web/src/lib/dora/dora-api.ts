import type { DoraFetchResult, DoraTimeframe } from "@/types/dora";

/**
 * PRODUCTION DORA ADAPTER: intentionally NOT implemented.
 *
 * The documented contract is unconfirmed and contradictory:
 *   - API reference 9.1:  GET /api/v1/dora/projects/{project_id}  ([Stub], returns no metrics)
 *   - Architecture 6:     GET /api/v1/projects/{project_id}/dora-metrics?timeframe_days=30
 *                         (aggregates only; no daily series, tiers, gauges, or nullability)
 *
 * Until the backend team confirms the endpoint and response shape
 * (docs/dora-backend-requirements.md), this adapter reports "unavailable" so the UI
 * shows an explicit "API not available" state instead of fake or empty telemetry.
 *
 * When the contract is confirmed, implement ONLY this file:
 *   1. call apiRequest<WireType>(endpoint) from "@/lib/api-client"
 *   2. map the wire response to DoraDashboardViewModel (source: "api")
 *   3. return { kind: "ok", data }
 * Do not invent endpoints or fields here.
 */
export async function fetchDoraMetricsFromApi(_params: {
  projectId: string;
  days: DoraTimeframe;
}): Promise<DoraFetchResult> {
  return {
    kind: "unavailable",
    reason:
      "The delivery metrics endpoint has not been confirmed or implemented by the backend yet.",
  };
}
