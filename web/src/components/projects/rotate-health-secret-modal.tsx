"use client";

import { useId } from "react";
import { AlertCircle, AlertTriangle, Loader2, RotateCw } from "lucide-react";
import {
  ModalCloseButton,
  ProjectModalShell,
} from "@/components/projects/project-modal-shell";
import { extractEndpointPath } from "@/lib/health-sources";

interface RotateHealthSecretModalProps {
  open: boolean;
  onClose: () => void;
  maskedSecret: string;
  probeSourceLabel: string;
  webhookUrl: string;
  isPending: boolean;
  errorMessage: string | null;
  onConfirm: () => void;
}

export function RotateHealthSecretModal({
  open,
  onClose,
  maskedSecret,
  probeSourceLabel,
  webhookUrl,
  isPending,
  errorMessage,
  onConfirm,
}: RotateHealthSecretModalProps) {
  const titleId = useId();
  const descId = useId();
  const endpointPath = extractEndpointPath(webhookUrl);

  const handleClose = () => {
    if (isPending) return;
    onClose();
  };

  return (
    <ProjectModalShell
      open={open}
      onClose={handleClose}
      titleId={titleId}
      descriptionId={descId}
      dismissLocked={isPending}
      accentTone="amber"
      maxWidthClassName="max-w-[500px]"
    >
      <div className="p-[22px]" data-testid="rotate-health-secret-modal">
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-center gap-2.5">
            <div className="flex size-[28px] shrink-0 items-center justify-center rounded-[7px] border border-[#f59e0b]/45 bg-[#23190c] text-[#f59e0b]">
              <AlertTriangle className="size-[15px]" aria-hidden="true" />
            </div>
            <h2
              id={titleId}
              className="text-[17px] font-semibold leading-[22px] text-[#f8fafc]"
            >
              Rotate HMAC Signing Secret?
            </h2>
          </div>

          <ModalCloseButton
            onClick={handleClose}
            disabled={isPending}
            label="Close Rotate HMAC Signing Secret dialog"
          />
        </div>

        <p
          id={descId}
          className="mt-[12px] text-[12px] leading-[18px] text-[#94a3b8]"
        >
          Generating a new HMAC secret immediately invalidates{" "}
          <code className="rounded bg-[#0a0e18] px-1 py-0.5 font-mono text-[11px] text-[#e2e8f0]">
            {maskedSecret}
          </code>
          . Incoming probes signed with the old secret will fail signature
          verification until you update {probeSourceLabel}.
        </p>

        <div className="mt-[14px] rounded-[10px] border border-[#92400e]/60 bg-[#23190c]/85 p-[12px]">
          <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.6px] text-[#f59e0b]">
            IMMEDIATE EFFECT
          </p>
          <ul className="mt-[6px] space-y-1 font-mono text-[11px] leading-[17px] text-[#cbd5e1]">
            <li className="truncate">
              • Target endpoint: ...{endpointPath}
            </li>
            <li>
              • Active recovery sessions &amp; historical records remain
              preserved
            </li>
          </ul>
        </div>

        {errorMessage && (
          <div
            role="alert"
            data-testid="rotate-secret-error"
            className="mt-[12px] flex items-start gap-[8px] rounded-[8px] border border-[#ef4444]/40 bg-[#ef4444]/10 px-[12px] py-[8px] text-[12px] text-[#fca5a5]"
          >
            <AlertCircle
              className="mt-[2px] size-[14px] shrink-0 text-[#ef4444]"
              aria-hidden="true"
            />
            <span>{errorMessage}</span>
          </div>
        )}

        <div className="mt-[18px] flex items-center justify-end gap-[8px] border-t border-[#1e293b] pt-[14px]">
          <button
            type="button"
            data-testid="rotate-secret-cancel-button"
            onClick={handleClose}
            disabled={isPending}
            className="h-[34px] rounded-[8px] border border-[#252b3b] bg-[#0f131d] px-[14px] text-[12px] font-medium text-[#cbd5e1] transition-colors hover:border-[#3b455e] hover:text-[#f8fafc] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#f59e0b] disabled:cursor-not-allowed disabled:opacity-50"
          >
            Cancel
          </button>

          <button
            type="button"
            data-testid="rotate-secret-confirm-button"
            onClick={onConfirm}
            disabled={isPending}
            className="inline-flex h-[34px] items-center gap-1.5 rounded-[8px] bg-[#f59e0b] px-[16px] text-[12px] font-semibold text-[#0a0e18] transition-colors hover:bg-[#fbbf24] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#f59e0b] disabled:cursor-not-allowed disabled:opacity-60"
          >
            {isPending ? (
              <>
                <Loader2 className="size-[13px] animate-spin" aria-hidden="true" />
                <span>Rotating...</span>
              </>
            ) : (
              <>
                <RotateCw className="size-[13px]" aria-hidden="true" />
                <span>Rotate Secret Now</span>
              </>
            )}
          </button>
        </div>
      </div>
    </ProjectModalShell>
  );
}
