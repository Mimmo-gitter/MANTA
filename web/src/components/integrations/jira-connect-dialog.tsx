"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import {
  Check,
  CheckCircle2,
  ExternalLink,
  Info,
  Link2,
  Eye,
  EyeOff,
  Lock,
  ShieldCheck,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { connectJira as connectJiraApi } from "@/lib/integrations-api";
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

export interface JiraConnection {
  siteUrl: string;
}

interface JiraConnectDialogProps {
  open: boolean;
  onClose: () => void;
  /** Called when the user presses "Done" on the success screen. */
  onConnected?: (connection: JiraConnection) => void;
}

const PERMISSIONS: {
  title: string;
  description: string;
  badge: "Read-only" | "Scoped";
}[] = [
  {
    title: "Read project and issue data.",
    description: "Issues, epics, and project metadata for delivery tracking.",
    badge: "Read-only",
  },
  {
    title: "Monitor issue status and work-in-progress (WIP).",
    description: "Status transitions used for sprint and WIP metrics.",
    badge: "Read-only",
  },
  {
    title: "Track changes to acceptance criteria after a sprint starts.",
    description: "Change history used for scope creep detection.",
    badge: "Read-only",
  },
  {
    title: "Access incident-related issue data where configured.",
    description: "Limited to the projects and issue types you select.",
    badge: "Scoped",
  },
];

/**
 * Atlassian Cloud sites live on https://<name>.atlassian.net.
 * Returns the normalised origin, or an error message for the field.
 */
function validateSiteUrl(
  raw: string,
): { ok: true; url: string } | { ok: false; message: string } {
  const value = raw.trim();
  if (!value) return { ok: false, message: "Enter your Jira site URL." };

  let parsed: URL;
  try {
    parsed = new URL(/^https?:\/\//i.test(value) ? value : `https://${value}`);
  } catch {
    return {
      ok: false,
      message: "Enter a valid URL, e.g. https://instance.atlassian.net",
    };
  }

  const host = parsed.hostname.toLowerCase();
  if (
    parsed.protocol !== "https:" ||
    !/^[a-z0-9-]+\.atlassian\.net$/.test(host)
  ) {
    return {
      ok: false,
      message:
        "Use your Atlassian Cloud URL, e.g. https://instance.atlassian.net",
    };
  }
  return { ok: true, url: `https://${host}` };
}

async function connectJira(
  siteUrl: string,
  email: string,
  apiToken: string,
  webhookSecret: string,
  signal: AbortSignal,
): Promise<JiraConnection> {
  if (signal.aborted) {
    throw new DOMException("Aborted", "AbortError");
  }

  await connectJiraApi({
    base_url: siteUrl,
    email: email.trim(),
    api_token: apiToken,
    ...(webhookSecret.trim() ? { webhook_secret: webhookSecret.trim() } : {}),
  });

  if (signal.aborted) {
    throw new DOMException("Aborted", "AbortError");
  }

  return {
    siteUrl,
  };
}

/* -------------------------------------------------------------------------- */
/* Jira mark                                                                  */
/* -------------------------------------------------------------------------- */

function JiraMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className={className}>
      <path
        fill="#2684ff"
        d="M11.571 11.513H0a5.218 5.218 0 0 0 5.232 5.215h2.13v2.057A5.215 5.215 0 0 0 12.575 24V12.518a1.005 1.005 0 0 0-1.005-1.005zm5.723-5.756H5.736a5.215 5.215 0 0 0 5.215 5.214h2.129v2.058a5.218 5.218 0 0 0 5.215 5.214V6.758a1.001 1.001 0 0 0-1.001-1.001zM23.013 0H11.455a5.215 5.215 0 0 0 5.215 5.215h2.129v2.057A5.215 5.215 0 0 0 24 12.483V1.005A1.001 1.001 0 0 0 23.013 0Z"
      />
    </svg>
  );
}

const ICON = <JiraMark className="size-[16px]" />;
const SUBTITLE =
  "Connect your Jira projects to monitor delivery workflows and scope changes";

/* -------------------------------------------------------------------------- */
/* Step 1: intro + site URL                                                   */
/* -------------------------------------------------------------------------- */

