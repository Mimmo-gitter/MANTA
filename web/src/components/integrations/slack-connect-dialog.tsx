"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import {
  ArrowRight,
  Check,
  CheckCircle2,
  Eye,
  EyeOff,
  ShieldCheck,
} from "lucide-react";
import { cn } from "@/lib/utils";
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

export interface SlackConnection {
  /** Reserved for the real response (workspace name, channels, etc.). */
  workspace?: string;
}

interface SlackConnectDialogProps {
  open: boolean;
  onClose: () => void;
  /** Called when the user presses "Done" on the success screen. */
  onConnected?: (connection: SlackConnection) => void;
}

const TOKEN_PREFIX = "xoxb-";

const PERMISSIONS = [
  "Post critical notifications and delivery threshold alerts to configured channels",
  "Correlate release incident discussions and team signal threads",
];

/**
 * TODO: replace with the real flow.
 * Send the bot token and signing secret to your backend over HTTPS. The backend
 * validates the token with Slack (auth.test), stores both credentials
 * encrypted, configures event access and registers the webhook.
 *
 * This mock resolves after a delay. Add `?slackError=1` to the page URL to see
 * the failure state.
 */
async function connectSlack(
  _botToken: string,
  _signingSecret: string,
  signal: AbortSignal,
): Promise<SlackConnection> {
  await new Promise<void>((resolve, reject) => {
    const timer = window.setTimeout(resolve, 2800);
    signal.addEventListener("abort", () => {
      window.clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    });
  });

  if (window.location.search.includes("slackError=1")) {
    throw new Error("Slack authorization could not be verified");
  }
  return {};
}

/* -------------------------------------------------------------------------- */
/* Slack mark                                                                 */
/* -------------------------------------------------------------------------- */

function SlackMark({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      aria-hidden="true"
      className={className}
      fill="none"
    >
      {/* Cyan */}
      <rect x="8" y="2" width="4" height="9" rx="2" fill="#36C5F0" />
      <rect x="2" y="8" width="9" height="4" rx="2" fill="#36C5F0" />

      {/* Green */}
      <rect x="12" y="2" width="4" height="9" rx="2" fill="#2EB67D" />
      <rect x="13" y="8" width="9" height="4" rx="2" fill="#2EB67D" />

      {/* Yellow */}
      <rect x="12" y="13" width="4" height="9" rx="2" fill="#ECB22E" />
      <rect x="13" y="12" width="9" height="4" rx="2" fill="#ECB22E" />

      {/* Red / Pink */}
      <rect x="8" y="13" width="4" height="9" rx="2" fill="#E01E5A" />
      <rect x="2" y="12" width="9" height="4" rx="2" fill="#E01E5A" />
    </svg>
  );
}

const ICON = <SlackMark className="size-[20px]" />;

/* -------------------------------------------------------------------------- */
/* Step 1: intro (credentials form)                                           */
/* -------------------------------------------------------------------------- */

const LINK_CLASS =
  "rounded-[2px] text-[#67e8f9] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00e599]";

function ExternalHelpLink({
  href,
  children,
}: {
  href: string;
  children: React.ReactNode;
}) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className={LINK_CLASS}
    >
      {children}
      <span className="sr-only"> (opens in a new tab)</span>
    </a>
  );
}

