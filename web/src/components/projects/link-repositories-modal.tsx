"use client";

import { useEffect, useId, useMemo, useState } from "react";
import {
  AlertCircle,
  AlertTriangle,
  GitBranch,
  Loader2,
  Lock,
  Plus,
  Trash2,
} from "lucide-react";
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
  const [entries, setEntries] = useState<string[]>([""]);
  const [entryErrors, setEntryErrors] = useState<Record<number, string>>({});
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
    setEntries([""]);
    setEntryErrors({});
    setDefaultBranch("main");
    setIsPrivate(true);
    setErrorMessage(null);
  };

  const handleClose = () => {
    if (isSubmitting) return;
    resetForm();
    onClose();
  };

  const updateEntry = (index: number, value: string) => {
    setEntries((prev) => prev.map((item, idx) => (idx === index ? value : item)));
    setEntryErrors((prev) => {
      if (!(index in prev)) {
        setErrorMessage(null);
        return prev;
      }
      const next = { ...prev };
      delete next[index];
      if (Object.keys(next).length === 0) {
        setErrorMessage(null);
      }
      return next;
    });
  };

  const handleEntryPaste = (
    index: number,
    event: React.ClipboardEvent<HTMLInputElement>,
  ) => {
    const pasted = event.clipboardData.getData("text");
    if (!/[\r\n,]/.test(pasted)) return;

    const parts = pasted
      .split(/[\r\n,]+/)
      .map((item) => item.trim())
      .filter(Boolean);
    if (parts.length <= 1) return;

    event.preventDefault();
    setEntries((prev) => {
      const next = [...prev];
      next.splice(index, 1, ...parts);
      return next;
    });
    setEntryErrors({});
    setErrorMessage(null);
  };

  const addEntry = () => {
    setEntries((prev) => [...prev, ""]);
  };

  const removeEntry = (index: number) => {
    setEntries((prev) => (prev.length <= 1 ? prev : prev.filter((_, idx) => idx !== index)));
    setEntryErrors((prev) => {
      const next: Record<number, string> = {};
      Object.entries(prev).forEach(([key, msg]) => {
        const idx = Number(key);
        if (idx < index) next[idx] = msg;
        if (idx > index) next[idx - 1] = msg;
      });
      if (Object.keys(next).length === 0) {
        setErrorMessage(null);
      }
      return next;
    });
  };

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setErrorMessage(null);
    setEntryErrors({});

    const populated = entries
      .map((value, index) => ({ value: value.trim(), index }))
      .filter((item) => item.value.length > 0);

    if (populated.length === 0) {
      const msg =
        "Enter at least one GitHub repository (e.g. owner/repo or https://github.com/owner/repo).";
      setEntryErrors({ 0: msg });
      setErrorMessage(msg);
      return;
    }

    const normalizedRepos: Array<{ fullName: string; repositoryUrl: string }> =
      [];
    const seen = new Set<string>();
    const existingSet = new Set(
      project.repositories.map((repo) => repo.full_name.toLowerCase()),
    );
    const nextErrors: Record<number, string> = {};

    for (const { value, index } of populated) {
      const result = normalizeGitHubRepoInput(value);
      if (!result.ok) {
        nextErrors[index] = result.error;
        continue;
      }

      const key = result.fullName.toLowerCase();
      if (existingSet.has(key)) {
        nextErrors[index] =
          `Repository '${result.fullName}' is already linked to this project.`;
        continue;
      }
      if (seen.has(key)) {
        nextErrors[index] =
          `Duplicate repository '${result.fullName}' in this batch.`;
        continue;
      }
      seen.add(key);
      normalizedRepos.push({
        fullName: result.fullName,
        repositoryUrl: result.repositoryUrl,
      });
    }

    const errorEntries = Object.values(nextErrors);
    if (errorEntries.length > 0) {
      setEntryErrors(nextErrors);
      setErrorMessage(
        errorEntries.length === 1
          ? errorEntries[0]
          : `${errorEntries.length} repository entries failed validation: ${errorEntries[0]}`,
      );
      return;
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

  const issueCount = Object.keys(entryErrors).length;
  const hasIssue = issueCount > 0 || Boolean(errorMessage);
  const queuedCount = entries.filter((item) => item.trim().length > 0).length;

  return (
    <ProjectModalShell
      open={open}
      onClose={handleClose}
      titleId={titleId}
      descriptionId={descId}
      dismissLocked={isSubmitting}
      accentTone={hasIssue ? "danger" : "emerald"}
    >
      <form onSubmit={handleSubmit} noValidate className="p-[22px]">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div
              className={`inline-flex items-center gap-1.5 rounded-full border px-[10px] py-[3px] font-mono text-[10px] font-medium uppercase tracking-[0.6px] ${
                hasIssue
                  ? "border-[#ef4444]/40 bg-[#ef4444]/10 text-[#f87171]"
                  : "border-[#00e599]/30 bg-[#00e599]/10 text-[#00e599]"
              }`}
            >
              <span
                className={`size-[5px] rounded-full ${
                  hasIssue ? "bg-[#ef4444]" : "bg-[#00e599]"
                }`}
                aria-hidden="true"
              />
              {issueCount > 0
                ? `Validation Blocked · ${issueCount} Repository ${
                    issueCount === 1 ? "Issue" : "Issues"
                  }`
                : hasIssue
                  ? "Repository Link Blocked"
                  : "GitHub Repository Link"}
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

        {errorMessage && (
          <div
            role="alert"
            data-testid="link-repo-error"
            className="mt-[14px] flex items-start gap-[8px] rounded-[8px] border border-[#ef4444]/40 bg-[#ef4444]/10 px-[12px] py-[9px] text-[12px] text-[#fca5a5]"
          >
            <AlertCircle
              className="mt-[2px] size-[14px] shrink-0 text-[#ef4444]"
              aria-hidden="true"
            />
            <span>{errorMessage}</span>
          </div>
        )}

        <div className="mt-[16px] flex flex-col gap-[12px]">
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
              issueCount > 0
                ? "border-[#ef4444]/50"
                : "border-[#1e293b] focus-within:border-[#00e599]/60"
            }`}
          >
            <div className="flex items-center justify-between gap-2">
              <label
                htmlFor={repoInputId}
                className="block text-[11px] font-medium text-[#cbd5e1]"
              >
                GitHub repository URL or slug{" "}
                <span className="text-[#ef4444]">*</span>
              </label>
              <span className="font-mono text-[10px] text-[#64748b]">
                {entries.length} {entries.length === 1 ? "entry" : "entries"}
              </span>
            </div>
            <p id={repoHintId} className="mt-[2px] text-[10px] text-[#64748b]">
              Supports https://github.com/owner/repo, git@github.com:owner/repo.git,
              or owner/repo. Paste multiple lines or commas to split automatically.
            </p>

            <div className="mt-[10px] flex max-h-[210px] flex-col gap-[8px] overflow-y-auto pr-0.5">
              {entries.map((entry, index) => {
                const rowError = entryErrors[index];
                const inputId =
                  index === 0 ? repoInputId : `${repoInputId}-${index}`;
                return (
                  <div key={index} className="flex flex-col gap-[4px]">
                    <div className="flex items-center justify-between">
                      <label
                        htmlFor={inputId}
                        className="font-mono text-[10px] uppercase tracking-[0.5px] text-[#64748b]"
                      >
                        Entry {index + 1}
                      </label>
                      {entries.length > 1 && (
                        <button
                          type="button"
                          onClick={() => removeEntry(index)}
                          disabled={isSubmitting}
                          aria-label={`Remove Entry ${index + 1}`}
                          data-testid={`remove-repo-entry-${index}`}
                          className="inline-flex items-center gap-1 rounded-[4px] px-1.5 py-0.5 text-[10px] text-[#64748b] transition-colors hover:bg-[#ef4444]/10 hover:text-[#fca5a5] disabled:opacity-50"
                        >
                          <Trash2 className="size-[11px]" aria-hidden="true" />
                          <span>Remove</span>
                        </button>
                      )}
                    </div>
                    <input
                      id={inputId}
                      name={index === 0 ? "repositoryUrl" : `repositoryUrl-${index}`}
                      type="text"
                      required={index === 0}
                      value={entry}
                      onChange={(e) => updateEntry(index, e.target.value)}
                      onPaste={(e) => handleEntryPaste(index, e)}
                      aria-describedby={repoHintId}
                      aria-invalid={Boolean(rowError)}
                      placeholder={
                        index === 0
                          ? "https://github.com/acme-org/payment-service"
                          : "acme-org/checkout-api"
                      }
                      className={`h-[34px] w-full rounded-[6px] border border-solid bg-[#0f131d] px-[10px] font-mono text-[12px] text-[#f8fafc] shadow-none placeholder:text-[#475569] focus:outline-none focus:ring-0 ${
                        rowError
                          ? "border-[#ef4444]/80 focus:border-[#ef4444]"
                          : "border-[#1e293b] focus:border-[#00e599]"
                      }`}
                      data-testid={
                        index === 0
                          ? "link-repo-url-input"
                          : `link-repo-url-input-${index}`
                      }
                    />
                    {rowError && (
                      <p
                        data-testid={`link-repo-entry-error-${index}`}
                        className="flex items-center gap-1.5 text-[11px] text-[#fca5a5]"
                      >
                        <AlertTriangle
                          className="size-[12px] shrink-0 text-[#ef4444]"
                          aria-hidden="true"
                        />
                        <span>{rowError}</span>
                      </p>
                    )}
                  </div>
                );
              })}
            </div>

            <button
              type="button"
              onClick={addEntry}
              disabled={isSubmitting}
              data-testid="add-repo-entry-button"
              className="mt-[10px] inline-flex items-center gap-1.5 rounded-[6px] border border-dashed border-[#252b3b] bg-[#0f131d]/70 px-[10px] py-[6px] text-[11px] font-medium text-[#cbd5e1] transition-colors hover:border-[#00e599]/50 hover:text-[#00e599] disabled:opacity-50"
            >
              <Plus className="size-[13px]" aria-hidden="true" />
              <span>Add another repository</span>
            </button>
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
                className="mt-[6px] h-[32px] w-full rounded-[6px] border border-[#1e293b] border-solid bg-[#0f131d] px-[10px] font-mono text-[12px] text-[#e2e8f0] shadow-none focus:border-[#00e599] focus:outline-none focus:ring-0"
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
                className="size-[16px] cursor-pointer rounded-[4px] border border-[#475569] border-solid bg-[#0f131d] text-[#00e599] checked:border-[#00e599] checked:bg-[#00e599] focus:outline-none focus:ring-0 focus-visible:ring-2 focus-visible:ring-[#00e599] focus-visible:ring-offset-0"
                data-testid="link-repo-private-checkbox"
              />
            </div>
          </div>
        </div>

        <div className="mt-[18px] flex items-center justify-between gap-3 border-t border-[#1e293b] pt-[14px]">
          <div className="text-[11px]">
            {issueCount > 0 ? (
              <span className="inline-flex items-center gap-1.5 font-medium text-[#fca5a5]">
                <AlertTriangle className="size-[13px] text-[#ef4444]" aria-hidden="true" />
                <span>
                  {issueCount} validation {issueCount === 1 ? "error" : "errors"}
                </span>
              </span>
            ) : (
              <span className="font-mono text-[#64748b]">
                {queuedCount} {queuedCount === 1 ? "repository" : "repositories"} ready
              </span>
            )}
          </div>
          <div className="flex items-center gap-[8px]">
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
        </div>
      </form>
    </ProjectModalShell>
  );
}
