"use client";

import { useId, useState } from "react";
import Link from "next/link";
import { AlertCircle, ArrowRight, Loader2 } from "lucide-react";
import { isApiHttpError } from "@/lib/api-client";
import { useUnlinkRepositoryMutation } from "@/hooks/use-projects";
import type { RepositoryItem } from "@/types/project";
import {
  ModalCloseButton,
  ProjectModalShell,
} from "@/components/projects/project-modal-shell";

interface UnlinkRepositoryModalProps {
  open: boolean;
  onClose: () => void;
  projectId: string;
  projectName: string;
  repository: RepositoryItem | null;
  onUnlinked: (repoFullName: string) => void;
}

function getRepoInitials(fullName: string): string {
  const parts = fullName.split("/");
  const target = parts[parts.length - 1] || fullName;
  const words = target.split(/[-_.]+/).filter(Boolean);
  if (words.length >= 2) {
    return `${words[0][0]}${words[1][0]}`.toUpperCase();
  }
  return target.slice(0, 2).toUpperCase();
}

export function UnlinkRepositoryModal({
  open,
  onClose,
  projectId,
  projectName,
  repository,
  onUnlinked,
}: UnlinkRepositoryModalProps) {
  const titleId = useId();
  const descId = useId();
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const unlinkMutation = useUnlinkRepositoryMutation(projectId);

  const handleClose = () => {
    if (unlinkMutation.isPending) return;
    setErrorMessage(null);
    onClose();
  };

  const handleConfirmUnlink = async () => {
    if (!repository) return;
    setErrorMessage(null);
    try {
      await unlinkMutation.mutateAsync(repository.id);
      onUnlinked(repository.full_name);
      onClose();
    } catch (error) {
      if (isApiHttpError(error) && error.status === 403) {
        setErrorMessage(
          "Only Tenant Administrators can unlink repositories from projects.",
        );
        return;
      }
      setErrorMessage(
        error instanceof Error
          ? error.message
          : "Failed to unlink repository. Please try again.",
      );
    }
  };

  return (
    <ProjectModalShell
      open={open && Boolean(repository)}
      onClose={handleClose}
      titleId={titleId}
      descriptionId={descId}
      dismissLocked={unlinkMutation.isPending}
      accentTone="danger"
      maxWidthClassName="max-w-[500px]"
    >
      {repository && (
        <div className="p-[22px]">
          <div className="flex items-start justify-between gap-4">
            <div>
              <div className="inline-flex items-center gap-1.5 rounded-[4px] border border-[#ef4444]/40 bg-[#ef4444]/10 px-[8px] py-[2px] font-mono text-[10px] font-medium uppercase tracking-[0.6px] text-[#fca5a5]">
                <span
                  className="size-[5px] rounded-full bg-[#ef4444]"
                  aria-hidden="true"
                />
                Unlink Repository
              </div>
              <h2
                id={titleId}
                className="mt-[10px] text-[18px] font-semibold leading-[24px] text-[#f8fafc]"
              >
                Unlink Repository from Project?
              </h2>
              <p
                id={descId}
                className="mt-[4px] text-[12px] leading-[18px] text-[#94a3b8]"
              >
                Disconnect {repository.full_name} from {projectName} telemetry.
              </p>
            </div>
            <ModalCloseButton
              onClick={handleClose}
              disabled={unlinkMutation.isPending}
              label="Close Unlink Repository dialog"
            />
          </div>

          {/* Repository card */}
          <div className="mt-[16px] flex items-center justify-between gap-3 rounded-[10px] border border-[#ef4444]/35 bg-[#191522] p-[12px]">
            <div className="flex min-w-0 items-center gap-[10px]">
              <div className="flex size-[34px] shrink-0 items-center justify-center rounded-full border border-[#334155] bg-[#1e293b] font-mono text-[11px] font-semibold text-[#e2e8f0]">
                {getRepoInitials(repository.full_name)}
              </div>
              <div className="min-w-0">
                <div className="truncate font-mono text-[12px] font-medium text-[#f8fafc]">
                  {repository.full_name}
                </div>
                <div className="truncate font-mono text-[11px] text-[#94a3b8]">
                  github.com/{repository.full_name} · branch:{" "}
                  {repository.default_branch}
                </div>
              </div>
            </div>
            <span className="shrink-0 rounded-[4px] border border-[#334155] bg-[#0f131d] px-[8px] py-[3px] font-mono text-[10px] text-[#cbd5e1]">
              DORA Active
            </span>
          </div>

          {/* Impact details */}
          <div className="mt-[12px] rounded-[10px] border border-[#1e293b] bg-[#0a0e18] p-[12px] text-[11px] leading-[18px] text-[#cbd5e1]">
            <ul className="space-y-1">
              <li>
                • Automated GitHub webhook ingestion for commits, PRs, and
                deployments will stop.
              </li>
              <li className="text-[#94a3b8]">
                • Historical DORA metrics and sanitized ADR records remain
                preserved in this project.
              </li>
              <li className="flex flex-wrap items-center gap-1 text-[#94a3b8]">
                <span>• Need to rotate GitHub App webhook secrets?</span>
                <Link
                  href="/integrations"
                  onClick={handleClose}
                  className="inline-flex items-center gap-0.5 font-medium text-[#00e599] hover:underline"
                >
                  <span>Open Administration → Integrations</span>
                  <ArrowRight className="size-[11px]" aria-hidden="true" />
                </Link>
              </li>
            </ul>
          </div>

          {errorMessage && (
            <div
              role="alert"
              data-testid="unlink-repository-error"
              className="mt-[12px] flex items-start gap-[8px] rounded-[8px] border border-[#ef4444]/40 bg-[#ef4444]/10 px-[12px] py-[8px] text-[12px] text-[#fca5a5]"
            >
              <AlertCircle
                className="mt-[2px] size-[14px] shrink-0 text-[#ef4444]"
                aria-hidden="true"
              />
              <span>{errorMessage}</span>
            </div>
          )}

          {/* Footer */}
          <div className="mt-[18px] flex flex-wrap items-center justify-between gap-3 border-t border-[#1e293b] pt-[14px]">
            <span className="font-mono text-[11px] text-[#f87171]">
              Historical metrics will be preserved
            </span>

            <div className="flex items-center gap-[8px]">
              <button
                type="button"
                onClick={handleClose}
                disabled={unlinkMutation.isPending}
                className="h-[34px] rounded-[8px] border border-[#252b3b] bg-[#0f131d] px-[14px] text-[12px] font-medium text-[#cbd5e1] transition-colors hover:border-[#3b455e] hover:text-[#f8fafc] disabled:cursor-not-allowed disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleConfirmUnlink}
                disabled={unlinkMutation.isPending}
                data-testid="confirm-unlink-repository-button"
                className="inline-flex h-[34px] items-center gap-1.5 rounded-[8px] bg-[#ef4444] px-[14px] text-[12px] font-semibold text-white transition-colors hover:bg-[#dc2626] disabled:cursor-not-allowed disabled:opacity-60"
              >
                {unlinkMutation.isPending ? (
                  <>
                    <Loader2 className="size-[14px] animate-spin" aria-hidden="true" />
                    <span>Unlinking...</span>
                  </>
                ) : (
                  <span>Unlink Repository</span>
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </ProjectModalShell>
  );
}