function IntroStep({
  siteUrl,
  onSiteUrlChange,
  email,
  onEmailChange,
  apiToken,
  onApiTokenChange,
  webhookSecret,
  onWebhookSecretChange,
  showWebhookSecret,
  onToggleWebhookSecret,
  showApiToken,
  onToggleApiToken,
  onClose,
  onConnect,
}: {
  siteUrl: string;
  onSiteUrlChange: (value: string) => void;
  email: string;
  onEmailChange: (value: string) => void;
  apiToken: string;
  onApiTokenChange: (value: string) => void;
  webhookSecret: string;
  onWebhookSecretChange: (value: string) => void;
  showWebhookSecret: boolean;
  onToggleWebhookSecret: () => void;
  showApiToken: boolean;
  onToggleApiToken: () => void;
  onClose: () => void;
  onConnect: (validatedUrl: string) => void;
}) {
  const titleId = useId();
  const inputId = useId();
  const helpId = useId();
  const errorId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [credentialsError, setCredentialsError] = useState<string | null>(null);

  const submit = () => {
    if (!email.trim() || !apiToken.trim()) {
      setCredentialsError("Enter your Atlassian account email and API token.");
      return;
    }

    setCredentialsError(null);
    const result = validateSiteUrl(siteUrl);
    if (!result.ok) {
      setError(result.message);
      inputRef.current?.focus();
      return;
    }
    setError(null);
    onConnect(result.url);
  };

  return (
    <ModalShell
      size="lg"
      icon={ICON}
      titleId={titleId}
      title="Connect Jira"
      subtitle={SUBTITLE}
      onClose={onClose}
      footer={
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-[11px] text-[#7c839b]">
            Webhook security: HMAC-SHA256 verified
          </p>
          <PrimaryButton onClick={submit}>
            Connect Jira
            <ExternalLink className="size-3" aria-hidden="true" />
          </PrimaryButton>
        </div>
      }
    >
      <div className="flex flex-col gap-3 p-4">
        <section className="rounded-[8px] border border-[#1f2433] bg-[#0f131d] p-3.5">
          <h3 className="flex items-center gap-2 text-[12px] font-medium text-[#e2e8f0]">
            <Info className="size-3.5 text-[#00e599]" aria-hidden="true" />
            Jira Integration
          </h3>
          <p className="mt-2 text-[11px] leading-[17px] text-[#a3acc2]">
            M.A.N.T.A. receives Jira issue and project events through
            authenticated webhooks to keep sprint metrics and delivery tracking
            in continuous sync.
          </p>
        </section>

        {/* Site URL */}
        <div>
          <label
            htmlFor={inputId}
            className="text-[11px] font-medium text-[#e2e8f0]"
          >
            Jira Site URL{" "}
            <span className="text-[#ef4444]" aria-hidden="true">
              *
            </span>
            <span className="sr-only">(required)</span>
          </label>
          <div
            className={cn(
              "mt-1.5 flex h-[36px] items-center gap-2 rounded-[6px] border bg-[#0a0d14] px-2.5 transition-colors focus-within:ring-1",
              error
                ? "border-[#ef4444]/70 focus-within:border-[#ef4444] focus-within:ring-[#ef4444]"
                : "border-[#252b3b] focus-within:border-[#00e599] focus-within:ring-[#00e599]",
            )}
          >
            <Link2
              className="size-3.5 shrink-0 text-[#7c839b]"
              aria-hidden="true"
            />
            <input
              ref={inputRef}
              id={inputId}
              type="url"
              inputMode="url"
              autoComplete="url"
              spellCheck={false}
              value={siteUrl}
              onChange={(e) => {
                onSiteUrlChange(e.target.value);
                if (error) setError(null);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  submit();
                }
              }}
              placeholder="https://your-domain.atlassian.net"
              aria-required="true"
              aria-invalid={error ? true : undefined}
              aria-describedby={error ? `${helpId} ${errorId}` : helpId}
              className="w-full min-w-0 border-0 bg-transparent font-mono text-[11px] text-[#e2e8f0] outline-none ring-0 focus:border-0 focus:outline-none focus:ring-0 focus-visible:outline-none focus-visible:ring-0 placeholder:text-[#64748b]"
            />
          </div>
          {error && (
            <p
              id={errorId}
              role="alert"
              className="mt-1.5 text-[10px] leading-[14px] text-[#ef4444]"
            >
              {error}
            </p>
          )}
          <p
            id={helpId}
            className="mt-1.5 text-[10px] leading-[14px] text-[#7c839b]"
          >
            Enter your Atlassian Cloud organization URL (e.g.,
            https://instance.atlassian.net)
          </p>
        </div>

        {/* Permissions */}

        {/* Jira Email */}
        <div>
          <label className="text-[11px] font-medium text-[#e2e8f0]">
            Atlassian Account Email
            <span className="text-[#ef4444]"> *</span>
          </label>

          <input
            type="email"
            autoComplete="email"
            value={email}
            onChange={(e) => onEmailChange(e.target.value)}
            placeholder="admin@company.com"
            className="mt-1.5 h-[36px] w-full rounded-[6px] border border-[#252b3b] bg-[#0a0d14] px-2.5 font-mono text-[11px] text-[#e2e8f0] outline-none focus:border-[#00e599] focus:ring-1 focus:ring-[#00e599]"
          />
        </div>

        {/* Jira API Token */}
        <div>
          <label className="text-[11px] font-medium text-[#e2e8f0]">
            API Token
            <span className="text-[#ef4444]"> *</span>
          </label>

          <div className="relative mt-1.5">
            <input
              type={showApiToken ? "text" : "password"}
              autoComplete="off"
              value={apiToken}
              onChange={(e) => onApiTokenChange(e.target.value)}
              placeholder="Enter your Atlassian API token"
              className="h-[36px] w-full rounded-[6px] border border-[#252b3b] bg-[#0a0d14] py-2 pl-2.5 pr-9 font-mono text-[11px] text-[#e2e8f0] outline-none focus:border-[#00e599] focus:ring-1 focus:ring-[#00e599]"
            />

            <button
              type="button"
              onClick={onToggleApiToken}
              className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[#7c839b] transition-colors hover:text-[#00e599]"
              aria-label={showApiToken ? "Hide API token" : "Show API token"}
            >
              {showApiToken ? (
                <EyeOff className="size-4" />
              ) : (
                <Eye className="size-4" />
              )}
            </button>
          </div>

          <p className="mt-1.5 text-[10px] text-[#7c839b]">
            Generate an API token in your Atlassian account security settings.
          </p>

          {credentialsError && (
            <p
              role="alert"
              className="mt-1.5 text-[10px] leading-[14px] text-[#ef4444]"
            >
              {credentialsError}
            </p>
          )}
        </div>

        {/* Optional Webhook Secret */}
        <div>
          <label className="text-[11px] font-medium text-[#e2e8f0]">
            Webhook Secret
            <span className="ml-1 text-[#7c839b]">(Optional)</span>
          </label>

          <div className="relative mt-1.5">
            <input
              type={showWebhookSecret ? "text" : "password"}
              autoComplete="off"
              value={webhookSecret}
              onChange={(e) => onWebhookSecretChange(e.target.value)}
              placeholder="Enter webhook secret"
              className="h-[36px] w-full rounded-[6px] border border-[#252b3b] bg-[#0a0d14] py-2 pl-2.5 pr-9 font-mono text-[11px] text-[#e2e8f0] outline-none focus:border-[#00e599] focus:ring-1 focus:ring-[#00e599]"
            />

            <button
              type="button"
              onClick={onToggleWebhookSecret}
              className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[#7c839b] transition-colors hover:text-[#00e599]"
              aria-label={
                showWebhookSecret
                  ? "Hide webhook secret"
                  : "Show webhook secret"
              }
            >
              {showWebhookSecret ? (
                <EyeOff className="size-4" />
              ) : (
                <Eye className="size-4" />
              )}
            </button>
          </div>
        </div>

        <section
          aria-labelledby="jira-perm-heading"
          className="rounded-[8px] border border-[#1f2433] bg-[#0f131d] p-3.5"
        >
          <h3 id="jira-perm-heading" className="sr-only">
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
                <Badge tone={p.badge === "Scoped" ? "green" : "muted"}>
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
          <p className="text-[10px] leading-[15px] text-[#7c839b]">
            Incoming webhook events are verified using HMAC-SHA256 signatures.
            Webhook secrets are stored encrypted.
          </p>
        </section>
      </div>
    </ModalShell>
  );
}

/* -------------------------------------------------------------------------- */
/* Step 3: connected                                                          */
/* -------------------------------------------------------------------------- */

function ConnectedStep({
  connection,
  onDone,
  onClose,
}: {
  connection: JiraConnection;
  onDone: () => void;
  onClose: () => void;
}) {
  const titleId = useId();

  return (
    <ModalShell
      size="lg"
      icon={ICON}
      titleId={titleId}
      title="Jira Connected"
      subtitle={SUBTITLE}
      onClose={onClose}
      footer={
        <div className="flex items-center justify-between gap-3">
          <p className="flex items-center gap-1.5 font-mono text-[10px] text-[#7c839b]">
            <ShieldCheck
              className="size-3 shrink-0 text-[#00e599]"
              aria-hidden="true"
            />
            Webhook security: HMAC-SHA256
          </p>
          <PrimaryButton onClick={onDone}>
            <Check className="size-3.5" aria-hidden="true" />
            Done
          </PrimaryButton>
        </div>
      }
    >
      <div className="flex flex-col items-center px-4 pb-5 pt-7">
        <div
          aria-hidden="true"
          className="mb-4 flex size-[40px] items-center justify-center rounded-[10px] border border-[#00e599]/30 bg-[#00e599]/10"
        >
          <CheckCircle2 className="size-5 text-[#00e599]" />
        </div>
        <h2 className="text-[16px] font-semibold leading-[24px] text-[#e2e8f0]">
          Jira Connected Successfully
        </h2>
        <p className="mt-0.5 text-[11px] leading-[16px] text-[#7c839b]">
          Your Jira integration is ready.
        </p>

        <dl className="mt-6 w-full overflow-hidden rounded-[8px] border border-[#1f2433] bg-[#0f131d]">
          <Row label="Jira Site">
            <span className="max-w-full truncate rounded-[4px] bg-[#0a0d14] px-2 py-1 font-mono text-[11px] text-[#e2e8f0]">
              {connection.siteUrl}
            </span>
          </Row>
          <Row label="Connection">
            <Badge tone="green" dot>
              Connected
            </Badge>
          </Row>
          <Row label="Project synchronization">
            <Badge tone="green" dot>
              Syncing
            </Badge>
          </Row>
          <Row label="Webhook verification">
            <Badge tone="amber" dot>
              Pending
            </Badge>
          </Row>
        </dl>
      </div>
    </ModalShell>
  );
}

function Row({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-3 border-t border-[#1f2433] px-3.5 py-3 first:border-t-0">
      <dt className="text-[11px] text-[#e2e8f0]">{label}</dt>
      <dd className="m-0 min-w-0">{children}</dd>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Public component                                                           */
/* -------------------------------------------------------------------------- */

export function JiraConnectDialog({
  open,
  onClose,
  onConnected,
}: JiraConnectDialogProps) {
  const [step, setStep] = useState<Step>("intro");
  const [siteUrl, setSiteUrl] = useState("");
  const [email, setEmail] = useState("");
  const [apiToken, setApiToken] = useState("");
  const [webhookSecret, setWebhookSecret] = useState("");
  const [showApiToken, setShowApiToken] = useState(false);
  const [showWebhookSecret, setShowWebhookSecret] = useState(false);
  const [progress, setProgress] = useState(0);
  const [connection, setConnection] = useState<JiraConnection | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  // The validated URL is kept so Retry reuses it without re-typing
  const validatedUrlRef = useRef("");

  useEffect(() => {
    if (open) {
      setStep("intro");
      setSiteUrl("");
      setEmail("");
      setApiToken("");
      setShowApiToken(false);
      setWebhookSecret("");
      setProgress(0);
      setConnection(null);
    }
    return () => abortRef.current?.abort();
  }, [open]);

  useEffect(() => {
    if (step !== "connecting") return;
    const id = window.setInterval(() => {
      setProgress((p) => (p < 90 ? p + (90 - p) * 0.18 : p));
    }, 350);
    return () => window.clearInterval(id);
  }, [step]);

  const start = useCallback(
    async (url: string) => {
      validatedUrlRef.current = url;
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      setProgress(8);
      setStep("connecting");

      try {
        const result = await connectJira(
          url,
          email,
          apiToken,
          webhookSecret,
          controller.signal,
        );
        if (controller.signal.aborted) return;
        setConnection(result);
        setProgress(100);
        setStep("connected");
      } catch {
        if (controller.signal.aborted) return;
        setStep("failed");
      }
    },
    [email, apiToken, webhookSecret],
  );

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
          siteUrl={siteUrl}
          onSiteUrlChange={setSiteUrl}
          email={email}
          onEmailChange={setEmail}
          apiToken={apiToken}
          onApiTokenChange={setApiToken}
          webhookSecret={webhookSecret}
          onWebhookSecretChange={setWebhookSecret}
          onClose={close}
          showWebhookSecret={showWebhookSecret}
          showApiToken={showApiToken}
          onToggleApiToken={() => setShowApiToken((prev) => !prev)}
          onToggleWebhookSecret={() => setShowWebhookSecret((prev) => !prev)}
          onConnect={start}
        />
      );
    case "connecting":
      return (
        <ConnectingStep
          icon={ICON}
          providerName="Jira"
          description="Verifying connection and loading project access."
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
          icon={ICON}
          providerName="Jira"
          onClose={close}
          onRetry={() => void start(validatedUrlRef.current)}
        />
      );
  }
}
