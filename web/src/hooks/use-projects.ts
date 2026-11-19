"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createProject,
  deleteProject,
  getDeploymentSignal,
  getProject,
  getProjectHealthConfig,
  linkRepository,
  listProjectHealthSignals,
  listProjectIncidents,
  listProjects,
  rotateProjectHealthSecret,
  unlinkRepository,
  updateDeploymentSignal,
  updateProject,
  updateProjectHealthConfig,
} from "@/lib/projects-api";
import type {
  DeploymentSignalUpdatePayload,
  HealthProbeConfigUpdatePayload,
  ProjectCreatePayload,
  ProjectUpdatePayload,
  RepositoryLinkPayload,
} from "@/types/project";

export const projectQueryKeys = {
  all: ["projects"] as const,
  detail: (projectId: string) => ["projects", projectId] as const,
  deploymentSignal: (projectId: string) =>
    ["projects", projectId, "deployment-signal"] as const,
  incidents: (projectId: string) =>
    ["projects", projectId, "incidents"] as const,
  healthConfig: (projectId: string) =>
    ["projects", projectId, "health-config"] as const,
  healthSignals: (projectId: string) =>
    ["projects", projectId, "health-signals"] as const,
};

export function useProjectsQuery(enabled = true) {
  return useQuery({
    queryKey: projectQueryKeys.all,
    queryFn: listProjects,
    enabled,
    staleTime: 30_000,
    retry: 1,
  });
}

export function useProjectQuery(projectId: string | undefined, enabled = true) {
  return useQuery({
    queryKey: projectQueryKeys.detail(projectId ?? ""),
    queryFn: () => getProject(projectId!),
    enabled: Boolean(projectId) && enabled,
    staleTime: 30_000,
    retry: 1,
  });
}

export function useDeploymentSignalQuery(
  projectId: string | undefined,
  enabled = true,
) {
  return useQuery({
    queryKey: projectQueryKeys.deploymentSignal(projectId ?? ""),
    queryFn: () => getDeploymentSignal(projectId!),
    enabled: Boolean(projectId) && enabled,
    staleTime: 30_000,
    retry: 1,
  });
}

export function useCreateProjectMutation() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: ProjectCreatePayload) => createProject(payload),
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey: projectQueryKeys.all });
      queryClient.setQueryData(projectQueryKeys.detail(created.id), created);
    },
  });
}

export function useLinkRepositoryMutation(projectId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: RepositoryLinkPayload) =>
      linkRepository(projectId, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: projectQueryKeys.all });
      queryClient.invalidateQueries({
        queryKey: projectQueryKeys.detail(projectId),
      });
    },
  });
}

export function useUnlinkRepositoryMutation(projectId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (repoId: string) => unlinkRepository(projectId, repoId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: projectQueryKeys.all });
      queryClient.invalidateQueries({
        queryKey: projectQueryKeys.detail(projectId),
      });
    },
  });
}

export function useUpdateDeploymentSignalMutation(projectId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: DeploymentSignalUpdatePayload) =>
      updateDeploymentSignal(projectId, payload),
    onSuccess: (updated) => {
      queryClient.setQueryData(
        projectQueryKeys.deploymentSignal(projectId),
        updated,
      );
      queryClient.invalidateQueries({ queryKey: projectQueryKeys.all });
      queryClient.invalidateQueries({
        queryKey: projectQueryKeys.detail(projectId),
      });
    },
  });
}

export function useUpdateProjectMutation(projectId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: ProjectUpdatePayload) =>
      updateProject(projectId, payload),
    onSuccess: (updated) => {
      queryClient.setQueryData(projectQueryKeys.detail(projectId), updated);
      queryClient.invalidateQueries({ queryKey: projectQueryKeys.all });
      queryClient.invalidateQueries({
        queryKey: projectQueryKeys.detail(projectId),
      });
    },
  });
}

export function useDeleteProjectMutation() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (projectId: string) => deleteProject(projectId),
    onSuccess: (_data, projectId) => {
      queryClient.invalidateQueries({ queryKey: projectQueryKeys.all });
      queryClient.invalidateQueries({
        queryKey: projectQueryKeys.detail(projectId),
      });
    },
  });
}

export function useProjectIncidentsQuery(
  projectId: string | undefined,
  enabled = true,
) {
  return useQuery({
    queryKey: projectQueryKeys.incidents(projectId ?? ""),
    queryFn: () => listProjectIncidents(projectId!),
    enabled: Boolean(projectId) && enabled,
    staleTime: 30_000,
    retry: 1,
  });
}

export function useProjectHealthConfigQuery(
  projectId: string | undefined,
  enabled = true,
  refetchInterval: number | false = false,
) {
  return useQuery({
    queryKey: projectQueryKeys.healthConfig(projectId ?? ""),
    queryFn: () => getProjectHealthConfig(projectId!),
    enabled: Boolean(projectId) && enabled,
    staleTime: 10_000,
    retry: 1,
    refetchInterval,
    refetchIntervalInBackground: false,
  });
}

export function useProjectHealthSignalsQuery(
  projectId: string | undefined,
  enabled = true,
  refetchInterval: number | false = false,
) {
  return useQuery({
    queryKey: projectQueryKeys.healthSignals(projectId ?? ""),
    queryFn: () => listProjectHealthSignals(projectId!),
    enabled: Boolean(projectId) && enabled,
    staleTime: 10_000,
    retry: 1,
    refetchInterval,
    refetchIntervalInBackground: false,
  });
}

export function useUpdateProjectHealthConfigMutation(projectId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: HealthProbeConfigUpdatePayload) =>
      updateProjectHealthConfig(projectId, payload),
    onSuccess: (updated) => {
      queryClient.setQueryData(
        projectQueryKeys.healthConfig(projectId),
        updated,
      );
      queryClient.invalidateQueries({ queryKey: projectQueryKeys.all });
      queryClient.invalidateQueries({
        queryKey: projectQueryKeys.detail(projectId),
      });
    },
  });
}

export function useRotateProjectHealthSecretMutation(projectId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: () => rotateProjectHealthSecret(projectId),
    onSuccess: (updated) => {
      queryClient.setQueryData(
        projectQueryKeys.healthConfig(projectId),
        updated,
      );
      queryClient.invalidateQueries({ queryKey: projectQueryKeys.all });
      queryClient.invalidateQueries({
        queryKey: projectQueryKeys.detail(projectId),
      });
    },
  });
}
