"use client";

import { useEffect, useId, useRef, useState } from "react";
import { Check, ChevronDown, Loader2, Lock } from "lucide-react";
import { HEALTH_PROBE_SOURCES, getProbeSourceMeta } from "@/lib/health-sources";
import { cn } from "@/lib/utils";
import type { HealthProbeSourceId } from "@/types/project";

interface HealthProbeSourceSelectProps {
  value: HealthProbeSourceId;
  onChange: (nextValue: HealthProbeSourceId) => void;
  disabled?: boolean;
  readOnly?: boolean;
  isSaving?: boolean;
}

export function HealthProbeSourceSelect({
  value,
  onChange,
  disabled = false,
  readOnly = false,
  isSaving = false,
}: HealthProbeSourceSelectProps) {
  const [open, setOpen] = useState(false);
  const [focusedIndex, setFocusedIndex] = useState(() =>
    Math.max(
      0,
      HEALTH_PROBE_SOURCES.findIndex((item) => item.id === value),
    ),
  );
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const labelId = useId();
  const listboxId = useId();

  const activeMeta = getProbeSourceMeta(value);
  const isInteractive = !disabled && !readOnly && !isSaving;

  useEffect(() => {
    if (!open) return;

    const handlePointerDown = (event: PointerEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(event.target as Node)
      ) {
        setOpen(false);
      }
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };

    window.addEventListener("pointerdown", handlePointerDown);
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("pointerdown", handlePointerDown);
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [open]);

  const handleSelect = (nextId: HealthProbeSourceId) => {
    setOpen(false);
    if (nextId !== value && isInteractive) {
      onChange(nextId);
    }
    triggerRef.current?.focus();
  };

  const handleTriggerKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (!isInteractive) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!open) {
        const currentIdx = Math.max(
          0,
          HEALTH_PROBE_SOURCES.findIndex((item) => item.id === value),
        );
        setFocusedIndex(currentIdx);
        setOpen(true);
        return;
      }
      setFocusedIndex((prev) =>
        event.key === "ArrowDown"
          ? (prev + 1) % HEALTH_PROBE_SOURCES.length
          : (prev - 1 + HEALTH_PROBE_SOURCES.length) % HEALTH_PROBE_SOURCES.length,
      );
    } else if (event.key === "Enter" || event.key === " ") {
      if (open) {
        event.preventDefault();
        const option = HEALTH_PROBE_SOURCES[focusedIndex];
        if (option) {
          handleSelect(option.id);
        }
      }
    }
  };

  return (
    <div ref={containerRef} className="relative">
      <span id={labelId} className="sr-only">
        Health Probe Source
      </span>

      <button
        ref={triggerRef}
        type="button"
        data-testid="health-probe-source-trigger"
        aria-labelledby={labelId}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listboxId : undefined}
        disabled={!isInteractive}
        onClick={() => {
          if (!isInteractive) return;
          setFocusedIndex(
            Math.max(
              0,
              HEALTH_PROBE_SOURCES.findIndex((item) => item.id === value),
            ),
          );
          setOpen((prev) => !prev);
        }}
        onKeyDown={handleTriggerKeyDown}
        className={cn(
          "flex h-[40px] w-full items-center justify-between gap-2 rounded-[8px] border px-[12px] text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00e599]",
          open
            ? "border-[#00e599]/75 bg-[#0a0e18]"
            : "border-[#1e293b] bg-[#0a0e18] hover:border-[#334155]",
          !isInteractive && "cursor-not-allowed opacity-75 hover:border-[#1e293b]",
        )}
      >
        <span className="flex min-w-0 items-center gap-2">
          <span
            aria-hidden="true"
            className={cn(
              "size-[6px] shrink-0 rounded-full",
              readOnly ? "bg-[#64748b]" : "bg-[#00e599]",
            )}
          />
          <span
            className={cn(
              "truncate text-[12px] font-medium",
              readOnly ? "text-[#94a3b8]" : "text-[#e2e8f0]",
            )}
          >
            {activeMeta.label}
          </span>
        </span>

        {isSaving ? (
          <Loader2
            className="size-[13px] shrink-0 animate-spin text-[#00e599]"
            aria-hidden="true"
          />
        ) : readOnly ? (
          <Lock
            className="size-[13px] shrink-0 text-[#64748b]"
            aria-hidden="true"
          />
        ) : (
          <ChevronDown
            className={cn(
              "size-[14px] shrink-0 text-[#7c839b] transition-transform",
              open && "rotate-180 text-[#00e599]",
            )}
            aria-hidden="true"
          />
        )}
      </button>

      {open && isInteractive && (
        <div
          id={listboxId}
          role="listbox"
          data-testid="health-probe-source-menu"
          aria-label="Select health probe source"
          className="absolute right-0 top-[calc(100%+6px)] z-30 w-full min-w-[260px] rounded-[10px] border border-[#262d3d] bg-[#131722] p-1.5 shadow-[0_18px_40px_rgba(0,0,0,0.65)]"
        >
          <div className="px-2.5 py-1.5 font-mono text-[10px] font-semibold uppercase tracking-[0.6px] text-[#64748b]">
            SELECT HEALTH PROBE SOURCE
          </div>

          <div className="space-y-1">
            {HEALTH_PROBE_SOURCES.map((source, index) => {
              const isSelected = source.id === value;
              const isHighlighted = index === focusedIndex;

              return (
                <button
                  key={source.id}
                  type="button"
                  role="option"
                  aria-selected={isSelected}
                  data-testid={`health-probe-option-${source.id}`}
                  onMouseEnter={() => setFocusedIndex(index)}
                  onClick={() => handleSelect(source.id)}
                  className={cn(
                    "flex w-full items-center justify-between gap-2.5 rounded-[7px] border px-2.5 py-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00e599]",
                    isSelected
                      ? "border-[#00e599]/40 bg-[#06241d] text-[#00e599]"
                      : isHighlighted
                        ? "border-[#252b3b] bg-[#1a2030] text-[#f8fafc]"
                        : "border-transparent text-[#cbd5e1] hover:bg-white/[0.04]",
                  )}
                >
                  <div className="min-w-0">
                    <p
                      className={cn(
                        "truncate text-[12px] font-semibold leading-[16px]",
                        isSelected ? "text-[#00e599]" : "text-[#e2e8f0]",
                      )}
                    >
                      {source.label}
                    </p>
                    <p className="mt-0.5 truncate text-[11px] leading-[14px] text-[#7c839b]">
                      {source.subtitle}
                    </p>
                  </div>

                  {isSelected && (
                    <Check
                      className="size-[14px] shrink-0 text-[#00e599]"
                      aria-hidden="true"
                    />
                  )}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
