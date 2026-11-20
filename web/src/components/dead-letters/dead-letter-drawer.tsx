"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Check, Copy, Loader2, RotateCw, X } from "lucide-react";
import { DeadLetterProviderAvatar } from "@/components/dead-letters/provider-avatar";
import {
  extractDeadLetterMeta,
  formatQuarantinedDrawerTime,
} from "@/lib/dead-letters-api";
import { cn } from "@/lib/utils";
import type { DeadLetterItem } from "@/types/dead-letter";

type DrawerTab = "stack-trace" | "json-payload";

interface DeadLetterDrawerProps {
  item: DeadLetterItem | null;
  open: boolean;
  onClose: () => void;
  onRedrive: (item: DeadLetterItem) => void;
  isRedriving: boolean;
  hasFailedRetry: boolean;
}

function renderJsonHighlighted(jsonText: string) {
  const lines = jsonText.split("\n");
  return lines.map((line, idx) => {
    const keyValMatch = /^(\s*)"([^"]+)"(\s*:\s*)(.+)$/.exec(line);
    if (!keyValMatch) {
      const strOnlyMatch = /^(\s*)"([^"]*)"(,?)$/.exec(line);
      if (strOnlyMatch) {
        const [, indent, strVal, comma] = strOnlyMatch;
        return (
          <div key={idx} className="leading-[19px]">
            <span>{indent}</span>
            <span className="text-[#00e599]">&quot;{strVal}&quot;</span>
            <span className="text-[#94a3b8]">{comma}</span>
          </div>
        );
      }
      return (
        <div key={idx} className="leading-[19px] text-[#cbd5e1]">
          {line}
        </div>
      );
    }

    const [, indent, key, colon, rawVal] = keyValMatch;
    const trimmedVal = rawVal.trimEnd();
    const hasComma = trimmedVal.endsWith(",");
    const coreVal = hasComma ? trimmedVal.slice(0, -1) : trimmedVal;

    let valClass = "text-[#cbd5e1]";
    if (coreVal.startsWith('"')) {
      valClass = "text-[#00e599]";
    } else if (/^-?\d+(?:\.\d+)?$/.test(coreVal)) {
      valClass = "text-[#fde047]";
    } else if (coreVal === "true" || coreVal === "false" || coreVal === "null") {
      valClass = "text-[#38bdf8]";
    }

    return (
      <div key={idx} className="leading-[19px]">
        <span>{indent}</span>
        <span className="text-[#93c5fd]">&quot;{key}&quot;</span>
        <span className="text-[#94a3b8]">{colon}</span>
        <span className={valClass}>{coreVal}</span>
        {hasComma && <span className="text-[#94a3b8]">,</span>}
      </div>
    );
  });
}

