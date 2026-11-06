"use client";

import { useEffect, useId, useRef, useState } from "react";
import { AlertCircle, CheckCircle2, Loader2, Mail, Shield, UserPlus, X } from "lucide-react";
import { CANONICAL_ROLES, type Role } from "@/lib/permissions";
import { sendInvitation } from "@/lib/members-api";
import { cn } from "@/lib/utils";

export const ROLE_CONFIGS: Record<
  string,
  { label: string; dotColor: string; description: string }
> = {
  TENANT_ADMIN: {
    label: "Tenant Administrator",
    dotColor: "#00e599",
    description: "Full workspace management, member invitations, and access control.",
  },
  SOLUTION_ARCHITECT: {
    label: "Solution Architect",
    dotColor: "#60a5fa",
    description: "Architectural decision records, tech debt, and verification rules.",
  },
  PROJECT_MANAGER: {
    label: "Project Manager",
    dotColor: "#00c2cb",
    description: "Sprint oversight, WIP monitoring, and cross-project delivery metrics.",
  },
  SCRUM_MASTER: {
    label: "Scrum Master",
    dotColor: "#00c2cb",
    description: "Sprint WIP tracking, scope creep alerts, and agile telemetry.",
  },
  DELIVERY_DIRECTOR: {
    label: "Delivery Director",
    dotColor: "#a78bfa",
    description: "Portfolio overview, executive health indicators, and DORA telemetry.",
  },
  DEVELOPER: {
    label: "Developer",
    dotColor: "#cbd5e1",
    description: "Engineering telemetry, code quality verification, and ADR search.",
  },
  TOP_MANAGEMENT: {
    label: "Top Management",
    dotColor: "#fbbf24",
    description: "Executive high-level portfolio overview and organizational reporting.",
  },
};

/**
 * Validates email format according to RFC 5322 pattern.
 */
export function isValidEmail(email: string): boolean {
  if (!email || typeof email !== "string") return false;
  const trimmed = email.trim();
  const emailRegex =
    /^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+$/;
  return emailRegex.test(trimmed);
}

interface InviteMemberDialogProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
}

