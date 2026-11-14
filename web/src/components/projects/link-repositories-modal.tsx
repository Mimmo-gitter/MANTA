"use client";

import { useEffect, useId, useMemo, useState } from "react";
import { AlertCircle, GitBranch, Loader2, Lock } from "lucide-react";
import { isApiHttpError } from "@/lib/api-client";
import { useLinkRepositoryMutation } from "@/hooks/use-projects";
import { normalizeGitHubRepoInput } from "@/lib/projects-api";
import type { Integration } from "@/types/integration";
import type { ProjectItem } from "@/types/project";
import {
  ModalCloseButton,
  ProjectModalShell,
} from "@/components/projects/project-modal-shell";
import {
  FilterDropdown,
  type FilterDropdownOption,
} from "@/components/ui/filter-dropdown";

interface LinkRepositoriesModalProps {
  open: boolean;
  onClose: () => void;
  project: ProjectItem;
  githubIntegrations: Integration[];
  selectedIntegrationId: string;
  onLinked: (linkedNames: string[]) => void;
}

export function LinkRepositoriesModal({
  open,
  onClose,
  project,
  githubIntegrations,
  selectedIntegrationId,
  onLinked,
}: LinkRepositoriesModalProps) {
  const titleId = useId();
  const descId = useId();
  const integrationSelectId = useId();
  const repoInputId = useId();
  const repoHintId = useId();
  const branchInputId = useId();

  const [integrationId, setIntegrationId] = useState(selectedIntegrationId);
  const [repoLines, setRepoLines] = useState("");
  const [defaultBranch, setDefaultBranch] = useState("main");
  const [isPrivate, setIsPrivate] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const linkMutation = useLinkRepositoryMutation(project.id);

  const integrationOptions = useMemo<FilterDropdownOption[]>(
    () =>
      githubIntegrations.map((integration) => ({
        value: integration.id,
        label: `${
          integration.external_account_id ||
          `GitHub (${integration.id.slice(0, 8)})`
        } (${integration.is_active ? "Active" : "Inactive"})`,
        dotClassName: integration.is_active ? "bg-[#00e599]" : "bg-[#f59e0b]",
      })),
    [githubIntegrations],
  );
  const selectedIntegrationOption =
    integrationOptions.find((option) => option.value === integrationId) ??
    integrationOptions[0];

  useEffect(() => {
    if (open) {
      setIntegrationId(
        selectedIntegrationId || githubIntegrations[0]?.id || "",
      );
    }
  }, [open, selectedIntegrationId, githubIntegrations]);

  const resetForm = () => {
    setRepoLines("");
    setDefaultBranch("main");
    setIsPrivate(true);
    setErrorMessage(null);
  };

  const handleClose = () => {
    if (isSubmitting) return;
    resetForm();
    onClose();
  };

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setErrorMessage(null);

    const rawEntries = repoLines
      .split(/[\n,]+/)
      .map((item) => item.trim())
      .filter(Boolean);

    if (rawEntries.length === 0) {
      setErrorMessage(
        "Enter at least one GitHub repository (e.g. owner/repo or https://github.com/owner/repo).",
      );
      return;
    }

    const normalizedRepos: Array<{ fullName: string; repositoryUrl: string }> =
      [];
    const seen = new Set<string>();
    const existingSet = new Set(
      project.repositories.map((repo) => repo.full_name.toLowerCase()),
    );

    for (const entry of rawEntries) {
      const result = normalizeGitHubRepoInput(entry);
      if (!result.ok) {
        setErrorMessage(result.error);
        return;
      }

      const key = result.fullName.toLowerCase();
      if (existingSet.has(key)) {
        setErrorMessage(
          `Repository '${result.fullName}' is already linked to this project.`,
        );
        return;
      }
      if (!seen.has(key)) {
        seen.add(key);
        normalizedRepos.push({
          fullName: result.fullName,
          repositoryUrl: result.repositoryUrl,
        });
      }
    }

    const branch = defaultBranch.trim() || "main";
    setIsSubmitting(true);
    const linkedNames: string[] = [];

    try {
      for (const repo of normalizedRepos) {
        await linkMutation.mutateAsync({
          full_name: repo.fullName,
          repository_url: repo.repositoryUrl,
          default_branch: branch,
          is_private: isPrivate,
          ...(integrationId ? { integration_id: integrationId } : {}),
        });
        linkedNames.push(repo.fullName);
      }

      resetForm();
      onClose();
      onLinked(linkedNames);
    } catch (error) {
      if (linkedNames.length > 0) {
        onLinked(linkedNames);
      }

      if (isApiHttpError(error)) {
        if (error.status === 403) {
          setErrorMessage(
            "Only Tenant Administrators can link repositories to projects.",
          );
          return;
        }
        if (error.status === 409) {
          setErrorMessage(
            error.detail ||
              "One of the specified repositories is already linked to a project.",
          );
          return;
        }
        if (error.status === 400 || error.status === 404) {
          setErrorMessage(
            error.detail ||
              "No active GitHub integration found. Connect GitHub in Administration -> Integrations first.",
          );
          return;
        }
      }

      setErrorMessage(
        error instanceof Error
          ? error.message
          : "Failed to link repository. Please try again.",
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <ProjectModalShell
      open={open}
      onClose={handleClose}
      titleId={titleId}
      descriptionId={descId}
      dismissLocked={isSubmitting}
    >
      <form onSubmit={handleSubmit} noValidate className="p-[22px]">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="inline-flex items-center gap-1.5 rounded-[4px] border border-[#00e599]/30 bg-[#00e599]/10 px-[8px] py-[2px] font-mono text-[10px] font-medium uppercase tracking-[0.6px] text-[#00e599]">
              <span
                className="size-[5px] rounded-full bg-[#00e599]"
                aria-hidden="true"
              />
              GitHub Repository Link
            </div>
            <h2
              id={titleId}
              className="mt-[10px] text-[18px] font-semibold leading-[24px] text-[#f8fafc]"
            >
              Link Repositories to {project.name}
            </h2>
            <p
              id={descId}
              className="mt-[4px] text-[12px] leading-[18px] text-[#94a3b8]"
            >
              Associate one or more GitHub repositories to ingest pull request,
              commit, and deployment webhooks.
            </p>
          </div>
          <ModalCloseButton
            onClick={handleClose}
            disabled={isSubmitting}
            label="Close Link Repositories dialog"
          />
        </div>

        <div className="mt-[18px] flex flex-col gap-[12px]">
          {githubIntegrations.length > 0 && (
            <div className="rounded-[10px] border border-[#1e293b] bg-[#0a0e18] p-[12px]">
              <label
                htmlFor={integrationSelectId}
                className="block text-[11px] font-medium text-[#cbd5e1]"
              >
                GitHub Integration
              </label>
              <FilterDropdown
                selectId={integrationSelectId}
                testId="link-repo-integration-select"
                ariaLabel="GitHub Integration"
                headerLabel="SELECT GITHUB INTEGRATION"
                value={integrationId}
                onChange={setIntegrationId}
                options={integrationOptions}
                triggerLabel={
                  selectedIntegrationOption?.label ?? "Select GitHub integration"
                }
                align="left"
                popoverWidthClassName="w-full"
                triggerClassName="mt-[6px] h-[34px] w-full bg-[#0f131d]"
              />
            </div>
          )}

          <div
            className={`rounded-[10px] border bg-[#0a0e18] p-[12px] ${
              errorMessage
                ? "border-[#ef4444]/60"
                : "border-[#1e293b] focus-within:border-[#00e599]/60"
            }`}
          >
            <label
              htmlFor={repoInputId}
              className="block text-[11px] font-medium text-[#cbd5e1]"
            >
              GitHub repository URL or slug{" "}
              <span className="text-[#ef4444]">*</span>
            </label>
            <p id={repoHintId} className="mt-[2px] text-[10px] text-[#64748b]">
              Supports https://github.com/owner/repo, git@github.com:owner/repo.git,
              or owner/repo (one per line). Non-GitHub hosts are rejected.
            </p>
            <textarea
              id={repoInputId}
              name="repositoryUrl"
              rows={3}
              required
              value={repoLines}
              onChange={(e) => {
                setRepoLines(e.target.value);
                setErrorMessage(null);
              }}
              aria-describedby={repoHintId}
              aria-invalid={Boolean(errorMessage)}
              placeholder={"https://github.com/acme-org/payment-service\nacme-org/checkout-api"}
              className="mt-[8px] w-full resize-y rounded-[6px] border border-[#1e293b] bg-[#0f131d] p-[10px] font-mono text-[12px] leading-[18px] text-[#f8fafc] placeholder:text-[#475569] focus:border-[#00e599] focus:outline-none"
              data-testid="link-repo-url-input"
            />
          </div>

          <div className="grid grid-cols-1 gap-[10px] sm:grid-cols-2">
            <div className="rounded-[10px] border border-[#1e293b] bg-[#0a0e18] p-[12px]">
              <label
                htmlFor={branchInputId}
                className="flex items-center gap-1.5 text-[11px] font-medium text-[#cbd5e1]"
              >
                <GitBranch className="size-[12px] text-[#60a5fa]" aria-hidden="true" />
                <span>Default branch</span>
              </label>
              <input
                id={branchInputId}
                type="text"
                maxLength={128}
                value={defaultBranch}
                onChange={(e) => setDefaultBranch(e.target.value)}
                placeholder="main"
                className="mt-[6px] h-[32px] w-full rounded-[6px] border border-[#1e293b] bg-[#0f131d] px-[10px] font-mono text-[12px] text-[#e2e8f0] focus:border-[#00e599] focus:outline-none"
                data-testid="link-repo-branch-input"
              />
            </div>

            <div className="flex items-center justify-between rounded-[10px] border border-[#1e293b] bg-[#0a0e18] p-[12px]">
              <label
                htmlFor={`${repoInputId}-private`}
                className="flex cursor-pointer items-center gap-2 text-[12px] text-[#cbd5e1]"
              >
                <Lock className="size-[13px] text-[#94a3b8]" aria-hidden="true" />
                <span>Private repository</span>
              </label>
              <input
                id={`${repoInputId}-private`}
                type="checkbox"
                checked={isPrivate}
                onChange={(e) => setIsPrivate(e.target.checked)}
                className="size-[16px] accent-[#00e599]"
                data-testid="link-repo-private-checkbox"
              />
            </div>
          </div>
        </div>

        {errorMessage && (
          <div
            role="alert"
            data-testid="link-repo-error"
            className="mt-[12px] flex items-start gap-[8px] rounded-[8px] border border-[#ef4444]/40 bg-[#ef4444]/10 px-[12px] py-[9px] text-[12px] text-[#fca5a5]"
          >
            <AlertCircle
              className="mt-[2px] size-[14px] shrink-0 text-[#ef4444]"
              aria-hidden="true"
            />
            <span>{errorMessage}</span>
          </div>
        )}

        <div className="mt-[18px] flex items-center justify-end gap-[8px] border-t border-[#1e293b] pt-[14px]">
          <button
            type="button"
            onClick={handleClose}
            disabled={isSubmitting}
            className="h-[34px] rounded-[8px] border border-[#252b3b] bg-[#0f131d] px-[14px] text-[12px] font-medium text-[#cbd5e1] transition-colors hover:border-[#3b455e] hover:text-[#f8fafc] disabled:cursor-not-allowed disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={isSubmitting}
            data-testid="link-repo-submit"
            className="inline-flex h-[34px] items-center gap-1.5 rounded-[8px] bg-[#00e599] px-[16px] text-[12px] font-semibold text-[#07111a] transition-colors hover:bg-[#1ef5ab] disabled:cursor-not-allowed disabled:opacity-60"
          >
            {isSubmitting ? (
              <>
                <Loader2 className="size-[14px] animate-spin" aria-hidden="true" />
                <span>Linking...</span>
              </>
            ) : (
              <span>Link Repositories</span>
            )}
          </button>
        </div>
      </form>
    </ProjectModalShell>
  );
}
