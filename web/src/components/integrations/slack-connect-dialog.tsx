"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import {
  Check,
  CheckCircle2,
  ExternalLink,
  Info,
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

const PERMISSIONS = [
  "Post critical notifications and delivery threshold alerts to configured channels",
  "Correlate release incident discussions and team signal threads",
];

/**
 * TODO: replace with the real flow.
 * 1. "Connect Slack" should start the Slack OAuth authorization.
 * 2. After the redirect back, your backend verifies the authorization,
 *    configures event access and registers the webhook.
 *
 * This mock resolves after a delay. Add `?slackError=1` to the page URL to see
 * the failure state.
 */
async function connectSlack(signal: AbortSignal): Promise<SlackConnection> {
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
    <svg viewBox="0 0 24 24" aria-hidden="true" className={className}>
      <rect x="9" y="2" width="3" height="8" rx="1.5" fill="#36c5f0" />
      <rect x="14" y="9" width="8" height="3" rx="1.5" fill="#2eb67d" />
      <rect x="12" y="14" width="3" height="8" rx="1.5" fill="#ecb22e" />
      <rect x="2" y="12" width="8" height="3" rx="1.5" fill="#e01e5a" />
    </svg>
  );
}

const ICON = <SlackMark className="size-[18px]" />;

/* -------------------------------------------------------------------------- */
/* Step 1: intro                                                              */
/* -------------------------------------------------------------------------- */

function IntroStep({
  onClose,
  onConnect,
}: {
  onClose: () => void;
  onConnect: () => void;
}) {
  const titleId = useId();

  return (
    <ModalShell
      size="md"
      icon={ICON}
      titleId={titleId}
      title="Connect Slack"
      subtitle="Connect your Slack workspace to monitor communication bottlenecks and receive project alerts."
      onClose={onClose}
      footer={
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-[11px] text-[#7c839b]">
            Webhook security: HMAC-SHA256 verified
          </p>
          <PrimaryButton onClick={onConnect}>
            Connect Slack
            <ExternalLink className="size-3" aria-hidden="true" />
          </PrimaryButton>
        </div>
      }
    >
      <div className="flex flex-col gap-4 p-4">
        <section className="rounded-[8px] border border-[#1f2433] bg-[#0f131d] p-3.5">
          <h3 className="flex items-center gap-2 text-[12px] font-medium text-[#e2e8f0]">
            <Info className="size-3.5 text-[#00e599]" aria-hidden="true" />
            Slack Integration
          </h3>
          <p className="mt-2 text-[11px] leading-[17px] text-[#a3acc2]">
            M.A.N.T.A. will access only the data and permissions required for
            communication monitoring and alerts.
          </p>
        </section>

        <section aria-labelledby="slack-perm-heading">
          <h3 id="slack-perm-heading" className="sr-only">
            Requested permissions
          </h3>
          <div aria-hidden="true">
            <SectionLabel>Requested permissions</SectionLabel>
          </div>
          <ul className="mt-2.5 flex flex-col gap-2">
            {PERMISSIONS.map((permission) => (
              <li
                key={permission}
                className="flex items-start gap-2 text-[11px] leading-[16px] text-[#e2e8f0]"
              >
                <CheckCircle2
                  className="mt-px size-3.5 shrink-0 text-[#00e599]"
                  aria-hidden="true"
                />
                <span>{permission}</span>
              </li>
            ))}
          </ul>
        </section>
      </div>
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
            <Badge tone="amber" dot>
              Pending
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
  const [connection, setConnection] = useState<SlackConnection | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (open) {
      setStep("intro");
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

  const start = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setProgress(8);
    setStep("connecting");

    try {
      const result = await connectSlack(controller.signal);
      if (controller.signal.aborted) return;
      setConnection(result);
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
      return <IntroStep onClose={close} onConnect={start} />;
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
