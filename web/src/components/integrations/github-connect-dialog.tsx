"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import {
  Check,
  ExternalLink,
  FolderGit2,
  GitBranch,
  Info,
  Loader2,
  Lock,
  ShieldCheck,
} from "lucide-react";
import {
  Badge,
  ConnectingStep,
  FailedStep,
  ModalShell,
  PrimaryButton,
  SectionLabel,
} from "@/components/integrations/connect-dialog-shell";

/* -------------------------------------------------------------------------- */
/* Types & data                                                               */
/* -------------------------------------------------------------------------- */

type Step = "intro" | "connecting" | "connected" | "failed";

export interface GitHubInstallation {
  account: string;
  organization: string;
  installationId: string;
  repositories: string[];
}

interface GitHubConnectDialogProps {
  open: boolean;
  onClose: () => void;
  /** Called when the user presses "Done" on the success screen. */
  onConnected?: (installation: GitHubInstallation) => void;
}

const PERMISSIONS: {
  title: string;
  description: string;
  badge: "Read-only" | "Webhook";
}[] = [
  {
    title: "Read Commit History & Branches",
    description:
      "Read-only telemetry for engineering velocity, cycle time, and WIP tracking.",
    badge: "Read-only",
  },
  {
    title: "Pull Requests & Review Delays",
    description:
      "Read-only monitoring for code review cycle times and delivery bottleneck metrics.",
    badge: "Read-only",
  },
  {
    title: "Webhook Ingestion Events",
    description:
      "Authenticated real-time event delivery via HMAC-SHA256 signature verification.",
    badge: "Webhook",
  },
  {
    title: "Production Deployment Signals",
    description:
      "Read-only tracking for deployment frequency and change failure rate analysis.",
    badge: "Read-only",
  },
];

/**
 * TODO: replace with the real flow.
 * 1. "Install GitHub App" should send the user to the GitHub App install URL.
 * 2. When GitHub redirects back with an installation_id, call your backend to
 *    verify the installation and load the repositories it can access.
 *
 * This mock resolves after a delay. Add `?githubError=1` to the URL to see the
 * failure state.
 */
async function connectGitHub(signal: AbortSignal): Promise<GitHubInstallation> {
  await new Promise<void>((resolve, reject) => {
    const timer = window.setTimeout(resolve, 2800);
    signal.addEventListener("abort", () => {
      window.clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    });
  });

  if (window.location.search.includes("githubError=1")) {
    throw new Error("GitHub installation could not be verified");
  }

  return {
    account: "acme-corp",
    organization: "Acme Enterprise Inc.",
    installationId: "#Example",
    repositories: [
      "acme/backend-api",
      "acme/frontend-web",
      "acme/data-pipeline",
    ],
  };
}

/* -------------------------------------------------------------------------- */
/* Shared bits                                                                */
/* -------------------------------------------------------------------------- */

function GitHubMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true" className={className}>
      <path
        fill="#f0f4f8"
        d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"
      />
    </svg>
  );
}

/* -------------------------------------------------------------------------- */
/* Step 1: intro                                                              */
/* -------------------------------------------------------------------------- */

