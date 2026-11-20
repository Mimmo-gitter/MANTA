"use client";

import { useId, useState } from "react";
import { AlertCircle, Archive, Loader2 } from "lucide-react";
import { isApiHttpError } from "@/lib/api-client";
import { useDeleteProjectMutation } from "@/hooks/use-projects";
import type { ProjectItem } from "@/types/project";
import {
  ModalCloseButton,
  ProjectModalShell,
} from "@/components/projects/project-modal-shell";

interface ArchiveProjectModalProps {
  open: boolean;
  onClose: () => void;
  project: ProjectItem | null;
  onArchived?: (project: ProjectItem) => void;
}

function getProjectInitials(name: string): string {
  const words = name.trim().split(/[\s-_]+/).filter(Boolean);
  if (words.length >= 2) {
    return `${words[0][0]}${words[1][0]}`.toUpperCase();
  }
  return (words[0] ?? "PR").slice(0, 2).toUpperCase();
}

export function ArchiveProjectModal({
  open,
  onClose,
  project,
  onArchived,
}: ArchiveProjectModalProps) {
  const titleId = useId();
  const descId = useId();
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const deleteProjectMutation = useDeleteProjectMutation();

  const handleClose = () => {
    if (deleteProjectMutation.isPending) return;
    setErrorMessage(null);
    onClose();
  };

  const handleConfirmArchive = async () => {
    if (!project) return;
    setErrorMessage(null);
    try {
      await deleteProjectMutation.mutateAsync(project.id);
      onArchived?.(project);
      onClose();
    } catch (error) {
      if (isApiHttpError(error) && error.status === 403) {
        setErrorMessage(
          "Only Tenant Administrators can archive workspace projects.",
        );
        return;
      }
      setErrorMessage(
        error instanceof Error
          ? error.message
          : "Failed to archive project. Please try again.",
      );
    }
  };

  const repoCount = project?.repositories?.length ?? 0;

  return (
    <ProjectModalShell
      open={open && Boolean(project)}
      onClose={handleClose}
      titleId={titleId}
      descriptionId={descId}
      dismissLocked={deleteProjectMutation.isPending}
      accentTone="danger"
      maxWidthClassName="max-w-[500px]"
    >
      {project && (
        <div className="p-[22px]" data-testid="archive-project-modal">
          <div className="flex items-start justify-between gap-4">
            <div>
              <div className="inline-flex items-center gap-1.5 rounded-full border border-[#ef4444]/40 bg-[#ef4444]/10 px-[10px] py-[3px] font-mono text-[10px] font-medium uppercase tracking-[0.6px] text-[#fca5a5]">
                <span
                  className="size-[5px] rounded-full bg-[#ef4444]"
                  aria-hidden="true"
                />
                Archive Project Workspace
              </div>
              <h2
                id={titleId}
                className="mt-[10px] text-[18px] font-semibold leading-[24px] text-[#f8fafc]"
              >
                Archive &ldquo;{project.name}&rdquo;?
              </h2>
              <p
                id={descId}
                className="mt-[4px] text-[12px] leading-[18px] text-[#94a3b8]"
              >
                Deactivate this workspace project and pause active repository telemetry.
              </p>
            </div>
            <ModalCloseButton
              onClick={handleClose}
              disabled={deleteProjectMutation.isPending}
              label="Close Archive Project dialog"
            />
          </div>

          <div className="mt-[16px] flex items-center justify-between gap-3 rounded-[10px] border border-[#ef4444]/35 bg-[#191522] p-[12px]">
            <div className="flex min-w-0 items-center gap-[10px]">
              <div className="flex size-[34px] shrink-0 items-center justify-center rounded-[8px] border border-[#ef4444]/35 bg-[#ef4444]/10 font-mono text-[11px] font-semibold text-[#fca5a5]">
                {getProjectInitials(project.name)}
              </div>
              <div className="min-w-0">
                <div className="truncate text-[13px] font-semibold text-[#f8fafc]">
                  {project.name}
                </div>
                <div className="truncate font-mono text-[11px] text-[#94a3b8]">
                  /projects/{project.slug}
                </div>
              </div>
            </div>
            <span className="shrink-0 rounded-[4px] border border-[#334155] bg-[#0f131d] px-[8px] py-[3px] font-mono text-[10px] text-[#cbd5e1]">
              {repoCount} {repoCount === 1 ? "repo" : "repos"}
            </span>
          </div>

          <div className="mt-[12px] rounded-[10px] border border-[#1e293b] bg-[#0a0e18] p-[12px] text-[11px] leading-[18px] text-[#cbd5e1]">
            <ul className="space-y-1">
              <li>
                • Project status will switch to <span className="text-[#f8fafc]">Inactive</span> and webhook ingestion for this project will pause.
              </li>
              <li className="text-[#94a3b8]">
                • Historical DORA metrics, incident timelines, and ADR records remain preserved for audit compliance.
              </li>
            </ul>
          </div>

          {errorMessage && (
            <div
              role="alert"
              data-testid="archive-project-error"
              className="mt-[12px] flex items-start gap-[8px] rounded-[8px] border border-[#ef4444]/40 bg-[#ef4444]/10 px-[12px] py-[8px] text-[12px] text-[#fca5a5]"
            >
              <AlertCircle
                className="mt-[2px] size-[14px] shrink-0 text-[#ef4444]"
                aria-hidden="true"
              />
              <span>{errorMessage}</span>
            </div>
          )}

          <div className="mt-[18px] flex flex-wrap items-center justify-between gap-3 border-t border-[#1e293b] pt-[14px]">
            <span className="font-mono text-[11px] text-[#f87171]">
              Requires Tenant Administrator action
            </span>

            <div className="flex items-center gap-[8px]">
              <button
                type="button"
                data-testid="archive-project-cancel-button"
                onClick={handleClose}
                disabled={deleteProjectMutation.isPending}
                className="h-[34px] rounded-[8px] border border-[#252b3b] bg-[#0f131d] px-[14px] text-[12px] font-medium text-[#cbd5e1] transition-colors hover:border-[#3b455e] hover:text-[#f8fafc] disabled:cursor-not-allowed disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="button"
                data-testid="archive-project-confirm-button"
                onClick={() => void handleConfirmArchive()}
                disabled={deleteProjectMutation.isPending}
                className="inline-flex h-[34px] items-center gap-1.5 rounded-[8px] bg-[#ef4444] px-[16px] text-[12px] font-semibold text-white transition-colors hover:bg-[#dc2626] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#ef4444] disabled:cursor-not-allowed disabled:opacity-60"
              >
                {deleteProjectMutation.isPending ? (
                  <>
                    <Loader2 className="size-[13px] animate-spin" aria-hidden="true" />
                    <span>Archiving...</span>
                  </>
                ) : (
                  <>
                    <Archive className="size-[13px]" aria-hidden="true" />
                    <span>Archive Project</span>
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </ProjectModalShell>
  );
}
