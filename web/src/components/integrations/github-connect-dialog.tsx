"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import { getCurrentUser } from "@/lib/auth";
import {
  ArrowRight,
  Check,
  Copy,
  Eye,
  EyeOff,
  Loader2,
  ShieldCheck,
  User,
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

export interface GitHubConnection {
  account: string;
  /** Masked token for display only, e.g. "ghp_•••828e". Never the raw token. */
  tokenMask: string;
  webhookUrl: string;
  /** True when a webhook secret was supplied and signatures are verified. */
  webhookVerified: boolean;
}

interface GitHubConnectDialogProps {
  open: boolean;
  onClose: () => void;
  /** Called when the user presses "Done" on the success screen. */
  onConnected?: (connection: GitHubConnection) => void;
}

/**
 * TODO: replace with the real flow.
 * Send the token (and optional webhook secret) to your backend over HTTPS.
 * The backend validates the token against the GitHub API, stores it encrypted,
 * and returns the account, a masked token and the webhook URL.
 *
 * This mock resolves after a delay. Add `?githubError=1` to the URL to see the
 * failure state.
 */
async function connectGitHub(
  token: string,
  webhookSecret: string,
  signal: AbortSignal,
): Promise<GitHubConnection> {
  // Get the current authenticated M.A.N.T.A. user
  const user = await getCurrentUser();

  if (!user?.access_token) {
    throw new Error("Your session has expired. Please sign in again.");
  }

  const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

  const response = await fetch(
    `${API_URL}/api/v1/integrations/github/connect`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${user.access_token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        access_token: token,
        ...(webhookSecret.trim()
          ? { webhook_secret: webhookSecret.trim() }
          : {}),
      }),
      signal,
    },
  );

  const data = await response.json().catch(() => null);

  if (!response.ok) {
    const message =
      typeof data?.detail === "string"
        ? data.detail
        : response.status === 400
          ? "Invalid GitHub access token. Please check your token."
          : `GitHub connection failed (${response.status}).`;

    throw new Error(message);
  }

  return {
    account: data.external_account_id,
    tokenMask: `ghp_•••${token.slice(-4)}`,
    webhookUrl: data.webhook_url,
    webhookVerified: Boolean(webhookSecret.trim()),
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
/* Step 1: intro (token form)                                                 */
/* -------------------------------------------------------------------------- */

function SecretField({
  id,
  label,
  optional = false,
  required = false,
  placeholder,
  helper,
  value,
  onChange,
  revealLabel,
}: {
  id: string;
  label: string;
  optional?: boolean;
  required?: boolean;
  placeholder: string;
  helper: string;
  value: string;
  onChange: (value: string) => void;
  revealLabel: string;
}) {
  const [visible, setVisible] = useState(false);
  const helperId = `${id}-helper`;

  return (
    <div>
      <label
        htmlFor={id}
        className="mb-2 block text-[11px] font-medium leading-[16px] text-[#e2e8f0]"
      >
        {label}
        {required && (
          <span className="text-[#ef4444]" aria-hidden="true">
            {" "}
            *
          </span>
        )}
        {optional && (
          <span className="font-normal text-[#7c839b]"> (Optional)</span>
        )}
      </label>
      <div className="relative">
        <input
          id={id}
          type={visible ? "text" : "password"}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          required={required}
          aria-describedby={helperId}
          autoComplete="off"
          autoCapitalize="off"
          spellCheck={false}
          className="h-[44px] w-full rounded-[6px] border border-[#252b3b] bg-[#0a0d14] pl-3.5 pr-10 font-mono text-[12px] text-[#e2e8f0] placeholder:text-[#4b5266] focus-visible:border-[#00e599]/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00e599]/40"
        />
        <button
          type="button"
          onClick={() => setVisible((v) => !v)}
          aria-label={`${visible ? "Hide" : "Show"} ${revealLabel}`}
          aria-pressed={visible}
          className="absolute right-2 top-1/2 flex size-[28px] -translate-y-1/2 items-center justify-center rounded-[6px] text-[#7c839b] transition-colors hover:text-[#e2e8f0] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00e599]"
        >
          {visible ? (
            <EyeOff className="size-3.5" aria-hidden="true" />
          ) : (
            <Eye className="size-3.5" aria-hidden="true" />
          )}
        </button>
      </div>
      <p
        id={helperId}
        className="mt-2 text-[10px] leading-[14px] text-[#7c839b]"
      >
        {helper}
      </p>
    </div>
  );
}

function IntroStep({
  token,
  webhookSecret,
  onTokenChange,
  onWebhookSecretChange,
  onClose,
  onConnect,
}: {
  token: string;
  webhookSecret: string;
  onTokenChange: (value: string) => void;
  onWebhookSecretChange: (value: string) => void;
  onClose: () => void;
  onConnect: () => void;
}) {
  const titleId = useId();
  const formId = useId();
  const tokenId = useId();
  const secretId = useId();
  const canSubmit = token.trim().length > 0;

  return (
    <ModalShell
      size="lg"
      headerLayout="stacked"
      icon={<GitHubMark className="size-[20px]" />}
      titleId={titleId}
      title="Connect GitHub"
      subtitle="Connect via GitHub Personal Access Token to ingest telemetry and refactoring workflows."
      onClose={onClose}
      footer={
        <PrimaryButton
          type="submit"
          form={formId}
          disabled={!canSubmit}
          className="h-[40px] w-full text-[12px]"
        >
          Connect GitHub
          <ArrowRight className="size-3.5" aria-hidden="true" />
        </PrimaryButton>
      }
    >
      <form
        id={formId}
        onSubmit={(e) => {
          e.preventDefault();
          if (canSubmit) onConnect();
        }}
        className="flex flex-col gap-5 px-6 pb-6 pt-3"
      >
        <SecretField
          id={tokenId}
          label="GitHub Personal Access Token"
          required
          placeholder="ghp_xxxxxxxxxxxxxxxxxxxx"
          helper="Your token is encrypted and securely stored by M.A.N.T.A."
          value={token}
          onChange={onTokenChange}
          revealLabel="token"
        />
        <SecretField
          id={secretId}
          label="Webhook Secret"
          optional
          placeholder="Enter webhook secret"
          helper="Used to verify incoming GitHub webhook events."
          value={webhookSecret}
          onChange={onWebhookSecretChange}
          revealLabel="webhook secret"
        />

        <section className="flex items-start gap-3 rounded-[8px] border border-[#252b3b] bg-[#161b26]/60 p-3.5">
          <ShieldCheck
            className="mt-0.5 size-3.5 shrink-0 text-[#22d3ee]"
            aria-hidden="true"
          />
          <div className="min-w-0">
            <h3 className="text-[11px] font-medium leading-[16px] text-[#e2e8f0]">
              Zero-Trust Security &amp; Verification
            </h3>
            <ul className="mt-1.5 space-y-1">
              {[
                "Credentials and personal access tokens are encrypted at rest with AES-256.",
                "Incoming webhook requests are verified using HMAC-SHA256 signatures.",
              ].map((line) => (
                <li
                  key={line}
                  className="flex items-start gap-2 text-[10px] leading-[15px] text-[#a3acc2]"
                >
                  <span
                    aria-hidden="true"
                    className="mt-[6px] size-[3px] shrink-0 rounded-full bg-[#22d3ee]"
                  />
                  {line}
                </li>
              ))}
            </ul>
          </div>
        </section>
      </form>
    </ModalShell>
  );
}

/* -------------------------------------------------------------------------- */
/* Step 3: connected                                                          */
/* -------------------------------------------------------------------------- */

type MetricStatus = "Validated" | "Active" | "Syncing" | "Pending";

function MetricCell({
  label,
  status,
}: {
  label: string;
  status: MetricStatus;
}) {
  const pending = status === "Pending";
  return (
    <li className="flex flex-col gap-1.5 rounded-[6px] border border-[#1f2433] bg-[#161b26] px-3 py-2.5">
      <span className="text-[10px] leading-[14px] text-[#a3acc2]">{label}</span>
      <span
        className={cn(
          "inline-flex items-center gap-1.5 font-mono text-[11px] font-semibold",
          pending ? "text-[#fbbf24]" : "text-[#00e599]",
        )}
      >
        {status === "Syncing" ? (
          <Loader2 className="size-2.5 animate-spin" aria-hidden="true" />
        ) : (
          <span
            aria-hidden="true"
            className={cn(
              "size-1.5 rounded-full",
              pending ? "bg-[#fbbf24]" : "bg-[#00e599]",
            )}
          />
        )}
        {status}
      </span>
    </li>
  );
}

function ConnectedStep({
  connection,
  onDone,
  onClose,
}: {
  connection: GitHubConnection;
  onDone: () => void;
  onClose: () => void;
}) {
  const titleId = useId();
  const [copied, setCopied] = useState(false);
  const resetTimer = useRef<number | undefined>(undefined);

  useEffect(() => () => window.clearTimeout(resetTimer.current), []);

  const copyUrl = async () => {
    try {
      await navigator.clipboard.writeText(connection.webhookUrl);
      setCopied(true);
      window.clearTimeout(resetTimer.current);
      resetTimer.current = window.setTimeout(() => setCopied(false), 1800);
    } catch {
      // Clipboard unavailable (insecure context or permission denied); the URL
      // stays selectable in the field so the user can copy it manually.
    }
  };

  return (
    <ModalShell
      size="lg"
      icon={<GitHubMark className="size-[20px]" />}
      titleId={titleId}
      title="GitHub Connected"
      subtitle="Repository telemetry and webhook event syncing active with M.A.N.T.A."
      onClose={onClose}
      footer={
        <div className="flex justify-end">
          <PrimaryButton onClick={onDone}>
            <Check className="size-3.5" aria-hidden="true" />
            Done
          </PrimaryButton>
        </div>
      }
    >
      <div className="flex flex-col gap-5 p-6">
        {/* Account */}
        <section className="flex items-center justify-between gap-3 rounded-[8px] border border-[#1f2433] bg-[#0f131d] p-3.5">
          <div className="flex min-w-0 items-center gap-3">
            <div
              aria-hidden="true"
              className="flex size-[32px] shrink-0 items-center justify-center rounded-full border border-[#252b3b] bg-[#161b26] text-[#a3acc2]"
            >
              <User className="size-3.5" />
            </div>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <p className="truncate font-mono text-[13px] font-semibold text-[#e2e8f0]">
                  {connection.account}
                </p>
                <span className="rounded-[4px] border border-[#252b3b] bg-[#0a0d14] px-1.5 py-0.5 font-mono text-[9px] leading-[12px] text-[#7c839b]">
                  PAT: {connection.tokenMask}
                </span>
              </div>
              <p className="mt-0.5 text-[11px] leading-[16px] text-[#7c839b]">
                Personal Access Token &bull; Read-only telemetry access
              </p>
            </div>
          </div>
          <Badge tone="green" dot>
            Active
          </Badge>
        </section>

        {/* Webhook payload URL */}
        <section>
          <div className="flex items-center justify-between gap-2">
            <SectionLabel>Webhook payload URL</SectionLabel>
            {connection.webhookVerified && (
              <span className="inline-flex items-center gap-1 font-mono text-[10px] text-[#22d3ee]">
                HMAC-SHA256 verified
              </span>
            )}
          </div>
          <p className="mt-2 text-[11px] leading-[16px] text-[#7c839b]">
            Configure this webhook URL in your GitHub repository or organization
            settings with HMAC-SHA256 verification.
          </p>
          <div className="mt-3 flex items-stretch gap-2">
            <input
              readOnly
              aria-label="Webhook payload URL"
              value={connection.webhookUrl}
              onFocus={(e) => e.currentTarget.select()}
              className="h-[36px] min-w-0 flex-1 rounded-[6px] border border-[#252b3b] bg-[#0a0d14] px-3 font-mono text-[11px] text-[#e2e8f0] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00e599]/40"
            />
            <button
              type="button"
              onClick={copyUrl}
              className="inline-flex h-[36px] shrink-0 items-center gap-1.5 rounded-[6px] border border-[#252b3b] bg-[#0f131d] px-3 text-[11px] font-medium text-[#e2e8f0] transition-colors hover:border-[#7c839b]/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00e599]"
            >
              {copied ? (
                <Check className="size-3 text-[#00e599]" aria-hidden="true" />
              ) : (
                <Copy className="size-3" aria-hidden="true" />
              )}
              <span aria-live="polite">{copied ? "Copied" : "Copy URL"}</span>
            </button>
          </div>
        </section>

        {/* Sync status metrics */}
        <section>
          <SectionLabel>Sync status metrics</SectionLabel>
          <ul className="mt-3 grid gap-2 sm:grid-cols-3">
            <MetricCell label="PAT Token" status="Validated" />
            <MetricCell
              label="Webhook Verification"
              status={connection.webhookVerified ? "Active" : "Pending"}
            />
            <MetricCell label="Event Ingestion" status="Syncing" />
          </ul>
        </section>

        {/* Zero-trust note */}
        <p className="flex items-center gap-2 rounded-[6px] border border-[#1f2433] bg-[#0f131d] px-3 py-2.5 text-[10px] leading-[14px] text-[#a3acc2]">
          <ShieldCheck
            className="size-3.5 shrink-0 text-[#00e599]"
            aria-hidden="true"
          />
          Zero-Trust architecture: Webhooks cryptographically verified via
          secret signature.
        </p>
      </div>
    </ModalShell>
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
  const [token, setToken] = useState("");
  const [webhookSecret, setWebhookSecret] = useState("");
  const [connection, setConnection] = useState<GitHubConnection | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Reset every time the dialog is opened; abort anything in flight on close
  useEffect(() => {
    if (open) {
      setStep("intro");
      setProgress(0);
      setToken("");
      setWebhookSecret("");
      setConnection(null);
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
      const result = await connectGitHub(
        token.trim(),
        webhookSecret,
        controller.signal,
      );
      if (controller.signal.aborted) return;
      // The raw credentials are no longer needed once the backend has them
      setToken("");
      setWebhookSecret("");
      setConnection(result);
      setProgress(100);
      setStep("connected");
    } catch {
      if (controller.signal.aborted) return;
      setStep("failed");
    }
  }, [token, webhookSecret]);

  // Cancel returns to the form with the entered values intact
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
      return (
        <IntroStep
          token={token}
          webhookSecret={webhookSecret}
          onTokenChange={setToken}
          onWebhookSecretChange={setWebhookSecret}
          onClose={close}
          onConnect={start}
        />
      );
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
      return connection ? (
        <ConnectedStep
          connection={connection}
          onClose={close}
          onDone={() => {
            onConnected?.(connection);
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
