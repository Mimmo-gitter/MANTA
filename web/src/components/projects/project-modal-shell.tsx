"use client";

import { useEffect, useRef } from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

interface ProjectModalShellProps {
  open: boolean;
  onClose: () => void;
  titleId: string;
  descriptionId?: string;
  dismissLocked?: boolean;
  maxWidthClassName?: string;
  accentTone?: "emerald" | "danger";
  children: React.ReactNode;
}

/**
 * Accessible native <dialog> wrapper using showModal(), closedby="any", and
 * a coordinate fallback for browsers without native closedBy support.
 */
export function ProjectModalShell({
  open,
  onClose,
  titleId,
  descriptionId,
  dismissLocked = false,
  maxWidthClassName = "max-w-[540px]",
  accentTone = "emerald",
  children,
}: ProjectModalShellProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;

    if (open && !dialog.open) {
      dialog.showModal();
    } else if (!open && dialog.open) {
      dialog.close();
    }
  }, [open]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;

    if ("closedBy" in HTMLDialogElement.prototype) {
      return;
    }

    const handleBackdropClick = (event: MouseEvent) => {
      if (dismissLocked || event.target !== dialog) return;
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
  }, [dismissLocked, onClose]);

  if (!open) {
    return null;
  }

  return (
    <dialog
      ref={dialogRef}
      closedby={dismissLocked ? "none" : "any"}
      aria-labelledby={titleId}
      aria-describedby={descriptionId}
      onCancel={(event) => {
        if (dismissLocked) {
          event.preventDefault();
          return;
        }
        event.preventDefault();
        onClose();
      }}
      onClose={() => {
        if (open) {
          onClose();
        }
      }}
      className={cn(
        "m-auto w-[calc(100vw-32px)] overflow-visible rounded-[14px] border bg-[#111622] p-0 text-[#f8fafc] shadow-[0px_25px_50px_-12px_rgba(0,0,0,0.75)] backdrop:bg-[#060911]/80 backdrop:backdrop-blur-[2px] focus:outline-none",
        accentTone === "danger"
          ? "border-[#ef4444]/40"
          : "border-[#1e293b]",
        maxWidthClassName,
      )}
    >
      <div className="relative">{children}</div>
    </dialog>
  );
}

export function ModalCloseButton({
  onClick,
  disabled = false,
  label = "Close modal",
}: {
  onClick: () => void;
  disabled?: boolean;
  label?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      className="flex size-[28px] shrink-0 items-center justify-center rounded-[6px] border border-[#252b3b] bg-[#0d121f] text-[#94a3b8] transition-colors hover:border-[#3b455e] hover:text-[#f8fafc] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00e599] disabled:cursor-not-allowed disabled:opacity-50"
    >
      <X className="size-[14px]" aria-hidden="true" />
    </button>
  );
}