function SecretField({
  id,
  label,
  hint,
  placeholder,
  helper,
  error,
  value,
  onChange,
  revealLabel,
}: {
  id: string;
  label: string;
  hint: string;
  placeholder: string;
  helper: React.ReactNode;
  error?: string;
  value: string;
  onChange: (value: string) => void;
  revealLabel: string;
}) {
  const [visible, setVisible] = useState(false);
  const helperId = `${id}-helper`;
  const errorId = `${id}-error`;

  return (
    <div>
      <div className="mb-2 flex items-baseline justify-between gap-3">
        <label
          htmlFor={id}
          className="text-[13px] font-medium leading-[18px] text-[#e2e8f0]"
        >
          {label}
          <span className="text-[#ef4444]" aria-hidden="true">
            {" "}
            *
          </span>
        </label>
        <span
          aria-hidden="true"
          className="font-mono text-[10px] leading-[14px] text-[#7c839b]"
        >
          {hint}
        </span>
      </div>
      <div className="relative">
        <input
          id={id}
          type={visible ? "text" : "password"}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          required
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${helperId} ${errorId}` : helperId}
          autoComplete="off"
          autoCapitalize="off"
          spellCheck={false}
          className={cn(
            "h-[38px] w-full rounded-[6px] border bg-[#0a0d14] pl-3.5 pr-10 font-mono text-[12px] text-[#e2e8f0] placeholder:text-[#4b5266] focus-visible:outline-none focus-visible:ring-2",
            error
              ? "border-[#ef4444]/60 focus-visible:ring-[#ef4444]/40"
              : "border-[#252b3b] focus-visible:border-[#00e599]/60 focus-visible:ring-[#00e599]/40",
          )}
        />
        <button
          type="button"
          onClick={() => setVisible((v) => !v)}
          aria-label={`${visible ? "Hide" : "Show"} ${revealLabel}`}
          aria-pressed={visible}
          className="absolute right-2 top-1/2 flex size-[26px] -translate-y-1/2 items-center justify-center rounded-[6px] text-[#a3acc2] transition-colors hover:text-[#e2e8f0] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00e599]"
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
        className="mt-2 text-[11px] leading-[16px] text-[#7c839b]"
      >
        {helper}
      </p>
      {error && (
        <p
          id={errorId}
          role="alert"
          className="mt-1 text-[11px] leading-[16px] text-[#ef4444]"
        >
          {error}
        </p>
      )}
    </div>
  );
}

function IntroStep({
  botToken,
  signingSecret,
  onBotTokenChange,
  onSigningSecretChange,
  onClose,
  onConnect,
}: {
  botToken: string;
  signingSecret: string;
  onBotTokenChange: (value: string) => void;
  onSigningSecretChange: (value: string) => void;
  onClose: () => void;
  onConnect: () => void;
}) {
  const titleId = useId();
  const formId = useId();
  const tokenId = useId();
  const secretId = useId();

  const token = botToken.trim();
  // Only flag the format once the prefix has been fully typed
  const tokenError =
    token.length >= TOKEN_PREFIX.length && !token.startsWith(TOKEN_PREFIX)
      ? `Bot tokens start with ${TOKEN_PREFIX}`
      : undefined;
  const canSubmit =
    token.startsWith(TOKEN_PREFIX) &&
    token.length > TOKEN_PREFIX.length &&
    signingSecret.trim().length > 0;

  return (
    <ModalShell
      size="xl"
      icon={ICON}
      titleId={titleId}
      title="Connect Slack"
      subtitle="Workspace integration for M.A.N.T.A."
      onClose={onClose}
      footer={
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="flex items-center gap-1.5 font-mono text-[10px] text-[#7c839b]">
            <Check
              className="size-3 shrink-0 text-[#00e599]"
              aria-hidden="true"
            />
            HMAC-SHA256 verified
          </p>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={onClose}
              className="inline-flex h-[32px] items-center rounded-[6px] border border-[#252b3b] bg-[#0f131d] px-3.5 text-[12px] font-medium text-[#e2e8f0] transition-colors hover:border-[#7c839b]/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00e599]"
            >
              Cancel
            </button>
            <PrimaryButton type="submit" form={formId} disabled={!canSubmit}>
              Connect Slack
              <ArrowRight className="size-3.5" aria-hidden="true" />
            </PrimaryButton>
          </div>
        </div>
      }
    >
      <form
        id={formId}
        onSubmit={(e) => {
          e.preventDefault();
          if (canSubmit) onConnect();
        }}
        className="flex flex-col gap-5 px-6 pb-6 pt-6"
      >
        <p className="text-[13px] leading-[20px] text-[#a3acc2]">
          Connect your Slack workspace to monitor communication bottlenecks,
          track delivery anomalies, and receive project alerts.
        </p>

        <SecretField
          id={tokenId}
          label="Bot User OAuth Token"
          hint="xoxb-format"
          placeholder="xoxb-..."
          helper={
            <>
              Find this token in your{" "}
              <ExternalHelpLink href="https://api.slack.com/apps">
                Slack App &rarr; OAuth &amp; Permissions
              </ExternalHelpLink>
              .
            </>
          }
          error={tokenError}
          value={botToken}
          onChange={onBotTokenChange}
          revealLabel="bot token"
        />

        <SecretField
          id={secretId}
          label="Signing Secret"
          hint="HMAC secret"
          placeholder="Enter your Slack signing secret"
          helper={
            <>
              Find this value in your{" "}
              <ExternalHelpLink href="https://api.slack.com/apps">
                Slack App &rarr; Basic Information &rarr; App Credentials
              </ExternalHelpLink>
              .
            </>
          }
          value={signingSecret}
          onChange={onSigningSecretChange}
          revealLabel="signing secret"
        />

        <section className="flex items-start gap-3 rounded-[8px] border border-[#252b3b] bg-[#161b26]/60 p-4">
          <ShieldCheck
            className="mt-0.5 size-4 shrink-0 text-[#22d3ee]"
            aria-hidden="true"
          />
          <div className="min-w-0">
            <h3 className="text-[13px] font-medium leading-[18px] text-[#e2e8f0]">
              Secure Connection
            </h3>
            <p className="mt-0.5 text-[11px] leading-[16px] text-[#7c839b]">
              Your credentials are securely transmitted using TLS 1.3 and stored
              with AES-256 RLS encryption by the M.A.N.T.A. backend.
            </p>
          </div>
        </section>

        <section aria-labelledby="slack-perm-heading">
          <h3 id="slack-perm-heading" className="sr-only">
            Requested permissions
          </h3>
          <div aria-hidden="true">
            <SectionLabel>Requested permissions</SectionLabel>
          </div>
          <ul className="mt-3 flex flex-col gap-2">
            {PERMISSIONS.map((permission) => (
              <li
                key={permission}
                className="flex items-start gap-2.5 text-[12px] leading-[18px] text-[#a3acc2]"
              >
                <Check
                  className="mt-[3px] size-3.5 shrink-0 text-[#00e599]"
                  aria-hidden="true"
                />
                <span>{permission}.</span>
              </li>
            ))}
          </ul>
        </section>
      </form>
    </ModalShell>
  );
}

/* -------------------------------------------------------------------------- */
/* Step 3: connected                                                          */
/* -------------------------------------------------------------------------- */

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
      <dd className="m-0">{children}</dd>
    </div>
  );
}

function ConnectedStep({
  onDone,
  onClose,
}: {
  onDone: () => void;
  onClose: () => void;
}) {
  const titleId = useId();

  return (
    <ModalShell
      size="md"
      icon={ICON}
      titleId={titleId}
      title="Slack Connected"
      subtitle="Your Slack workspace is connected to M.A.N.T.A."
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
          Slack Connected Successfully
        </h2>
        <p className="mt-0.5 max-w-[300px] text-center text-[11px] leading-[16px] text-[#7c839b]">
          Slack is connected for communication bottleneck analysis and alerts.
        </p>

        <dl className="mt-6 w-full overflow-hidden rounded-[8px] border border-[#1f2433] bg-[#0f131d]">
          <Row label="Connection">
            <Badge tone="green" dot>
              Connected
            </Badge>
          </Row>
          <Row label="Communication monitoring">
            <Badge tone="green" dot>
              Configured
            </Badge>
          </Row>
          <Row label="Webhook verification">
            <Badge tone="green" dot>
              Verified
            </Badge>
          </Row>
        </dl>
      </div>
    </ModalShell>
  );
}

/* -------------------------------------------------------------------------- */
/* Public component                                                           */
/* -------------------------------------------------------------------------- */

export function SlackConnectDialog({
  open,
  onClose,
  onConnected,
}: SlackConnectDialogProps) {
  const [step, setStep] = useState<Step>("intro");
  const [progress, setProgress] = useState(0);
  const [botToken, setBotToken] = useState("");
  const [signingSecret, setSigningSecret] = useState("");
  const [connection, setConnection] = useState<SlackConnection | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (open) {
      setStep("intro");
      setProgress(0);
      setBotToken("");
      setSigningSecret("");
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

  const start = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setProgress(8);
    setStep("connecting");

    try {
      const result = await connectSlack(
        botToken.trim(),
        signingSecret.trim(),
        controller.signal,
      );
      if (controller.signal.aborted) return;
      // The backend has the credentials now; don't keep them in the browser
      setBotToken("");
      setSigningSecret("");
      setConnection(result);
      setProgress(100);
      setStep("connected");
    } catch {
      if (controller.signal.aborted) return;
      setStep("failed");
    }
  }, [botToken, signingSecret]);

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
          botToken={botToken}
          signingSecret={signingSecret}
          onBotTokenChange={setBotToken}
          onSigningSecretChange={setSigningSecret}
          onClose={close}
          onConnect={start}
        />
      );
    case "connecting":
      return (
        <ConnectingStep
          icon={ICON}
          providerName="Slack"
          description="Verifying authorization and configuring event access."
          progress={progress}
          onCancel={cancel}
        />
      );
    case "connected":
      return (
        <ConnectedStep
          onClose={close}
          onDone={() => {
            onConnected?.(connection ?? {});
            close();
          }}
        />
      );
    case "failed":
      return (
        <FailedStep
          icon={ICON}
          providerName="Slack"
          onClose={close}
          onRetry={start}
        />
      );
  }
}
