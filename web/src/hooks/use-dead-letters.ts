"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { listDeadLetters, redriveDeadLetter } from "@/lib/dead-letters-api";
import type { DeadLetterListResponse, RedriveResponse } from "@/types/dead-letter";

export const deadLetterQueryKeys = {
  all: ["dead-letters"] as const,
  list: (status: "unresolved" | "all") => ["dead-letters", status] as const,
};

export function useDeadLettersQuery(
  status: "unresolved" | "all" = "all",
  enabled = true,
) {
  return useQuery({
    queryKey: deadLetterQueryKeys.list(status),
    queryFn: () => listDeadLetters({ status, limit: 200, offset: 0 }),
    enabled,
    staleTime: 15_000,
    retry: false,
  });
}

export function useRedriveDeadLetterMutation() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (deadLetterId: string): Promise<RedriveResponse> => {
      const response = await redriveDeadLetter(deadLetterId);
      if (!response.enqueued) {
        throw new Error(
          "Event was marked queued in database, but the priority broker queue could not be reached.",
        );
      }
      return response;
    },
    onSuccess: (response, deadLetterId) => {
      queryClient.setQueriesData<DeadLetterListResponse>(
        { queryKey: deadLetterQueryKeys.all },
        (current) => {
          if (!current) return current;
          let decremented = false;
          const nextItems = current.items.map((item) => {
            if (item.id === deadLetterId) {
              if (!item.is_resolved) decremented = true;
              return {
                ...item,
                is_resolved: true,
                resolved_at: response.resolved_at,
              };
            }
            return item;
          });
          return {
            ...current,
            items: nextItems,
            unresolved_count: decremented
              ? Math.max(0, current.unresolved_count - 1)
              : current.unresolved_count,
          };
        },
      );
    },
  });
}
