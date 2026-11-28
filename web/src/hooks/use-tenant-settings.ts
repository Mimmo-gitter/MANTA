"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  REINDEX_POLL_INTERVAL_MS,
  getTenantSettings,
  isReindexActive,
  updateTenantSettings,
} from "@/lib/tenant-settings-api";
import type {
  TenantSettingsResponse,
  TenantSettingsUpdatePayload,
} from "@/types/tenant-settings";

export const tenantSettingsQueryKeys = {
  all: ["tenant-settings"] as const,
  detail: () => ["tenant-settings", "current"] as const,
};

export function useTenantSettingsQuery(enabled = true) {
  return useQuery<TenantSettingsResponse>({
    queryKey: tenantSettingsQueryKeys.detail(),
    queryFn: getTenantSettings,
    enabled,
    staleTime: 10_000,
    retry: false,
    refetchInterval: (query) =>
      isReindexActive(query.state.data) ? REINDEX_POLL_INTERVAL_MS : false,
    refetchIntervalInBackground: false,
  });
}

export function useUpdateTenantSettingsMutation() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: TenantSettingsUpdatePayload) =>
      updateTenantSettings(payload),
    onSuccess: (updated) => {
      queryClient.setQueryData(tenantSettingsQueryKeys.detail(), updated);
    },
  });
}
