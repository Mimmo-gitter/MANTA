"use client";

import { useId, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AlertCircle,
  ArrowRight,
  ChevronDown,
  Loader2,
  Lock,
} from "lucide-react";
import { isApiHttpError } from "@/lib/api-client";
import { useCreateProjectMutation } from "@/hooks/use-projects";
import {
  isValidProjectSlug,
  saveProjectUiMeta,
  slugifyProjectName,
} from "@/lib/projects-api";
import type { DeploymentSignalType, ProjectItem } from "@/types/project";
import {
  ModalCloseButton,
  ProjectModalShell,
} from "@/components/projects/project-modal-shell";
import {
  FilterDropdown,
  type FilterDropdownOption,
} from "@/components/ui/filter-dropdown";

const DEPLOYMENT_SIGNAL_OPTIONS: readonly FilterDropdownOption<DeploymentSignalType>[] =
  [
    {
      value: "github_deployments_api",
      label: "GitHub Deployments API",
      dotClassName: "bg-[#00e599]",
    },
    {
      value: "argocd_postsync_webhook",
      label: "ArgoCD PostSync Webhook",
      dotClassName: "bg-[#00c2cb]",
    },
    {
      value: "direct_deployment_api",
      label: "Direct Deployment API",
      dotClassName: "bg-[#60a5fa]",
    },
  ];

interface CreateProjectModalProps {
  open: boolean;
  onClose: () => void;
  existingProjects?: ProjectItem[];
}