export function InviteMemberDialog({
  isOpen,
  onClose,
  onSuccess,
}: InviteMemberDialogProps) {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("DEVELOPER");
  const [touched, setTouched] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  const dialogRef = useRef<HTMLDivElement>(null);
  const emailInputRef = useRef<HTMLInputElement>(null);
  const titleId = useId();
  const errorId = useId();

  const isEmailValid = isValidEmail(email);
  const showEmailError = touched && (!email || !isEmailValid);
  const emailErrorMessage = !email
    ? "Email address is required"
    : "Please enter a valid email address (e.g. name@company.com)";

  // Reset state when dialog opens
  useEffect(() => {
    if (isOpen) {
      setEmail("");
      setRole("DEVELOPER");
      setTouched(false);
      setServerError(null);
      setSuccessMessage(null);
      setIsSubmitting(false);

      // Focus email input
      const timer = setTimeout(() => {
        emailInputRef.current?.focus();
      }, 50);
      return () => clearTimeout(timer);
    }
  }, [isOpen]);

  // Trap focus and handle Escape
  useEffect(() => {
    if (!isOpen) return;

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
        return;
      }
      if (e.key !== "Tab" || !dialogRef.current) return;

      const focusable = dialogRef.current.querySelectorAll<HTMLElement>(
        'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
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
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setTouched(true);

    if (!isEmailValid) {
      emailInputRef.current?.focus();
      return;
    }

    setIsSubmitting(true);
    setServerError(null);

    try {
      await sendInvitation({
        email: email.trim().toLowerCase(),
        role,
      });

      setSuccessMessage(`Invitation sent to ${email.trim()}`);
      onSuccess();
      setTimeout(() => {
        onClose();
      }, 900);
    } catch (err: unknown) {
      const msg =
        err instanceof Error
          ? err.message
          : "Failed to send invitation. Please try again.";
      setServerError(msg);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div
      role="presentation"
      className="fixed inset-0 z-[90] flex items-center justify-center overflow-y-auto bg-[#05070b]/80 p-4 backdrop-blur-[3px]"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !isSubmitting) onClose();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className="flex w-full max-w-[480px] flex-col overflow-hidden rounded-[14px] border border-[#252b3b] bg-[#131722] shadow-[0px_25px_50px_-12px_rgba(0,0,0,0.85)] outline-none"
      >
        {/* Header */}
        <header className="flex shrink-0 items-center justify-between border-b border-[#1f2433] bg-[#171b26] px-6 py-4">
          <div className="flex items-center gap-3">
            <div className="flex size-[38px] shrink-0 items-center justify-center rounded-[10px] border border-[rgba(0,229,153,0.3)] bg-[rgba(0,229,153,0.1)] text-[#00e599]">
              <UserPlus className="size-5" aria-hidden="true" />
            </div>
            <div>
              <h2
                id={titleId}
                className="font-['Plus_Jakarta_Sans:Bold'] text-[16px] font-bold leading-[22px] text-[#f8fafc]"
              >
                Invite Member
              </h2>
              <p className="font-['Plus_Jakarta_Sans:Regular'] text-[12px] leading-[16px] text-[#94a3b8]">
                Send an invitation link with an assigned role
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={isSubmitting}
            aria-label="Close dialog"
            className="flex size-[28px] items-center justify-center rounded-[6px] text-[#64748b] transition-colors hover:bg-white/[0.05] hover:text-[#e2e8f0] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00e599] disabled:opacity-50"
          >
            <X className="size-4" aria-hidden="true" />
          </button>
        </header>

        {/* Form Body */}
        <form onSubmit={handleSubmit} noValidate className="flex flex-col p-6">
          {/* Success Toast */}
          {successMessage && (
            <div
              role="status"
              className="mb-4 flex items-center gap-2.5 rounded-[8px] border border-[rgba(0,229,153,0.35)] bg-[rgba(0,229,153,0.12)] px-3.5 py-2.5 text-[12px] text-[#00e599]"
            >
              <CheckCircle2 className="size-4 shrink-0" aria-hidden="true" />
              <span>{successMessage}</span>
            </div>
          )}

          {/* Server Error Toast */}
          {serverError && (
            <div
              role="alert"
              className="mb-4 flex items-start gap-2.5 rounded-[8px] border border-[#ef4444]/30 bg-[#ef4444]/10 px-3.5 py-2.5 text-[12px] text-[#ef4444]"
            >
              <AlertCircle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
              <span>{serverError}</span>
            </div>
          )}

          {/* Email Field */}
          <div className="flex flex-col gap-1.5">
            <label
              htmlFor="invite-email-input"
              className="font-mono text-[11px] font-bold uppercase tracking-[0.45px] text-[#94a3b8]"
            >
              Email Address <span className="text-[#00e599]">*</span>
            </label>
            <div className="relative">
              <div className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3 text-[#64748b]">
                <Mail className="size-4" aria-hidden="true" />
              </div>
              <input
                ref={emailInputRef}
                id="invite-email-input"
                data-testid="invite-email-input"
                type="email"
                required
                value={email}
                onChange={(e) => {
                  setEmail(e.target.value);
                  if (touched) setTouched(true);
                }}
                onBlur={() => setTouched(true)}
                placeholder="colleague@company.com"
                aria-invalid={showEmailError ? "true" : "false"}
                aria-describedby={showEmailError ? errorId : undefined}
                className={cn(
                  "h-[40px] w-full rounded-[8px] border bg-[#0a0e18] pl-9 pr-3 text-[13px] text-[#f8fafc] placeholder-[#475569] transition-colors focus:outline-none focus:ring-1",
                  showEmailError
                    ? "border-[#ef4444] focus:border-[#ef4444] focus:ring-[#ef4444]"
                    : "border-[#262d3d] focus:border-[#00e599] focus:ring-[#00e599]",
                )}
              />
            </div>
            {showEmailError && (
              <p
                id={errorId}
                data-testid="invite-email-error"
                role="alert"
                className="flex items-center gap-1.5 text-[11px] text-[#ef4444]"
              >
                <AlertCircle className="size-3.5 shrink-0" aria-hidden="true" />
                <span>{emailErrorMessage}</span>
              </p>
            )}
          </div>

          {/* Role Selection Field */}
          <div className="mt-4 flex flex-col gap-1.5">
            <label
              htmlFor="invite-role-select"
              className="font-mono text-[11px] font-bold uppercase tracking-[0.45px] text-[#94a3b8]"
            >
              Assigned Role (SRS RBAC) <span className="text-[#00e599]">*</span>
            </label>
            <div className="relative">
              <div className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3 text-[#64748b]">
                <Shield className="size-4" aria-hidden="true" />
              </div>
              <select
                id="invite-role-select"
                data-testid="invite-role-select"
                value={role}
                onChange={(e) => setRole(e.target.value as Role)}
                className="h-[40px] w-full appearance-none rounded-[8px] border border-[#262d3d] bg-[#0a0e18] pl-9 pr-8 text-[13px] text-[#f8fafc] transition-colors focus:border-[#00e599] focus:outline-none focus:ring-1 focus:ring-[#00e599]"
              >
                {CANONICAL_ROLES.map((r) => {
                  const cfg = ROLE_CONFIGS[r] || { label: r, dotColor: "#cbd5e1" };
                  return (
                    <option key={r} value={r} className="bg-[#131722] text-[#f8fafc]">
                      {cfg.label}
                    </option>
                  );
                })}
              </select>
              <div className="pointer-events-none absolute inset-y-0 right-0 flex items-center pr-3 text-[#64748b]">
                <svg className="size-4" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                  <path
                    fillRule="evenodd"
                    d="M5.23 7.21a.75.75 0 011.06.02L10 10.94l3.71-3.71a.75.75 0 111.06 1.06l-4.24 4.25a.75.75 0 01-1.06 0L5.21 8.27a.75.75 0 01.02-1.06z"
                    clipRule="evenodd"
                  />
                </svg>
              </div>
            </div>
            {ROLE_CONFIGS[role] && (
              <p className="text-[11px] leading-[15px] text-[#64748b]">
                {ROLE_CONFIGS[role].description}
              </p>
            )}
          </div>

          {/* Invitation Notice */}
          <div className="mt-5 rounded-[8px] border border-[#1e2636] bg-[#0a0e18]/60 p-3 text-[11px] leading-[16px] text-[#94a3b8]">
            <p className="font-['Plus_Jakarta_Sans:Regular']">
              The recipient will receive an onboarding email containing a single-use token valid for <span className="font-semibold text-[#f8fafc]">48 hours</span>.
            </p>
          </div>

          {/* Actions */}
          <footer className="mt-6 flex items-center justify-end gap-3 border-t border-[#1f2433] pt-4">
            <button
              type="button"
              onClick={onClose}
              disabled={isSubmitting}
              data-testid="invite-cancel-btn"
              className="inline-flex h-[38px] items-center justify-center rounded-[8px] border border-[#262d3d] bg-[#0f131d] px-4 font-['Plus_Jakarta_Sans:Medium'] text-[13px] text-[#cbd5e1] transition-colors hover:border-[#64748b]/50 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00e599] disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={!isEmailValid || isSubmitting}
              data-testid="invite-submit-btn"
              className={cn(
                "inline-flex h-[38px] items-center justify-center gap-2 rounded-[8px] px-5 font-['Plus_Jakarta_Sans:SemiBold'] text-[13px] transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00e599]",
                isEmailValid && !isSubmitting
                  ? "bg-[#00e599] text-[#05080f] shadow-[0px_4px_12px_rgba(0,229,153,0.25)] hover:bg-[#2df0b0]"
                  : "cursor-not-allowed bg-[#1b2333] text-[#64748b]",
              )}
            >
              {isSubmitting ? (
                <>
                  <Loader2 className="size-4 animate-spin" aria-hidden="true" />
                  <span>Sending...</span>
                </>
              ) : (
                <span>Send Invitation</span>
              )}
            </button>
          </footer>
        </form>
      </div>
    </div>
  );
}
