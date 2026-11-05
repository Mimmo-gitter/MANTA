"use client";

import { useEffect, useId, useRef } from "react";
import { AlertCircle, RefreshCw, X } from "lucide-react";
import { cn } from "@/lib/utils";

/* -------------------------------------------------------------------------- */
/* Small shared elements                                                      */
/* -------------------------------------------------------------------------- */

export function Badge({
  tone,
  dot = false,
  children,
}: {
  tone: "green" | "amber" | "muted";
  dot?: boolean;
  children: React.ReactNode;
}) {
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-1.5 rounded-[4px] border px-1.5 py-0.5 font-mono text-[10px] leading-[14px]",
        tone === "green" &&
          "border-[#00e599]/25 bg-[#00e599]/10 text-[#00e599]",
        tone === "amber" &&
          "border-[#fbbf24]/25 bg-[#fbbf24]/10 text-[#fbbf24]",
        tone === "muted" && "border-[#252b3b] bg-[#0f131d] text-[#a3acc2]",
      )}
    >
      {dot && (
        <span
          aria-hidden="true"
          className={cn(
            "size-1.5 rounded-full",
            tone === "green" && "bg-[#00e599]",
            tone === "amber" && "bg-[#fbbf24]",
            tone === "muted" && "bg-[#a3acc2]",
          )}
        />
      )}
      {children}
    </span>
  );
}

export function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="font-mono text-[10px] font-semibold uppercase leading-[15px] tracking-[0.8px] text-[#a3acc2]">
      {children}
    </h3>
  );
}

export function PrimaryButton({
  className,
  children,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      {...props}
      className={cn(
        "inline-flex h-[32px] items-center justify-center gap-1.5 rounded-[6px] bg-[#00e599] px-3.5 text-[12px] font-semibold text-[#0f131d] transition-colors hover:bg-[#2df0b0] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00e599] focus-visible:ring-offset-2 focus-visible:ring-offset-[#131824] disabled:opacity-60",
        className,
      )}
    >
      {children}
    </button>
  );
}

/* -------------------------------------------------------------------------- */
/* Modal shell                                                                */
/* -------------------------------------------------------------------------- */