export function CreateProjectModal({
  open,
  onClose,
  existingProjects = [],
}: CreateProjectModalProps) {
  const router = useRouter();
  const createMutation = useCreateProjectMutation();

  const titleId = useId();
  const descId = useId();
  const nameId = useId();
  const nameHintId = useId();
  const slugId = useId();
  const slugHintId = useId();
  const descriptionInputId = useId();
  const slaId = useId();
  const wipId = useId();
  const staleId = useId();
  const signalId = useId();

  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugEdited, setSlugEdited] = useState(false);
  const [description, setDescription] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [tierSlaHours, setTierSlaHours] = useState("48");
  const [wipLimitPerDev, setWipLimitPerDev] = useState("3");
  const [prStaleHours, setPrStaleHours] = useState("48");
  const [deploymentSignal, setDeploymentSignal] =
    useState<DeploymentSignalType>("github_deployments_api");

  const [touched, setTouched] = useState(false);
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);

  const normalizedSlug = useMemo(
    () => (slugEdited ? slug.trim().toLowerCase() : slugifyProjectName(name)),
    [name, slug, slugEdited],
  );

  const duplicateInCache = useMemo(
    () =>
      Boolean(
        normalizedSlug &&
          existingProjects.some(
            (project) => project.slug.toLowerCase() === normalizedSlug,
          ),
      ),
    [existingProjects, normalizedSlug],
  );

  const resetForm = () => {
    setName("");
    setSlug("");
    setSlugEdited(false);
    setDescription("");
    setShowAdvanced(false);
    setTierSlaHours("48");
    setWipLimitPerDev("3");
    setPrStaleHours("48");
    setDeploymentSignal("github_deployments_api");
    setTouched(false);
    setFieldError(null);
    setApiError(null);
  };

  const handleClose = () => {
    if (createMutation.isPending) return;
    resetForm();
    onClose();
  };

  const handleNameChange = (value: string) => {
    setName(value);
    if (!slugEdited) {
      setSlug(slugifyProjectName(value));
    }
    setFieldError(null);
    setApiError(null);
  };

  const handleSlugChange = (value: string) => {
    setSlugEdited(true);
    setSlug(value.toLowerCase().replace(/\s+/g, "-"));
    setFieldError(null);
    setApiError(null);
  };

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setTouched(true);
    setFieldError(null);
    setApiError(null);

    const trimmedName = name.trim();
    if (!trimmedName) {
      setFieldError("Project name is required.");
      return;
    }

    if (!isValidProjectSlug(normalizedSlug)) {
      setFieldError(
        "Slug must be lowercase alphanumeric with single hyphens (max 64 chars).",
      );
      return;
    }

    if (duplicateInCache) {
      setApiError(
        `Project with slug '${normalizedSlug}' already exists in this workspace.`,
      );
      return;
    }

    const parsedSla = Number.parseInt(tierSlaHours, 10);
    const parsedWip = Number.parseInt(wipLimitPerDev, 10);
    const parsedStale = Number.parseInt(prStaleHours, 10);

    if (
      Number.isNaN(parsedSla) ||
      parsedSla < 1 ||
      parsedSla > 720 ||
      Number.isNaN(parsedWip) ||
      parsedWip < 1 ||
      parsedWip > 50 ||
      Number.isNaN(parsedStale) ||
      parsedStale < 1 ||
      parsedStale > 720
    ) {
      setFieldError(
        "Engineering thresholds must stay within valid bounds (SLA 1-720h, WIP 1-50, PR stale 1-720h).",
      );
      return;
    }

    try {
      const created = await createMutation.mutateAsync({
        name: trimmedName,
        slug: normalizedSlug,
        tier_sla_hours: parsedSla,
        wip_limit_per_dev: parsedWip,
        pr_stale_threshold_hours: parsedStale,
        authoritative_deployment_signal: deploymentSignal,
        authoritative_cd_signal: deploymentSignal,
        lead_time_first_commit_strategy: "feature_branch_root",
      });

      if (description.trim()) {
        saveProjectUiMeta(created, { description: description.trim() });
      }

      resetForm();
      onClose();
      router.push(`/projects/${created.slug}/settings`);
    } catch (error) {
      if (isApiHttpError(error)) {
        if (error.status === 409) {
          setApiError(
            error.detail ||
              `Project with slug '${normalizedSlug}' already exists in this organization.`,
          );
          return;
        }
        if (error.status === 403) {
          setApiError(
            "Only Tenant Administrators can create projects in this workspace.",
          );
          return;
        }
      }
      setApiError(
        error instanceof Error
          ? error.message
          : "Failed to create project. Please try again.",
      );
    }
  };

  const slugInvalid =
    (touched || slugEdited) &&
    normalizedSlug.length > 0 &&
    !isValidProjectSlug(normalizedSlug);

  return (
    <ProjectModalShell
      open={open}
      onClose={handleClose}
      titleId={titleId}
      descriptionId={descId}
      dismissLocked={createMutation.isPending}
    >
      <form onSubmit={handleSubmit} noValidate className="p-[22px]">
        {/* Header */}
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="inline-flex items-center gap-1.5 rounded-[4px] border border-[#00e599]/30 bg-[#00e599]/10 px-[8px] py-[2px] font-mono text-[10px] font-medium uppercase tracking-[0.6px] text-[#00e599]">
              <span
                className="size-[5px] rounded-full bg-[#00e599]"
                aria-hidden="true"
              />
              New Project Workspace
            </div>
            <h2
              id={titleId}
              className="mt-[10px] text-[18px] font-semibold leading-[24px] text-[#f8fafc]"
            >
              Create New Project
            </h2>
            <p
              id={descId}
              className="mt-[4px] text-[12px] leading-[18px] text-[#94a3b8]"
            >
              Initialize a project workspace, configure its URL slug, and prepare
              repository linking.
            </p>
          </div>
          <ModalCloseButton
            onClick={handleClose}
            disabled={createMutation.isPending}
            label="Close Create Project dialog"
          />
        </div>

        {/* Fields */}
        <div className="mt-[18px] flex flex-col gap-[12px]">
          <div className="rounded-[10px] border border-[#1e293b] bg-[#0a0e18] p-[12px] focus-within:border-[#00e599]/60">
            <label
              htmlFor={nameId}
              className="block text-[11px] font-medium text-[#cbd5e1]"
            >
              Project name <span className="text-[#ef4444]">*</span>
            </label>
            <p id={nameHintId} className="mt-[2px] text-[10px] text-[#64748b]">
              Displayed across dashboards, deployment signals, and settings.
            </p>
            <input
              id={nameId}
              name="projectName"
              type="text"
              required
              maxLength={255}
              value={name}
              onChange={(e) => handleNameChange(e.target.value)}
              aria-describedby={nameHintId}
              placeholder="Core Payment Gateway"
              className="mt-[6px] w-full bg-transparent text-[14px] font-medium text-[#f8fafc] placeholder:text-[#475569] focus:outline-none"
              data-testid="create-project-name-input"
            />
          </div>

          <div
            className={`rounded-[10px] border bg-[#0a0e18] p-[12px] ${
              slugInvalid || duplicateInCache || apiError
                ? "border-[#ef4444]/60"
                : "border-[#1e293b] focus-within:border-[#00e599]/60"
            }`}
          >
            <label
              htmlFor={slugId}
              className="block text-[11px] font-medium text-[#cbd5e1]"
            >
              Project URL slug (auto-generated, editable){" "}
              <span className="text-[#ef4444]">*</span>
            </label>
            <p id={slugHintId} className="mt-[2px] text-[10px] text-[#64748b]">
              Lowercase letters, numbers, and single hyphens only.
            </p>
            <div className="mt-[6px] flex items-center rounded-[6px] border border-[#1e293b] bg-[#0f131d] px-[10px] py-[6px]">
              <span
                className="mr-[6px] size-[6px] shrink-0 rounded-full bg-[#00e599]"
                aria-hidden="true"
              />
              <span className="shrink-0 font-mono text-[12px] text-[#64748b]">
                manta.app/projects/
              </span>
              <input
                id={slugId}
                name="projectSlug"
                type="text"
                required
                maxLength={64}
                value={normalizedSlug}
                onChange={(e) => handleSlugChange(e.target.value)}
                aria-describedby={slugHintId}
                aria-invalid={slugInvalid || duplicateInCache}
                placeholder="core-payment-gateway"
                className="min-w-0 flex-1 bg-transparent font-mono text-[12px] text-[#00e599] placeholder:text-[#475569] focus:outline-none"
                data-testid="create-project-slug-input"
              />
            </div>
          </div>

          <div className="rounded-[10px] border border-[#1e293b] bg-[#0a0e18] p-[12px] focus-within:border-[#00e599]/60">
            <div className="flex items-center justify-between gap-2">
              <label
                htmlFor={descriptionInputId}
                className="text-[11px] font-medium text-[#cbd5e1]"
              >
                Description (optional)
              </label>
              <span className="font-mono text-[10px] text-[#64748b]">
                {description.length}/250
              </span>
            </div>
            <textarea
              id={descriptionInputId}
              name="projectDescription"
              rows={2}
              maxLength={250}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Transaction clearing, payment provider routing, and automated settlement service."
              className="mt-[6px] w-full resize-none bg-transparent text-[12px] leading-[18px] text-[#e2e8f0] placeholder:text-[#475569] focus:outline-none"
              data-testid="create-project-description-input"
            />
          </div>

          {/* Advanced Engineering Thresholds */}
          <div className="rounded-[10px] border border-[#1e293b] bg-[#0d121f]">
            <button
              type="button"
              onClick={() => setShowAdvanced((prev) => !prev)}
              aria-expanded={showAdvanced}
              className="flex w-full items-center justify-between px-[12px] py-[9px] text-left text-[11px] font-medium text-[#94a3b8] transition-colors hover:text-[#e2e8f0]"
            >
              <span>Engineering Thresholds &amp; Default Deployment Signal</span>
              <ChevronDown
                className={`size-[14px] transition-transform ${
                  showAdvanced ? "rotate-180" : ""
                }`}
                aria-hidden="true"
              />
            </button>
            {showAdvanced && (
              <div className="grid grid-cols-1 gap-[10px] border-t border-[#1e293b] p-[12px] sm:grid-cols-2">
                <div>
                  <label
                    htmlFor={slaId}
                    className="block text-[10px] font-medium text-[#94a3b8]"
                  >
                    Tier SLA (hours, 1-720)
                  </label>
                  <input
                    id={slaId}
                    type="number"
                    min={1}
                    max={720}
                    value={tierSlaHours}
                    onChange={(e) => setTierSlaHours(e.target.value)}
                    className="mt-[4px] h-[32px] w-full rounded-[6px] border border-[#1e293b] bg-[#0a0e18] px-[10px] font-mono text-[12px] text-[#e2e8f0] focus:border-[#00e599] focus:outline-none"
                  />
                </div>
                <div>
                  <label
                    htmlFor={wipId}
                    className="block text-[10px] font-medium text-[#94a3b8]"
                  >
                    WIP Limit / Dev (1-50)
                  </label>
                  <input
                    id={wipId}
                    type="number"
                    min={1}
                    max={50}
                    value={wipLimitPerDev}
                    onChange={(e) => setWipLimitPerDev(e.target.value)}
                    className="mt-[4px] h-[32px] w-full rounded-[6px] border border-[#1e293b] bg-[#0a0e18] px-[10px] font-mono text-[12px] text-[#e2e8f0] focus:border-[#00e599] focus:outline-none"
                  />
                </div>
                <div>
                  <label
                    htmlFor={staleId}
                    className="block text-[10px] font-medium text-[#94a3b8]"
                  >
                    PR Stale Threshold (hours, 1-720)
                  </label>
                  <input
                    id={staleId}
                    type="number"
                    min={1}
                    max={720}
                    value={prStaleHours}
                    onChange={(e) => setPrStaleHours(e.target.value)}
                    className="mt-[4px] h-[32px] w-full rounded-[6px] border border-[#1e293b] bg-[#0a0e18] px-[10px] font-mono text-[12px] text-[#e2e8f0] focus:border-[#00e599] focus:outline-none"
                  />
                </div>
                <div>
                  <label
                    htmlFor={signalId}
                    className="block text-[10px] font-medium text-[#94a3b8]"
                  >
                    Deployment Signal
                  </label>
                  <FilterDropdown
                    selectId={signalId}
                    ariaLabel="Deployment Signal"
                    headerLabel="SELECT DEPLOYMENT SIGNAL"
                    value={deploymentSignal}
                    onChange={setDeploymentSignal}
                    options={DEPLOYMENT_SIGNAL_OPTIONS}
                    triggerLabel={
                      DEPLOYMENT_SIGNAL_OPTIONS.find(
                        (opt) => opt.value === deploymentSignal,
                      )?.label ?? deploymentSignal
                    }
                    align="left"
                    popoverWidthClassName="w-full"
                    triggerClassName="mt-[4px] h-[32px] w-full"
                  />
                </div>
              </div>
            )}
          </div>

          {/* Guidance Banner */}
          <div className="flex items-center gap-[10px] rounded-[10px] border border-[#1e293b] bg-[#0a0e18] px-[12px] py-[10px]">
            <Lock className="size-[14px] shrink-0 text-[#00e599]" aria-hidden="true" />
            <p className="text-[11px] leading-[16px] text-[#94a3b8]">
              After creating the project, you will be redirected to Settings to
              link GitHub repositories.
            </p>
          </div>
        </div>

        {(fieldError || apiError) && (
          <div
            role="alert"
            data-testid="create-project-error"
            className="mt-[12px] flex items-start gap-[8px] rounded-[8px] border border-[#ef4444]/40 bg-[#ef4444]/10 px-[12px] py-[9px] text-[12px] text-[#fca5a5]"
          >
            <AlertCircle
              className="mt-[2px] size-[14px] shrink-0 text-[#ef4444]"
              aria-hidden="true"
            />
            <span>{apiError || fieldError}</span>
          </div>
        )}

        {/* Footer */}
        <div className="mt-[18px] flex flex-wrap items-center justify-between gap-3 border-t border-[#1e293b] pt-[14px]">
          <div
            aria-live="polite"
            className="font-mono text-[11px] text-[#64748b]"
          >
            {!normalizedSlug ? (
              <span>Enter project name to generate slug</span>
            ) : slugInvalid ? (
              <span className="text-[#ef4444]">Invalid slug format</span>
            ) : duplicateInCache ? (
              <span className="text-[#ef4444]">Slug conflict detected</span>
            ) : (
              <span className="text-[#94a3b8]">
                Slug available · 0 conflicts
              </span>
            )}
          </div>

          <div className="flex items-center gap-[8px]">
            <button
              type="button"
              onClick={handleClose}
              disabled={createMutation.isPending}
              className="h-[36px] rounded-[8px] border border-[#252b3b] bg-[#0f131d] px-[14px] text-[12px] font-medium text-[#cbd5e1] transition-colors hover:border-[#3b455e] hover:text-[#f8fafc] disabled:cursor-not-allowed disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={createMutation.isPending}
              data-testid="create-project-submit"
              className="inline-flex h-[36px] items-center gap-1.5 rounded-[8px] bg-[#00e599] px-[16px] text-[12px] font-semibold text-[#07111a] transition-colors hover:bg-[#1ef5ab] disabled:cursor-not-allowed disabled:opacity-60"
            >
              {createMutation.isPending ? (
                <>
                  <Loader2 className="size-[14px] animate-spin" aria-hidden="true" />
                  <span>Creating...</span>
                </>
              ) : (
                <>
                  <span>Create Project</span>
                  <ArrowRight className="size-[14px]" aria-hidden="true" />
                </>
              )}
            </button>
          </div>
        </div>
      </form>
    </ProjectModalShell>
  );
}
