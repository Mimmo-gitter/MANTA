import type {
  FlowMetricsResponse,
  SprintWipStatus,
  WipItem,
  WipTone,
} from "@/types/sprint-wip";

/**
 * Calculates estimated WIP limits for workflow stages.
 * Uses backend limits when available and estimates missing ones.
 */
export function buildWipItems(data: FlowMetricsResponse): WipItem[] {
  const developerLimit = Math.max(
    1,
    data.wip_by_developer?.limit_per_developer ?? 1,
  );

  // Only developers with unfinished assigned issues are counted.
  const activeDevelopers = Math.max(
    1,
    data.wip_by_developer?.developers?.length ?? 0,
  );

  const teamCapacity = developerLimit * activeDevelopers;

  return data.wip.map((item) => {
    const normalizedStatus = normalizeStatus(item.status);

    // Prefer a real configured backend limit if one exists.
    const limit =
      typeof item.limit === "number" && item.limit > 0
        ? item.limit
        : estimateStatusLimit(normalizedStatus, teamCapacity);

    const overLimit = item.count > limit;

    let tone: WipTone = "blue";

    if (overLimit || normalizedStatus.includes("blocked")) {
      tone = "red";
    } else if (
      normalizedStatus.includes("progress") ||
      normalizedStatus.includes("development")
    ) {
      tone = "cyan";
    } else if (
      normalizedStatus.includes("done") ||
      normalizedStatus.includes("complete")
    ) {
      tone = "green";
    }

    return {
      name: item.status,
      count: item.count,
      limit,
      tone,
    };
  });
}

function normalizeStatus(status: string): string {
  return status
    .trim()
    .toLowerCase()
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ");
}

function estimateStatusLimit(
  status: string,
  teamCapacity: number,
): number {
  // Review and QA stages have half of the team's capacity.
  if (/\b(review|qa|testing|test)\b/.test(status)) {
    return Math.max(1, Math.ceil(teamCapacity / 2));
  }

  // Backlog stages can hold a larger queue.
  if (/\b(to do|todo|backlog|open|ready|selected)\b/.test(status)) {
    return teamCapacity * 2;
  }

  // Other workflow stages use the standard team capacity.
  return teamCapacity;
}

/**
 * Determines the overall WIP health from calculated limits.
 */
export function getWipStatus(items: WipItem[]): SprintWipStatus {
  const hasExceededLimit = items.some(
    (item) => item.count > item.limit,
  );

  if (hasExceededLimit) {
    return "critical";
  }

  const hasReachedLimit = items.some(
    (item) => item.count >= item.limit,
  );

  return hasReachedLimit ? "warning" : "healthy";
}