function IntroStep({
  onClose,
  onInstall,
}: {
  onClose: () => void;
  onInstall: () => void;
}) {
  const titleId = useId();

  return (
    <ModalShell
      size="lg"
      icon={<GitHubMark className="size-[20px]" />}
      titleId={titleId}
      title="Connect GitHub"
      subtitle="Telemetry ingestion & automated refactoring workflows"
      onClose={onClose}
      footer={
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-[11px] text-[#7c839b]">
            Redirects to github.com to select account &amp; repos
          </p>
          <PrimaryButton onClick={onInstall}>
            Install GitHub App
            <ExternalLink className="size-3" aria-hidden="true" />
          </PrimaryButton>
        </div>
      }
    >
      <div className="flex flex-col gap-3 p-4">
        <section className="rounded-[8px] border border-[#1f2433] bg-[#0f131d] p-3.5">
          <h3 className="flex items-center gap-2 text-[12px] font-medium text-[#e2e8f0]">
            <Info className="size-3.5 text-[#00e599]" aria-hidden="true" />
            Official GitHub App Integration
          </h3>
          <p className="mt-2 text-[11px] leading-[17px] text-[#a3acc2]">
            M.A.N.T.A. integrates directly via the official GitHub App. When you
            click install, you will be securely redirected to GitHub to
            authorize the app, select your organization or account, and choose
            repository access permissions.
          </p>
        </section>

        <section
          aria-labelledby="perm-heading"
          className="rounded-[8px] border border-[#1f2433] bg-[#0f131d] p-3.5"
        >
          <h3 id="perm-heading" className="sr-only">
            Requested least-privilege permissions
          </h3>
          <div aria-hidden="true">
            <SectionLabel>Requested least-privilege permissions</SectionLabel>
          </div>

          <ul className="mt-2">
            {PERMISSIONS.map((p) => (
              <li
                key={p.title}
                className="flex items-start justify-between gap-3 border-t border-[#1f2433] py-2.5 first:border-t-0"
              >
                <div className="min-w-0">
                  <p className="text-[12px] font-medium leading-[16px] text-[#e2e8f0]">
                    {p.title}
                  </p>
                  <p className="mt-0.5 text-[10px] leading-[15px] text-[#7c839b]">
                    {p.description}
                  </p>
                </div>
                <Badge tone={p.badge === "Webhook" ? "green" : "muted"}>
                  {p.badge}
                </Badge>
              </li>
            ))}
          </ul>

          <div className="mt-1 flex items-start gap-2.5 rounded-[6px] bg-[#1a2030] p-3">
            <Lock
              className="mt-0.5 size-3 shrink-0 text-[#00e599]"
              aria-hidden="true"
            />
            <div>
              <p className="text-[12px] font-medium leading-[16px] text-[#e2e8f0]">
                Create Pull Requests
              </p>
              <p className="mt-0.5 text-[10px] leading-[15px] text-[#7c839b]">
                Strictly gated and enabled only after automated refactoring
                verification passes. M.A.N.T.A. will never push directly to
                branches or merge code without human sign-off.
              </p>
            </div>
          </div>
        </section>

        <section className="flex items-start gap-2.5 rounded-[8px] border border-[#252b3b] bg-[#1a2030]/70 p-3.5">
          <ShieldCheck
            className="mt-0.5 size-3.5 shrink-0 text-[#00e599]"
            aria-hidden="true"
          />
          <div>
            <h3 className="text-[12px] font-medium leading-[16px] text-[#e2e8f0]">
              Zero Token Exposure
            </h3>
            <p className="mt-1 text-[10px] leading-[15px] text-[#7c839b]">
              No personal access tokens or SSH keys required. Permissions and
              short-lived tokens are managed directly and securely via official
              GitHub App authorization.
            </p>
          </div>
        </section>
      </div>
    </ModalShell>
  );
}

/* -------------------------------------------------------------------------- */
/* Step 3: connected                                                          */
/* -------------------------------------------------------------------------- */

function StatusCell({
  label,
  status,
}: {
  label: string;
  status: "Active" | "Verified" | "Syncing" | "Pending";
}) {
  return (
    <li className="flex items-center justify-between gap-2 rounded-[6px] border border-[#1f2433] bg-[#161b26] px-2.5 py-2">
      <span className="text-[10px] leading-[14px] text-[#a3acc2]">{label}</span>
      <span
        className={cn(
          "inline-flex shrink-0 items-center gap-1.5 font-mono text-[10px] font-semibold",
          status === "Pending" ? "text-[#fbbf24]" : "text-[#00e599]",
        )}
      >
        {status === "Syncing" ? (
          <Loader2
            className="size-2.5 motion-safe:animate-spin"
            aria-hidden="true"
          />
        ) : (
          <span
            aria-hidden="true"
            className={cn(
              "size-1.5 rounded-full",
              status === "Pending" ? "bg-[#fbbf24]" : "bg-[#00e599]",
            )}
          />
        )}
        {status}
      </span>
    </li>
  );
}

function ConnectedStep({
  installation,
  onDone,
  onClose,
}: {
  installation: GitHubInstallation;
  onDone: () => void;
  onClose: () => void;
}) {
  const titleId = useId();
  const count = installation.repositories.length;

  return (
    <ModalShell
      size="lg"
      icon={<GitHubMark className="size-[20px]" />}
      titleId={titleId}
      title="GitHub Connected"
      subtitle="Repository telemetry and event syncing active with M.A.N.T.A."
      onClose={onClose}
      footer={
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="flex items-center gap-1.5 font-mono text-[10px] text-[#7c839b]">
            <Lock className="size-3 shrink-0" aria-hidden="true" />
            Integration credentials are stored securely in an encrypted vault.
          </p>
          <PrimaryButton onClick={onDone}>
            <Check className="size-3.5" aria-hidden="true" />
            Done
          </PrimaryButton>
        </div>
      }
    >
      <div className="flex flex-col gap-3 p-4">
        {/* Installation */}
        <section className="flex items-center justify-between gap-3 rounded-[8px] border border-[#1f2433] bg-[#0f131d] p-3.5">
          <div className="min-w-0">
            <p className="truncate text-[13px] font-semibold text-[#e2e8f0]">
              {installation.account}{" "}
              <span className="text-[11px] font-normal text-[#7c839b]">
                ({installation.organization})
              </span>
            </p>
            <p className="mt-0.5 font-mono text-[10px] text-[#7c839b]">
              Installation ID: {installation.installationId}
            </p>
          </div>
          <Badge tone="green">Active</Badge>
        </section>

        {/* Repository access */}
        <section className="rounded-[8px] border border-[#1f2433] bg-[#0f131d] p-3.5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="flex items-center gap-1.5 text-[11px] font-medium text-[#e2e8f0]">
                <FolderGit2
                  className="size-3.5 text-[#a3acc2]"
                  aria-hidden="true"
                />
                Repository Access
              </h3>
              <Badge tone="green">
                {count} {count === 1 ? "repository" : "repositories"} accessible
              </Badge>
            </div>
            {/* TODO: link to the installation's repository settings on github.com */}
            <a
              href="https://github.com/settings/installations"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 rounded text-[11px] text-[#00e599] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00e599]"
            >
              Manage repositories
              <ExternalLink className="size-3" aria-hidden="true" />
              <span className="sr-only">(opens in a new tab)</span>
            </a>
          </div>
          <ul className="mt-3 grid gap-2 sm:grid-cols-3">
            {installation.repositories.map((repo) => (
              <li
                key={repo}
                className="flex items-center gap-1.5 rounded-[6px] border border-[#1f2433] bg-[#161b26] px-2.5 py-2 font-mono text-[10px] text-[#e2e8f0]"
              >
                <GitBranch
                  className="size-3 shrink-0 text-[#00e599]"
                  aria-hidden="true"
                />
                <span className="truncate">{repo}</span>
              </li>
            ))}
          </ul>
        </section>

        {/* Integration statuses */}
        <section className="rounded-[8px] border border-[#1f2433] bg-[#0f131d] p-3.5">
          <SectionLabel>Integration statuses</SectionLabel>
          <ul className="mt-3 grid gap-2 sm:grid-cols-2">
            <StatusCell label="GitHub App installation" status="Active" />
            <StatusCell label="Repository access" status="Verified" />
            <StatusCell label="Initial data synchronization" status="Syncing" />
            <StatusCell label="Webhook verification" status="Pending" />
          </ul>
        </section>

        {/* Permissions summary */}
        <section className="rounded-[8px] border border-[#1f2433] bg-[#0f131d] p-3.5">
          <div className="flex items-center justify-between gap-2">
            <SectionLabel>Permissions summary</SectionLabel>
            <span className="font-mono text-[10px] text-[#7c839b]">
              Principle of Least Privilege
            </span>
          </div>
          <ul className="mt-3 grid gap-2 sm:grid-cols-3">
            <PermissionCard
              title="Commits, Branches, PRs & Reviews"
              label="Scope"
              value="Read-only"
              tone="green"
            />
            <PermissionCard
              title="Deployment signals"
              label="Status"
              value="Not configured"
              tone="muted"
            />
            <PermissionCard
              title="Pull Request creation"
              label="Action"
              value="Conditional write"
              tone="green"
            />
          </ul>
        </section>
      </div>
    </ModalShell>
  );
}

function PermissionCard({
  title,
  label,
  value,
  tone,
}: {
  title: string;
  label: string;
  value: string;
  tone: "green" | "muted";
}) {
  return (
    <li className="flex min-h-[64px] flex-col justify-between gap-2 rounded-[6px] border border-[#1f2433] bg-[#161b26] p-2.5">
      <p className="text-[11px] font-medium leading-[15px] text-[#e2e8f0]">
        {title}
      </p>
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-[9px] text-[#7c839b]">{label}</span>
        <Badge tone={tone}>{value}</Badge>
      </div>
    </li>
  );
}

/* -------------------------------------------------------------------------- */
/* Public component                                                           */
/* -------------------------------------------------------------------------- */

export function GitHubConnectDialog({
  open,
  onClose,
  onConnected,
}: GitHubConnectDialogProps) {
  const [step, setStep] = useState<Step>("intro");
  const [progress, setProgress] = useState(0);
  const [installation, setInstallation] = useState<GitHubInstallation | null>(
    null,
  );
  const abortRef = useRef<AbortController | null>(null);

  // Reset every time the dialog is opened; abort anything in flight on close
  useEffect(() => {
    if (open) {
      setStep("intro");
      setProgress(0);
      setInstallation(null);
    }
    return () => abortRef.current?.abort();
  }, [open]);

  // Ease the progress bar toward 90% while we wait for the backend
  useEffect(() => {
    if (step !== "connecting") return;
    const id = window.setInterval(() => {
      setProgress((p) => (p < 90 ? p + (90 - p) * 0.18 : p));
    }, 350);
    return () => window.clearInterval(id);
  }, [step]);

  const start = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setProgress(8);
    setStep("connecting");

    try {
      const result = await connectGitHub(controller.signal);
      if (controller.signal.aborted) return;
      setInstallation(result);
      setProgress(100);
      setStep("connected");
    } catch {
      if (controller.signal.aborted) return;
      setStep("failed");
    }
  }, []);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    setStep("intro");
    setProgress(0);
  }, []);

  const close = useCallback(() => {
    abortRef.current?.abort();
    onClose();
  }, [onClose]);

  if (!open) return null;

  switch (step) {
    case "intro":
      return <IntroStep onClose={close} onInstall={start} />;
    case "connecting":
      return (
        <ConnectingStep
          icon={<GitHubMark className="size-[20px]" />}
          providerName="GitHub"
          description="Verifying installation and loading repository access."
          progress={progress}
          onCancel={cancel}
        />
      );
    case "connected":
      return installation ? (
        <ConnectedStep
          installation={installation}
          onClose={close}
          onDone={() => {
            onConnected?.(installation);
            close();
          }}
        />
      ) : null;
    case "failed":
      return (
        <FailedStep
          icon={<GitHubMark className="size-[20px]" />}
          providerName="GitHub"
          onClose={close}
          onRetry={start}
        />
      );
  }
}
