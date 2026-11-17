"use client";

import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

export interface FilterDropdownOption<T extends string = string> {
  value: T;
  label: string;
  triggerText?: string;
  count?: number;
  dotClassName?: string;
}

interface FilterDropdownProps<T extends string = string> {
  value: T;
  onChange: (value: T) => void;
  options: readonly FilterDropdownOption<T>[];
  headerLabel: string;
  triggerLabel: string;
  ariaLabel: string;
  selectId?: string;
  testId?: string;
  triggerTestId?: string;
  popoverTestId?: string;
  isActive?: boolean;
  align?: "left" | "right";
  popoverWidthClassName?: string;
  triggerClassName?: string;
  className?: string;
  disabled?: boolean;
}

/**
 * Dark-themed custom dropdown matching the Access & Roles popover design,
 * paired with a visually hidden native <select> for form/E2E compatibility.
 */
export function FilterDropdown<T extends string = string>({
  value,
  onChange,
  options,
  headerLabel,
  triggerLabel,
  ariaLabel,
  selectId,
  testId,
  triggerTestId,
  popoverTestId,
  isActive = false,
  align = "right",
  popoverWidthClassName = "w-[248px]",
  triggerClassName,
  className,
  disabled = false,
}: FilterDropdownProps<T>) {
  const [open, setOpen] = useState(false);
  const [flipUp, setFlipUp] = useState(false);

  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const optionRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const listboxId = useId();
  const labelId = useId();

  useLayoutEffect(() => {
    if (!open || !triggerRef.current) {
      setFlipUp(false);
      return;
    }
    const rect = triggerRef.current.getBoundingClientRect();
    const spaceBelow = window.innerHeight - rect.bottom;
    const spaceAbove = rect.top;
    setFlipUp(spaceBelow < 220 && spaceAbove > spaceBelow);
  }, [open]);

  useEffect(() => {
    if (!open) return;

    function handlePointerDown(event: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        setOpen(false);
        triggerRef.current?.focus();
      }
    }

    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown, true);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown, true);
    };
  }, [open]);

  const focusOptionAt = (index: number) => {
    if (options.length === 0) return;
    const bounded = (index + options.length) % options.length;
    optionRefs.current[bounded]?.focus();
  };

  const openAndFocus = (index?: number) => {
    if (disabled) return;
    setOpen(true);
    const selectedIndex = options.findIndex((opt) => opt.value === value);
    const targetIndex =
      typeof index === "number"
        ? index
        : selectedIndex >= 0
          ? selectedIndex
          : 0;
    window.requestAnimationFrame(() => focusOptionAt(targetIndex));
  };

  const handleTriggerKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (disabled) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      if (!open) {
        openAndFocus();
      } else {
        focusOptionAt(0);
      }
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      if (!open) {
        openAndFocus(options.length - 1);
      } else {
        focusOptionAt(options.length - 1);
      }
    }
  };

  const handleOptionKeyDown = (
    event: React.KeyboardEvent<HTMLButtonElement>,
    index: number,
  ) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      focusOptionAt(index + 1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      focusOptionAt(index - 1);
    } else if (event.key === "Home") {
      event.preventDefault();
      focusOptionAt(0);
    } else if (event.key === "End") {
      event.preventDefault();
      focusOptionAt(options.length - 1);
    } else if (event.key === "Tab") {
      setOpen(false);
    }
  };

  const highlighted = open || isActive;

  return (
    <div ref={rootRef} className={cn("relative", className)}>
      {/* Native visually-hidden select preserved for form/E2E compatibility */}
      <select
        data-testid={testId}
        value={value}
        onChange={(e) => onChange(e.target.value as T)}
        disabled={disabled}
        tabIndex={-1}
        aria-label={ariaLabel}
        className="sr-only"
      >
        {options.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.triggerText ?? opt.label}
            {typeof opt.count === "number" ? ` (${opt.count})` : ""}
          </option>
        ))}
      </select>

      <button
        ref={triggerRef}
        id={selectId}
        type="button"
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listboxId : undefined}
        aria-label={ariaLabel}
        data-testid={triggerTestId}
        onClick={() => {
          if (disabled) return;
          setOpen((prev) => !prev);
        }}
        onKeyDown={handleTriggerKeyDown}
        className={cn(
          "flex h-[30px] items-center justify-between gap-[6px] rounded-[6px] border pl-[10px] pr-[9px] font-sans text-[12px] font-medium leading-normal transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[#00e599] disabled:cursor-not-allowed disabled:opacity-50",
          highlighted
            ? "border-[rgba(0,229,153,0.55)] bg-[rgba(0,229,153,0.1)] text-[#00e599]"
            : "border-[#262d3d] bg-[#0a0e18] text-[#cbd5e1] hover:border-[#64748b]/50",
          triggerClassName,
        )}
      >
        <span className="truncate whitespace-nowrap">{triggerLabel}</span>
        <ChevronDown
          className={cn(
            "size-[12px] shrink-0 transition-transform duration-200",
            highlighted ? "text-[#00e599]" : "text-[#94a3b8]",
            open && "rotate-180",
          )}
          aria-hidden="true"
        />
      </button>

      {open && (
        <div
          id={listboxId}
          role="listbox"
          aria-labelledby={labelId}
          data-testid={popoverTestId}
          className={cn(
            "absolute z-50 flex max-h-[260px] flex-col gap-[2px] overflow-y-auto rounded-[10px] border border-[#2e374b] bg-[#171b26] px-[6px] py-[8px] shadow-[0px_14px_34px_rgba(0,0,0,0.65)]",
            align === "left" ? "left-0" : "right-0",
            flipUp ? "bottom-[calc(100%+6px)]" : "top-[calc(100%+6px)]",
            popoverWidthClassName,
          )}
        >
          <div id={labelId} className="px-[8px] pb-[4px] pt-[2px]">
            <span className="font-mono text-[9.5px] font-bold uppercase leading-[13px] tracking-[0.2px] text-[#64748b]">
              {headerLabel}
            </span>
          </div>

          {options.map((opt, idx) => {
            const isSelected = value === opt.value;
            return (
              <button
                key={opt.value}
                ref={(el) => {
                  optionRefs.current[idx] = el;
                }}
                type="button"
                role="option"
                aria-selected={isSelected}
                onKeyDown={(e) => handleOptionKeyDown(e, idx)}
                onClick={() => {
                  onChange(opt.value);
                  setOpen(false);
                  triggerRef.current?.focus();
                }}
                className={cn(
                  "flex min-h-[27px] w-full items-center justify-between rounded-[6px] px-[8px] py-[5px] text-left transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[#00e599]",
                  isSelected
                    ? "bg-[rgba(0,229,153,0.12)]"
                    : "hover:bg-[rgba(248,250,252,0.06)]",
                )}
              >
                <div className="flex min-w-0 items-center gap-[7px] pr-2">
                  {isSelected ? (
                    <svg
                      width="12"
                      height="12"
                      viewBox="0 0 12 12"
                      fill="none"
                      aria-hidden="true"
                      className="shrink-0"
                    >
                      <path
                        d="M2.625 6.375L4.875 8.625L9.375 3.375"
                        stroke="#00E599"
                        strokeWidth="1.2"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  ) : (
                    <span
                      className={cn(
                        "size-[6px] shrink-0 rounded-full",
                        opt.dotClassName ?? "bg-[#64748b]",
                      )}
                      aria-hidden="true"
                    />
                  )}
                  <span
                    className={cn(
                      "truncate font-sans text-[12px] leading-[15px]",
                      isSelected
                        ? "font-semibold text-[#00e599]"
                        : "font-medium text-[#e2e8f0]",
                    )}
                  >
                    {opt.label}
                  </span>
                </div>

                {typeof opt.count === "number" && (
                  <span className="shrink-0 font-mono text-[11px] font-medium leading-[15px] text-[#64748b]">
                    {opt.count}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