export function ModalShell({
  size,
  icon,
  title,
  subtitle,
  titleId,
  onClose,
  closeLabel = "Close dialog",
  dismissOnBackdrop = true,
  footer,
  children,
}: {
  size: "sm" | "md" | "lg";
  icon: React.ReactNode;
  title: string;
  subtitle?: string;
  titleId: string;
  onClose: () => void;
  closeLabel?: string;
  dismissOnBackdrop?: boolean;
  footer?: React.ReactNode;
  children: React.ReactNode;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);

  // Each step remounts the shell, so this focuses the dialog on every step change
  useEffect(() => {
    dialogRef.current?.focus();
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
        return;
      }
      if (e.key !== "Tab" || !dialogRef.current) return;
      const focusable = dialogRef.current.querySelectorAll<HTMLElement>(
        'button:not([disabled]), [href], input, [tabindex]:not([tabindex="-1"])',
      );
      if (focusable.length === 0) {
        e.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-[90] flex items-center justify-center bg-[#05070b]/80 p-4 backdrop-blur-[2px]"
      onMouseDown={(e) => {
        if (dismissOnBackdrop && e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={cn(
          "flex max-h-[calc(100vh-2rem)] w-full flex-col overflow-hidden rounded-[12px] border border-[#252b3b] bg-[#171b26] shadow-[0px_25px_50px_-12px_rgba(0,0,0,0.7)] outline-none",
          size === "lg"
            ? "h-[580px] max-w-[610px]"
            : size === "md"
              ? "h-auto max-w-[440px]"
              : "h-auto max-w-[400px]",
        )}
      >
        <header className="flex shrink-0 items-center justify-between gap-3 border-b border-[#1f2433] bg-[#161b26] px-6 py-4">
          <div className="flex min-w-0 items-center gap-3">
            <div
              aria-hidden="true"
              className="flex size-[40px] shrink-0 items-center justify-center rounded-[8px] border border-[#252b3b] bg-[#0a0d14]"
            >
              {icon}
            </div>
            <div className="min-w-0">
              <h2
                id={titleId}
                className={cn(
                  "truncate font-semibold text-[#e2e8f0]",
                  subtitle
                    ? "text-[16px] leading-[22px]"
                    : "text-[14px] leading-[20px]",
                )}
              >
                {title}
              </h2>
              {subtitle && (
                <p className="truncate text-[12px] leading-[17px] text-[#7c839b]">
                  {subtitle}
                </p>
              )}
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label={closeLabel}
            className="flex size-[28px] shrink-0 items-center justify-center rounded-[6px] text-[#7c839b] transition-colors hover:bg-white/[0.05] hover:text-[#e2e8f0] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00e599]"
          >
            <X className="size-4" aria-hidden="true" />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
          {children}
        </div>

        {footer && (
          <footer className="shrink-0 border-t border-[#1f2433] bg-[#161b26] px-6 py-4">
            {footer}
          </footer>
        )}
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* "Connecting…" and "Connection failed" screens (identical across providers) */
/* -------------------------------------------------------------------------- */

export function ConnectingStep({
  icon,
  providerName,
  description,
  progress,
  onCancel,
}: {
  icon: React.ReactNode;
  providerName: string;
  description: string;
  progress: number;
  onCancel: () => void;
}) {
  const titleId = useId();

  return (
    <ModalShell
      size="sm"
      icon={icon}
      titleId={titleId}
      title={`Connecting to ${providerName}`}
      onClose={onCancel}
      closeLabel="Cancel connection"
      dismissOnBackdrop={false}
      footer={
        <div className="flex justify-end">
          <button
            type="button"
            onClick={onCancel}
            className="inline-flex h-[28px] items-center rounded-[6px] border border-[#252b3b] bg-[#0f131d] px-3 text-[11px] font-medium text-[#e2e8f0] transition-colors hover:border-[#7c839b]/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00e599]"
          >
            Cancel
          </button>
        </div>
      }
    >
      <div
        role="status"
        aria-live="polite"
        className="flex flex-col items-center px-6 py-8 text-center"
      >
        <div
          aria-hidden="true"
          className="relative mb-5 flex size-[36px] items-center justify-center"
        >
          <div className="absolute inset-0 rounded-full border-2 border-[#00e599]/15 border-t-[#00e599] motion-safe:animate-spin" />
          <div className="size-1.5 rounded-full bg-[#00e599]" />
        </div>
        <h2 className="text-[16px] font-semibold leading-[24px] text-[#e2e8f0]">
          Connecting to {providerName}&hellip;
        </h2>
        <p className="mt-1 text-[11px] leading-[16px] text-[#7c839b]">
          {description}
        </p>

        <div
          role="progressbar"
          aria-label="Connection progress"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(progress)}
          className="mt-5 h-[3px] w-full overflow-hidden rounded-full bg-[#1f2433]"
        >
          <div
            className="h-full rounded-full bg-[#00e599] transition-[width] duration-500 ease-out"
            style={{ width: `${progress}%` }}
          />
        </div>
      </div>
    </ModalShell>
  );
}

export function FailedStep({
  icon,
  providerName,
  onClose,
  onRetry,
}: {
  icon: React.ReactNode;
  providerName: string;
  onClose: () => void;
  onRetry: () => void;
}) {
  const titleId = useId();

  return (
    <ModalShell
      size="sm"
      icon={icon}
      titleId={titleId}
      title="Connection Failed"
      onClose={onClose}
    >
      <div
        role="alert"
        className="flex flex-col items-center px-6 pb-5 pt-8 text-center"
      >
        <div
          aria-hidden="true"
          className="mb-4 flex size-[40px] items-center justify-center rounded-full border border-[#ef4444]/30 bg-[#ef4444]/10"
        >
          <AlertCircle className="size-[18px] text-[#ef4444]" />
        </div>
        <h2 className="text-[16px] font-semibold leading-[24px] text-[#e2e8f0]">
          Unable to connect to {providerName}
        </h2>
        <p className="mt-1 text-[11px] leading-[16px] text-[#7c839b]">
          Please check your settings and try again.
        </p>
        <PrimaryButton onClick={onRetry} className="mt-6 h-[34px] w-full">
          <RefreshCw className="size-3" aria-hidden="true" />
          Retry
        </PrimaryButton>
      </div>
    </ModalShell>
  );
}
