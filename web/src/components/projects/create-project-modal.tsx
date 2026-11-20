"use client";

import { useEffect, useId, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  ArrowRight,
  ChevronDown,
  Loader2,
  Lock,
  RotateCcw,
} from "lucide-react";
import { isApiHttpError } from "@/lib/api-client";
import { useCreateProjectMutation } from "@/hooks/use-projects";
import {
  formatRelativeTime,
  getInvalidSlugCharacters,
  isValidProjectSlug,
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
import { cn } from "@/lib/utils";

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
  initialName?: string;
}

export function CreateProjectModal({
  open,
  onClose,
  existingProjects = [],
  initialName = "",
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
  const [conflictFromApi, setConflictFromApi] = useState(false);

  useEffect(() => {
    if (!open) return;
    const trimmedInit = initialName.trim();
    if (trimmedInit) {
      setName(trimmedInit);
      setSlug(slugifyProjectName(trimmedInit));
      setSlugEdited(false);
      setFieldError(null);
      setApiError(null);
      setConflictFromApi(false);
      setTouched(false);
    }
  }, [initialName, open]);

  const displayedSlug = useMemo(
    () => (slugEdited ? slug : slugifyProjectName(name)),
    [name, slug, slugEdited],
  );
  const trimmedSlug = displayedSlug.trim();

  const duplicateProject = useMemo(() => {
    if (!trimmedSlug) return null;
    const lower = trimmedSlug.toLowerCase();
    return (
      existingProjects.find((project) => project.slug.toLowerCase() === lower) ??
      null
    );
  }, [existingProjects, trimmedSlug]);

  const suggestedSlug = useMemo(() => {
    const base = slugifyProjectName(trimmedSlug) || "project";
    const existingSet = new Set(
      existingProjects.map((project) => project.slug.toLowerCase()),
    );
    let idx = 2;
    while (existingSet.has(`${base}-v${idx}`)) {
      idx += 1;
    }
    return `${base}-v${idx}`;
  }, [existingProjects, trimmedSlug]);

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
    setConflictFromApi(false);
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
    setConflictFromApi(false);
  };

  const handleSlugChange = (value: string) => {
    setSlugEdited(true);
    setSlug(value);
    setFieldError(null);
    setApiError(null);
    setConflictFromApi(false);
  };

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setTouched(true);
    setFieldError(null);
    setApiError(null);
    setConflictFromApi(false);

    const trimmedName = name.trim();
    if (!trimmedName) {
      setFieldError("Project name is required.");
      return;
    }

    if (!isValidProjectSlug(trimmedSlug)) {
      setFieldError("Cannot create project: URL slug contains invalid characters.");
      return;
    }

    if (duplicateProject) {
      setApiError(
        `Cannot create project: URL slug '${trimmedSlug}' already exists in this workspace.`,
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
      const trimmedDescription = description.trim();
      const created = await createMutation.mutateAsync({
        name: trimmedName,
        slug: trimmedSlug,
        description: trimmedDescription || undefined,
        tier_sla_hours: parsedSla,
        wip_limit_per_dev: parsedWip,
        pr_stale_threshold_hours: parsedStale,
        authoritative_deployment_signal: deploymentSignal,
        authoritative_cd_signal: deploymentSignal,
        lead_time_first_commit_strategy: "feature_branch_root",
      });

      resetForm();
      onClose();
      router.push(`/projects/${created.slug}/settings`);
    } catch (error) {
      if (isApiHttpError(error)) {
        if (error.status === 409) {
          setConflictFromApi(true);
          setApiError(
            error.detail ||
              `Cannot create project: URL slug '${trimmedSlug}' already exists in this organization.`,
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
    trimmedSlug.length > 0 && !isValidProjectSlug(trimmedSlug);
  const invalidChars = useMemo(
    () => (slugInvalid ? getInvalidSlugCharacters(trimmedSlug) : []),
    [slugInvalid, trimmedSlug],
  );
  const isSlugConflict = Boolean(duplicateProject) || conflictFromApi;
  const isInvalidSlug =
    slugInvalid || (touched && name.trim().length > 0 && trimmedSlug.length === 0);
  const topBannerMessage =
    apiError ||
    fieldError ||
    (duplicateProject
      ? `Cannot create project: URL slug '${trimmedSlug}' already exists in this workspace.`
      : slugInvalid
        ? "Cannot create project: URL slug contains invalid characters."
        : null);
  const hasAnyError = Boolean(topBannerMessage);

  return (
    <ProjectModalShell
      open={open}
      onClose={handleClose}
      titleId={titleId}
      descriptionId={descId}
      dismissLocked={createMutation.isPending}
      accentTone={hasAnyError ? "danger" : "emerald"}
    >
      <form onSubmit={handleSubmit} noValidate className="p-[20px]">
        {/* Header (Figma 2A-2E) */}
        <div className="flex items-start justify-between gap-4">
          <div>
            {createMutation.isPending ? (
              <div className="inline-flex items-center gap-1.5 rounded-full border border-[#00e599]/40 bg-[#00e599]/15 px-[10px] py-[3px] font-mono text-[10px] font-medium uppercase tracking-[0.6px] text-[#00e599]">
                <span
                  className="size-[5px] animate-pulse rounded-full bg-[#00e599]"
                  aria-hidden="true"
                />
                <span>Provisioning Workspace...</span>
              </div>
            ) : isSlugConflict ? (
              <div className="inline-flex items-center gap-1.5 rounded-full border border-[#ef4444]/40 bg-[#ef4444]/10 px-[10px] py-[3px] font-mono text-[10px] font-medium uppercase tracking-[0.6px] text-[#fca5a5]">
                <span
                  className="size-[5px] rounded-full bg-[#ef4444]"
                  aria-hidden="true"
                />
                <span>Validation Blocked · Slug Conflict</span>
              </div>
            ) : isInvalidSlug ? (
              <div className="inline-flex items-center gap-1.5 rounded-full border border-[#ef4444]/40 bg-[#ef4444]/10 px-[10px] py-[3px] font-mono text-[10px] font-medium uppercase tracking-[0.6px] text-[#fca5a5]">
                <span
                  className="size-[5px] rounded-full bg-[#ef4444]"
                  aria-hidden="true"
                />
                <span>Validation Blocked · Invalid Slug Format</span>
              </div>
            ) : hasAnyError ? (
              <div className="inline-flex items-center gap-1.5 rounded-full border border-[#ef4444]/40 bg-[#ef4444]/10 px-[10px] py-[3px] font-mono text-[10px] font-medium uppercase tracking-[0.6px] text-[#fca5a5]">
                <span
                  className="size-[5px] rounded-full bg-[#ef4444]"
                  aria-hidden="true"
                />
                <span>Validation Blocked</span>
              </div>
            ) : (
              <div className="inline-flex items-center gap-1.5 rounded-full border border-[#00e599]/30 bg-[#00e599]/10 px-[10px] py-[3px] font-mono text-[10px] font-medium uppercase tracking-[0.6px] text-[#00e599]">
                <span
                  className="size-[5px] rounded-full bg-[#00e599]"
                  aria-hidden="true"
                />
                <span>New Project Workspace</span>
              </div>
            )}
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

        {/* Top Validation Alert Banner (Figma 2C/2D) */}
        {topBannerMessage && (
          <div
            role="alert"
            data-testid="create-project-error"
            className="mt-[14px] flex items-start gap-[8px] rounded-[8px] border border-[#ef4444]/45 bg-[#24131a] px-[12px] py-[9px] text-[12px] text-[#fca5a5]"
          >
            <AlertTriangle
              className="mt-[2px] size-[14px] shrink-0 text-[#ef4444]"
              aria-hidden="true"
            />
            <span>{topBannerMessage}</span>
          </div>
        )}

        {/* Fields */}
        <div className="mt-[14px] flex flex-col gap-[10px]">
          <div
            className={cn(
              "rounded-[10px] border bg-[#0a0e18] p-[12px] transition-colors",
              touched && !name.trim()
                ? "border-[#ef4444]/70"
                : name.trim()
                  ? "border-[#00e599]/75 focus-within:border-[#00e599]"
                  : "border-[#1e293b] focus-within:border-[#00e599]",
            )}
          >
            <label
              htmlFor={nameId}
              className="block text-[11px] font-medium text-[#cbd5e1]"
            >
              Project name <span className="text-[#ef4444]">*</span>
            </label>
            <input
              id={nameId}
              name="projectName"
              type="text"
              required
              maxLength={255}
              disabled={createMutation.isPending}
              value={name}
              onChange={(e) => handleNameChange(e.target.value)}
              aria-describedby={nameHintId}
              placeholder="e.g. Core Payment Gateway"
              className="m-0 mt-[6px] w-full border-0 bg-transparent p-0 font-mono text-[13px] font-medium text-[#f8fafc] shadow-none outline-none ring-0 placeholder:text-[#475569] focus:border-0 focus:outline-none focus:ring-0 disabled:opacity-60"
              data-testid="create-project-name-input"
            />
            <p id={nameHintId} className="sr-only">
              Displayed across dashboards, deployment signals, and settings.
            </p>
          </div>

          <div>
            <div
              className={cn(
                "rounded-[10px] border bg-[#0a0e18] p-[12px] transition-colors",
                isSlugConflict || isInvalidSlug
                  ? "border-[#ef4444]/75"
                  : trimmedSlug
                    ? "border-[#00e599]/55 focus-within:border-[#00e599]"
                    : "border-[#1e293b] focus-within:border-[#00e599]/70",
              )}
            >
              <div className="flex items-center justify-between gap-2">
                <label
                  htmlFor={slugId}
                  className="block text-[11px] font-medium text-[#cbd5e1]"
                >
                  Project URL slug (auto-generated, editable){" "}
                  <span className="text-[#ef4444]">*</span>
                </label>
                {slugEdited && (
                  <button
                    type="button"
                    disabled={createMutation.isPending}
                    onClick={() => {
                      setSlugEdited(false);
                      setSlug(slugifyProjectName(name));
                      setFieldError(null);
                      setApiError(null);
                      setConflictFromApi(false);
                    }}
                    className="inline-flex items-center gap-1 font-mono text-[10px] text-[#94a3b8] hover:text-[#00e599] disabled:opacity-50"
                  >
                    <RotateCcw className="size-[10px]" aria-hidden="true" />
                    <span>Auto-sync</span>
                  </button>
                )}
              </div>
              <div className="mt-[6px] flex items-center font-mono text-[13px]">
                {trimmedSlug && !isSlugConflict && !isInvalidSlug && (
                  <span
                    className="mr-[8px] size-[6px] shrink-0 rounded-full bg-[#00e599]"
                    aria-hidden="true"
                  />
                )}
                <span
                  className={cn(
                    "shrink-0",
                    trimmedSlug && !isSlugConflict && !isInvalidSlug
                      ? "text-[#00e599]"
                      : "text-[#64748b]",
                  )}
                >
                  manta.app/projects/
                </span>
                <input
                  id={slugId}
                  name="projectSlug"
                  type="text"
                  required
                  maxLength={64}
                  disabled={createMutation.isPending}
                  value={displayedSlug}
                  onChange={(e) => handleSlugChange(e.target.value)}
                  aria-describedby={slugHintId}
                  aria-invalid={isInvalidSlug || isSlugConflict}
                  placeholder="auto-generated-slug"
                  className={cn(
                    "m-0 min-w-0 flex-1 border-0 bg-transparent p-0 font-mono text-[13px] shadow-none outline-none ring-0 placeholder:text-[#475569] focus:border-0 focus:outline-none focus:ring-0 disabled:opacity-60",
                    isSlugConflict || isInvalidSlug
                      ? "text-[#f8fafc]"
                      : trimmedSlug
                        ? "text-[#00e599]"
                        : "text-[#cbd5e1]",
                  )}
                  data-testid="create-project-slug-input"
                />
                {(isSlugConflict || isInvalidSlug) && (
                  <AlertTriangle
                    className="ml-[8px] size-[14px] shrink-0 text-[#f59e0b]"
                    aria-hidden="true"
                  />
                )}
              </div>
              <p id={slugHintId} className="sr-only">
                Lowercase letters, digits, and single hyphens only.
              </p>
            </div>

            {isInvalidSlug && (
              <p className="mt-[6px] text-[11px] leading-[16px] text-[#fca5a5]">
                Slug must contain only lowercase letters (a-z), digits (0-9), and hyphens (-).
                {invalidChars.length > 0 && (
                  <> {invalidChars.join(", ")} not allowed.</>
                )}
              </p>
            )}

            {isSlugConflict && !isInvalidSlug && (
              <div className="mt-[6px] flex flex-wrap items-center justify-between gap-2 text-[11px] leading-[16px] text-[#fca5a5]">
                <span>
                  {duplicateProject
                    ? `Slug '${trimmedSlug}' is already used by ${duplicateProject.name} (created ${formatRelativeTime(duplicateProject.created_at)}).`
                    : `Slug '${trimmedSlug}' is already taken in this organization.`}
                </span>
                <button
                  type="button"
                  onClick={() => handleSlugChange(suggestedSlug)}
                  className="font-mono text-[10px] font-semibold text-[#00e599] underline-offset-2 hover:underline"
                >
                  Use &lsquo;{suggestedSlug}&rsquo;
                </button>
              </div>
            )}
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
              disabled={createMutation.isPending}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Describe project scope, service boundaries, and delivery team..."
              data-gramm="false"
              data-gramm_editor="false"
              data-enable-grammarly="false"
              className="m-0 mt-[6px] w-full resize-none border-0 border-none bg-transparent p-0 text-[12px] leading-[18px] text-[#e2e8f0] shadow-none outline-none ring-0 placeholder:text-[#475569] focus:border-0 focus:outline-none focus:ring-0 disabled:opacity-60"
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
                    className="mt-[4px] h-[32px] w-full rounded-[6px] border border-[#1e293b] border-solid bg-[#0a0e18] px-[10px] font-mono text-[12px] text-[#e2e8f0] shadow-none focus:border-[#00e599] focus:outline-none focus:ring-0"
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
                    className="mt-[4px] h-[32px] w-full rounded-[6px] border border-[#1e293b] border-solid bg-[#0a0e18] px-[10px] font-mono text-[12px] text-[#e2e8f0] shadow-none focus:border-[#00e599] focus:outline-none focus:ring-0"
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
                    className="mt-[4px] h-[32px] w-full rounded-[6px] border border-[#1e293b] border-solid bg-[#0a0e18] px-[10px] font-mono text-[12px] text-[#e2e8f0] shadow-none focus:border-[#00e599] focus:outline-none focus:ring-0"
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
              {createMutation.isPending
                ? "Provisioning workspace and redirecting to Project Settings..."
                : "After creating the project, you will be redirected to Settings to link GitHub repositories."}
            </p>
          </div>
        </div>

        {/* Footer */}
        <div className="mt-[18px] flex flex-wrap items-center justify-between gap-3 border-t border-[#1e293b] pt-[14px]">
          <div
            aria-live="polite"
            className="font-mono text-[11px] text-[#64748b]"
          >
            {createMutation.isPending ? (
              <span className="text-[#00e599]">Provisioning...</span>
            ) : hasAnyError ? (
              <span className="text-[#ef4444]">1 validation error</span>
            ) : !trimmedSlug ? (
              <span>Slug auto-generated from name</span>
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
                  <span>Provisioning Workspace...</span>
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