export function DeadLetterDrawer({
  item,
  open,
  onClose,
  onRedrive,
  isRedriving,
  hasFailedRetry,
}: DeadLetterDrawerProps) {
  const [activeTab, setActiveTab] = useState<DrawerTab>("stack-trace");
  const [copied, setCopied] = useState(false);

  const dialogRef = useRef<HTMLDialogElement>(null);
  const copyTimerRef = useRef<number | null>(null);
  const titleId = useId();
  const stackTabPanelId = useId();
  const payloadTabPanelId = useId();

  useEffect(() => {
    setCopied(false);
    setActiveTab("stack-trace");
  }, [item?.id]);

  useEffect(() => {
    return () => {
      if (copyTimerRef.current !== null) {
        window.clearTimeout(copyTimerRef.current);
      }
    };
  }, []);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;

    if (open && item && !dialog.open) {
      dialog.showModal();
    } else if ((!open || !item) && dialog.open) {
      dialog.close();
    }
  }, [open, item]);

  // Fallback light-dismiss handler for browsers without native closedby="any" support (e.g., Safari)
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;

    if ("closedBy" in HTMLDialogElement.prototype) {
      return;
    }

    const handleBackdropClick = (event: MouseEvent) => {
      if (isRedriving || event.target !== dialog) return;
      const rect = dialog.getBoundingClientRect();
      const inside =
        rect.top <= event.clientY &&
        event.clientY <= rect.top + rect.height &&
        rect.left <= event.clientX &&
        event.clientX <= rect.left + rect.width;
      if (!inside) {
        onClose();
      }
    };

    dialog.addEventListener("click", handleBackdropClick);
    return () => {
      dialog.removeEventListener("click", handleBackdropClick);
    };
  }, [isRedriving, onClose]);

  const meta = useMemo(
    () => (item ? extractDeadLetterMeta(item) : null),
    [item],
  );

  if (!open || !item || !meta) {
    return null;
  }

  const displayedAttempts = hasFailedRetry
    ? item.total_attempts + 1
    : item.total_attempts;

  const handleCopy = async () => {
    const textToCopy =
      activeTab === "stack-trace"
        ? item.stack_trace || `${meta.shortErrorClass}: ${meta.errorSummaryMessage}`
        : meta.payloadJson;

    try {
      await navigator.clipboard.writeText(textToCopy);
    } catch {
      // Fallback for non-secure clipboard contexts in headless environments
      const textarea = document.createElement("textarea");
      textarea.value = textToCopy;
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand("copy");
      document.body.removeChild(textarea);
    }

    setCopied(true);
    if (copyTimerRef.current !== null) {
      window.clearTimeout(copyTimerRef.current);
    }
    copyTimerRef.current = window.setTimeout(() => setCopied(false), 2000);
  };

  return (
    <dialog
      ref={dialogRef}
      closedby={isRedriving ? "none" : "any"}
      aria-labelledby={titleId}
      data-testid="dead-letter-drawer"
      onCancel={(event) => {
        event.preventDefault();
        if (!isRedriving) {
          onClose();
        }
      }}
      onClose={() => {
        if (open) {
          onClose();
        }
      }}
      className="fixed inset-y-0 right-0 left-auto m-0 flex h-[100dvh] max-h-none w-full max-w-[560px] flex-col overflow-hidden border-y-0 border-r-0 border-l border-[#262d3d] border-solid bg-[#131722] p-0 text-[#f8fafc] shadow-[-12px_0px_36px_0px_rgba(3,5,10,0.65)] backdrop:bg-[rgba(6,9,17,0.72)] focus:outline-none"
    >
      {/* Header (72px — Figma 5C/5D) */}
      <div className="flex min-h-[72px] shrink-0 items-center justify-between gap-3 px-[20px] py-[16px]">
        <div className="flex min-w-0 items-center gap-[12px]">
          <DeadLetterProviderAvatar provider={meta.normalizedProvider} />
          <div className="flex min-w-0 flex-col gap-[3px]">
            <div className="flex flex-wrap items-center gap-[8px]">
              <h2
                id={titleId}
                className="truncate text-[15px] font-bold leading-normal text-[#f8fafc]"
              >
                {meta.eventType}
              </h2>
              {isRedriving ? (
                <span className="inline-flex items-center gap-[6px] rounded-full border border-[#06b6d4]/45 bg-[#06b6d4]/15 px-[9px] py-[3px] text-[11px] font-semibold leading-[14px] text-[#67e8f9]">
                  <span className="size-[6px] rounded-full bg-[#22d3ee]" aria-hidden="true" />
                  <span>Re-driving</span>
                </span>
              ) : item.is_resolved ? (
                <span className="inline-flex items-center gap-[6px] rounded-full border border-[#00e599]/35 bg-[#00e599]/12 px-[9px] py-[3px] text-[11px] font-semibold leading-[14px] text-[#00e599]">
                  <span className="size-[6px] rounded-full bg-[#00e599]" aria-hidden="true" />
                  <span>Re-driven</span>
                </span>
              ) : (
                <span className="inline-flex items-center gap-[6px] rounded-full border border-[#ff4d4d]/35 bg-[#ff4d4d]/10 px-[9px] py-[3px] text-[11px] font-semibold leading-[14px] text-[#fca5a5]">
                  <span className="size-[6px] rounded-full bg-[#ff4d4d]" aria-hidden="true" />
                  <span>Quarantined</span>
                </span>
              )}
            </div>
            <p className="truncate font-mono text-[11px] text-[#64748b]">
              {meta.deliveryShortId} · {meta.contextTarget}
            </p>
          </div>
        </div>

        <button
          type="button"
          onClick={onClose}
          disabled={isRedriving}
          aria-label="Close dead-letter inspector"
          data-testid="drawer-close-button"
          className="flex size-[30px] shrink-0 items-center justify-center rounded-[6px] border border-[#262d3d] bg-[#0e121b] text-[#94a3b8] transition-colors hover:border-[#64748b] hover:text-[#f8fafc] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[#00e599] disabled:opacity-50"
        >
          <X className="size-[14px]" aria-hidden="true" />
        </button>
      </div>

      <div className="h-px w-full shrink-0 bg-[#1e2636]" />

      {/* Scrollable Body */}
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto px-[20px] py-[14px]">
        {/* Metadata 2x2 Grid */}
        <div className="grid grid-cols-1 gap-[8px] sm:grid-cols-2">
          <div className="rounded-[8px] border border-[#1e2636] bg-[#111622] px-[12px] py-[9px]">
            <div className="font-mono text-[9.5px] font-bold uppercase text-[#64748b]">
              PROVIDER
            </div>
            <div className="mt-[3px] truncate text-[12px] font-medium text-[#f8fafc]">
              {meta.providerWebhookLabel}
            </div>
          </div>

          <div className="rounded-[8px] border border-[#1e2636] bg-[#111622] px-[12px] py-[9px]">
            <div className="font-mono text-[9.5px] font-bold uppercase text-[#64748b]">
              ERROR CLASS
            </div>
            <div className="mt-[3px] truncate font-mono text-[12px] font-medium text-[#fca5a5]">
              {meta.shortErrorClass}
            </div>
          </div>

          <div className="rounded-[8px] border border-[#1e2636] bg-[#111622] px-[12px] py-[9px]">
            <div className="font-mono text-[9.5px] font-bold uppercase text-[#64748b]">
              TOTAL ATTEMPTS
            </div>
            <div
              className={cn(
                "mt-[3px] font-mono text-[12px] font-medium",
                hasFailedRetry ? "text-[#fca5a5]" : "text-[#fbbf24]",
              )}
            >
              {displayedAttempts} of 5 exhausted
            </div>
          </div>

          <div className="rounded-[8px] border border-[#1e2636] bg-[#111622] px-[12px] py-[9px]">
            <div className="font-mono text-[9.5px] font-bold uppercase text-[#64748b]">
              QUARANTINED AT
            </div>
            <div className="mt-[3px] truncate font-mono text-[12px] font-medium text-[#cbd5e1]">
              {formatQuarantinedDrawerTime(item.quarantined_at)}
            </div>
          </div>
        </div>

        {/* Drawer Tabs Bar */}
        <div className="mt-[12px] flex h-[40px] shrink-0 items-center justify-between gap-2">
          <div role="tablist" aria-label="Inspector views" className="flex items-center gap-[6px]">
            <button
              type="button"
              role="tab"
              aria-selected={activeTab === "stack-trace"}
              aria-controls={stackTabPanelId}
              data-testid="drawer-tab-stack-trace"
              onClick={() => {
                setActiveTab("stack-trace");
                setCopied(false);
              }}
              className={cn(
                "flex h-[32px] items-center justify-center rounded-[6px] border px-[12px] text-[12px] transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[#00e599]",
                activeTab === "stack-trace"
                  ? "border-[#00e599]/50 bg-[#0b1917] font-semibold text-[#00e599]"
                  : "border-[#1e2636] bg-[#111622] font-medium text-[#94a3b8] hover:text-[#e2e8f0]",
              )}
            >
              Stack Trace
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={activeTab === "json-payload"}
              aria-controls={payloadTabPanelId}
              data-testid="drawer-tab-json-payload"
              onClick={() => {
                setActiveTab("json-payload");
                setCopied(false);
              }}
              className={cn(
                "flex h-[32px] items-center justify-center rounded-[6px] border px-[12px] text-[12px] transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[#00e599]",
                activeTab === "json-payload"
                  ? "border-[#00e599]/50 bg-[#0b1917] font-semibold text-[#00e599]"
                  : "border-[#1e2636] bg-[#111622] font-medium text-[#94a3b8] hover:text-[#e2e8f0]",
              )}
            >
              JSON Payload
            </button>
          </div>

          <span className="font-mono text-[10.5px] text-[#64748b]">
            {activeTab === "stack-trace"
              ? `${meta.stackFrameCount} ${meta.stackFrameCount === 1 ? "frame" : "frames"}`
              : `${meta.payloadBytes} bytes`}
          </span>
        </div>

        {/* Code Viewer Panel */}
        <div className="mt-[8px] flex min-h-[280px] flex-1 flex-col overflow-hidden rounded-[10px] border border-[#1e2636] bg-[#0e121b]">
          <div className="flex h-[36px] shrink-0 items-center justify-between border-b border-[#1e2636] bg-[#111622] px-[14px]">
            <span className="font-mono text-[10px] font-bold uppercase text-[#94a3b8]">
              {activeTab === "stack-trace"
                ? "ERROR TRACEBACK"
                : "WEBHOOK PAYLOAD · APPLICATION/JSON"}
            </span>
            <button
              type="button"
              onClick={() => void handleCopy()}
              data-testid="drawer-copy-button"
              className={cn(
                "inline-flex h-[24px] items-center gap-[5px] rounded-[5px] border px-[8px] text-[11px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[#00e599]",
                copied
                  ? "border-[#00e599]/45 bg-[#0b1917] text-[#00e599]"
                  : "border-[#262d3d] bg-[#161b26] text-[#cbd5e1] hover:border-[#64748b]",
              )}
            >
              {copied ? (
                <>
                  <Check className="size-[11px]" aria-hidden="true" />
                  <span>Copied</span>
                </>
              ) : (
                <>
                  <Copy className="size-[11px]" aria-hidden="true" />
                  <span>Copy</span>
                </>
              )}
            </button>
          </div>

          {activeTab === "stack-trace" ? (
            <div
              id={stackTabPanelId}
              role="tabpanel"
              data-testid="drawer-stack-trace-content"
              className="flex min-h-0 flex-1 flex-col gap-[10px] overflow-y-auto p-[14px]"
            >
              <div className="rounded-[6px] border border-[#ff4d4d]/35 bg-[#221014] px-[12px] py-[10px]">
                <div className="font-mono text-[11.5px] font-bold text-[#fca5a5]">
                  {meta.shortErrorClass}
                </div>
                <p className="mt-[3px] break-words font-mono text-[11px] leading-[16px] text-[#f8fafc]">
                  {meta.errorSummaryMessage}
                </p>
              </div>

              <pre
                tabIndex={0}
                className="m-0 overflow-x-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-[18px] text-[#cbd5e1] focus:outline-none focus-visible:ring-1 focus-visible:ring-[#00e599]"
              >
                <code>
                  {item.stack_trace?.trim()
                    ? `${item.stack_trace.trim()}\n\nRetry history: ${displayedAttempts} attempts failed (max retries reached)`
                    : `No stack traceback recorded.\n\nRetry history: ${displayedAttempts} attempts failed (max retries reached)`}
                </code>
              </pre>
            </div>
          ) : (
            <div
              id={payloadTabPanelId}
              role="tabpanel"
              data-testid="drawer-json-payload-content"
              className="flex min-h-0 flex-1 flex-col overflow-auto p-[14px]"
            >
              <pre
                tabIndex={0}
                className="m-0 font-mono text-[11.5px] focus:outline-none focus-visible:ring-1 focus-visible:ring-[#00e599]"
              >
                <code>{renderJsonHighlighted(meta.payloadJson)}</code>
              </pre>
            </div>
          )}
        </div>
      </div>

      {/* Footer (65px — Figma 5C/5D) */}
      <div className="flex min-h-[65px] shrink-0 items-center justify-between gap-3 border-t border-[#1e2636] bg-[#111622] px-[20px]">
        <span className="text-[11.5px] text-[#64748b]">
          Replays payload into ingestion queue
        </span>
        <div className="flex items-center gap-[10px]">
          <button
            type="button"
            onClick={onClose}
            disabled={isRedriving}
            className="flex h-[34px] items-center justify-center rounded-[8px] border border-[#262d3d] bg-[#161b26] px-[14px] text-[12px] font-medium text-[#cbd5e1] transition-colors hover:border-[#64748b] hover:text-[#f8fafc] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[#00e599] disabled:opacity-50"
          >
            Close
          </button>
          <button
            type="button"
            onClick={() => onRedrive(item)}
            disabled={isRedriving || item.is_resolved}
            data-testid="drawer-redrive-button"
            className={cn(
              "inline-flex h-[34px] items-center justify-center gap-[7px] rounded-[8px] pl-[14px] pr-[16px] text-[12.5px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00e599] focus-visible:ring-offset-2 focus-visible:ring-offset-[#111622]",
              item.is_resolved
                ? "cursor-not-allowed border border-[#222938] bg-[#0e121b] text-[#64748b]"
                : isRedriving
                  ? "cursor-wait border border-[#06b6d4]/45 bg-[#0b1720] text-[#67e8f9]"
                  : hasFailedRetry
                    ? "bg-[#ef4444] text-[#ffffff] hover:bg-[#dc2626]"
                    : "bg-[#00e599] text-[#05080f] hover:bg-[#2df0b0]",
            )}
          >
            {isRedriving ? (
              <>
                <Loader2 className="size-[13px] animate-spin" aria-hidden="true" />
                <span>Sending...</span>
              </>
            ) : item.is_resolved ? (
              <>
                <Check className="size-[13px]" aria-hidden="true" />
                <span>Done</span>
              </>
            ) : (
              <>
                <RotateCw className="size-[13px]" aria-hidden="true" />
                <span>{hasFailedRetry ? "Retry Re-drive" : "Re-drive Event"}</span>
              </>
            )}
          </button>
        </div>
      </div>
    </dialog>
  );
}
