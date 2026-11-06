"use client";

import { useEffect, useId, useRef, useState } from "react";
import Link from "next/link";
import {
  ArrowRight,
  Check,
  ChevronDown,
  Loader2,
  Shield,
  X,
} from "lucide-react";
import { type Role, CANONICAL_ROLES } from "@/lib/permissions";
import { type WorkspaceMember } from "@/types/member";
import { cn } from "@/lib/utils";

export const ROLE_DETAILS: Record<
  string,
  { label: string; dotColor: string; grants: string }
> = {
  TENANT_ADMIN: {
    label: "Tenant Administrator",
    dotColor: "#00e599",
    grants:
      "Full administrative access: user provisioning, role assignments, third-party integrations (GitHub, Jira, Slack), audit logs, and GDPR Art. 17 data erasure.",
  },
  DELIVERY_DIRECTOR: {
    label: "Delivery Director",
    dotColor: "#a78bfa",
    grants:
      "Grants portfolio-wide visibility into cross-project delivery velocity, DORA engineering metrics, SLA risk forecasts, and organizational security posture.",
  },
  PROJECT_MANAGER: {
    label: "Project Manager / Scrum Master",
    dotColor: "#00c2cb",
    grants:
      "Grants access to sprint velocity, burndown charts, blocked Jira tickets & WIP congestion limits. Cannot modify tenant integrations or trigger GDPR erasure.",
  },
  SCRUM_MASTER: {
    label: "Project Manager / Scrum Master",
    dotColor: "#00c2cb",
    grants:
      "Grants access to sprint velocity, burndown charts, blocked Jira tickets & WIP congestion limits. Cannot modify tenant integrations or trigger GDPR erasure.",
  },
  SOLUTION_ARCHITECT: {
    label: "Solution Architect",
    dotColor: "#60a5fa",
    grants:
      "Review & approve PII-sanitized ADRs, SCIP call-graphs & technical debt metrics.",
  },
  DEVELOPER: {
    label: "Developer",
    dotColor: "#cbd5e1",
    grants:
      "Grants access to assigned repositories, developer telemetry, personal code quality insights, AI-assisted refactoring PRs, and sandbox verification test suites.",
  },
  TOP_MANAGEMENT: {
    label: "Top Management",
    dotColor: "#fbbf24",
    grants:
      "Executive high-level portfolio overview and organizational reporting.",
  },
};

function getInitials(name?: string, email?: string): string {
  if (name && name.trim()) {
    const parts = name.trim().split(/\s+/);
    if (parts.length >= 2) {
      return (parts[0][0] + parts[1][0]).toUpperCase();
    }
    return name.slice(0, 2).toUpperCase();
  }
  if (email) {
    return email.slice(0, 2).toUpperCase();
  }
  return "??";
}

// =========================================================================
// 1. RE-ISSUE WORKSPACE INVITATION DIALOG (Figma 513:7655)
// =========================================================================
interface ReissueInviteDialogProps {
  isOpen: boolean;
  member: WorkspaceMember | null;
  onClose: () => void;
  onConfirm: (member: WorkspaceMember, role: Role) => Promise<void>;
}

export function ReissueInviteDialog({
  isOpen,
  member,
  onClose,
  onConfirm,
}: ReissueInviteDialogProps) {
  const [selectedRole, setSelectedRole] = useState<Role>("DEVELOPER");
  const [isRoleDropdownOpen, setIsRoleDropdownOpen] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const titleId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (member) {
      setSelectedRole((member.role as Role) || "DEVELOPER");
      setIsRoleDropdownOpen(false);
      setIsSubmitting(false);
    }
  }, [member, isOpen]);

  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !isSubmitting) onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [isOpen, onClose, isSubmitting]);

  if (!isOpen || !member) return null;

  const roleConfig = ROLE_DETAILS[selectedRole] || ROLE_DETAILS.DEVELOPER;
  const initials = getInitials(member.name, member.email);

  const handleSend = async () => {
    setIsSubmitting(true);
    try {
      await onConfirm(member, selectedRole);
      onClose();
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div
      role="presentation"
      className="fixed inset-0 z-[95] flex items-center justify-center overflow-y-auto bg-[#05070b]/80 p-4 backdrop-blur-[3px]"
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
        className="flex w-full max-w-[540px] flex-col overflow-visible rounded-[12px] border border-[#262d3d] bg-[#171b26] pt-[22px] px-[24px] pb-[20px] shadow-[0px_25px_50px_-12px_rgba(0,0,0,0.85)] outline-none"
      >
        {/* Header (Figma 513:7656) */}
        <header className="flex shrink-0 items-start justify-between">
          <div className="flex flex-col gap-1">
            <div
              className={cn(
                "inline-flex w-fit items-center gap-[5px] rounded-[99px] border px-[7px] py-[2px]",
                member.status === "INVITE_SENT"
                  ? "border-[rgba(0,194,203,0.35)] bg-[rgba(0,194,203,0.12)] text-[#00c2cb]"
                  : "border-[rgba(245,158,11,0.35)] bg-[rgba(245,158,11,0.12)] text-[#f59e0b]",
              )}
            >
              <span
                className={cn(
                  "size-[5px] rounded-full",
                  member.status === "INVITE_SENT" ? "bg-[#00c2cb]" : "bg-[#f59e0b]",
                )}
                aria-hidden="true"
              />
              <span className="font-mono text-[9.5px] font-bold tracking-[0.5px] uppercase">
                {member.status === "INVITE_SENT"
                  ? `INVITATION ACTIVE · ${(member.expiresIn || "48H LEFT").toUpperCase()}`
                  : `INVITATION EXPIRED · ${(member.expiredAgo || "2D AGO").toUpperCase()}`}
              </span>
            </div>

            <h2
              id={titleId}
              className="font-sans text-[18px] font-bold leading-[23px] text-[#f8fafc]"
            >
              Re-issue Workspace Invitation
            </h2>

            <p className="font-sans text-[12px] leading-[15px] text-[#94a3b8]">
              Generate a fresh 48-hour token and resend onboarding instructions.
            </p>
          </div>

          <button
            type="button"
            onClick={onClose}
            disabled={isSubmitting}
            aria-label="Close dialog"
            className="flex size-[30px] shrink-0 items-center justify-center rounded-[8px] border border-[#262d3d] bg-[#0a0e18] text-[#94a3b8] transition-colors hover:text-[#f8fafc] disabled:opacity-50"
          >
            <X className="size-3.5 stroke-[2]" aria-hidden="true" />
          </button>
        </header>

        {/* Recipient summary card (Figma 513:7666) */}
        <div
          className={cn(
            "mt-3.5 flex items-center justify-between rounded-[8px] px-[14px] py-[11px]",
            member.status === "INVITE_SENT"
              ? "border border-[rgba(0,194,203,0.35)] bg-[rgba(0,194,203,0.06)]"
              : "border border-[rgba(245,158,11,0.4)] bg-[#1d1813]",
          )}
        >
          <div className="flex items-center gap-[10px]">
            <div
              className={cn(
                "flex size-[34px] shrink-0 items-center justify-center rounded-full font-mono text-[11px] font-bold",
                member.status === "INVITE_SENT"
                  ? "border border-[rgba(0,194,203,0.45)] bg-[rgba(0,194,203,0.16)] text-[#00c2cb]"
                  : "border border-[rgba(245,158,11,0.45)] bg-[rgba(245,158,11,0.16)] text-[#fbbf24]",
              )}
            >
              {initials}
            </div>
            <div className="flex flex-col gap-0.5">
              <span className="font-sans text-[13px] font-semibold text-[#f8fafc]">
                {member.name || member.email}
              </span>
              <span className="font-mono text-[11px] text-[#94a3b8]">
                {member.email}
              </span>
            </div>
          </div>
          <div
            className={cn(
              "flex h-[22px] shrink-0 items-center justify-center rounded-[99px] border px-[8px]",
              member.status === "INVITE_SENT"
                ? "border-[rgba(0,194,203,0.35)] bg-[rgba(0,194,203,0.14)]"
                : "border border-[rgba(245,158,11,0.4)] bg-[rgba(245,158,11,0.15)]",
            )}
          >
            <span
              className={cn(
                "font-mono text-[10.5px] font-medium leading-none",
                member.status === "INVITE_SENT" ? "text-[#00c2cb]" : "text-[#fbbf24]",
              )}
            >
              {member.status === "INVITE_SENT"
                ? member.expiresIn || "48h left"
                : `Expired ${member.expiredAgo || "2d ago"}`}
            </span>
          </div>
        </div>

        {/* Assigned role field with dropdown (Figma 513:7675) */}
        <div className="relative mt-3.5 flex flex-col gap-1">
          <button
            type="button"
            aria-haspopup="listbox"
            aria-expanded={isRoleDropdownOpen}
            onClick={() => setIsRoleDropdownOpen(!isRoleDropdownOpen)}
            className="flex h-[56px] w-full flex-col justify-between rounded-[4px] border border-[#262a35] bg-[#0a0e18] px-[14px] py-[8px] text-left transition-colors cursor-pointer hover:border-[#64748b]/50"
          >
            <div className="flex items-center gap-[3px]">
              <span className="font-sans text-[10px] text-[#94a3b8]">
                Assigned role (verify before resending)
              </span>
              <span className="font-sans text-[10px] text-[#ff4d4d]">*</span>
            </div>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-[7px]">
                <span
                  className="size-[7px] shrink-0 rounded-full"
                  style={{ backgroundColor: roleConfig.dotColor }}
                  aria-hidden="true"
                />
                <span className="font-sans text-[14px] font-medium text-[#dfe2f1]">
                  {roleConfig.label}
                </span>
              </div>
              <ChevronDown
                className={cn(
                  "size-3 shrink-0 text-[#94a3b8] transition-transform duration-150",
                  isRoleDropdownOpen && "rotate-180 text-[#00e599]",
                )}
                aria-hidden="true"
              />
            </div>
          </button>

          {isRoleDropdownOpen && (
            <div
              role="listbox"
              className="absolute left-0 top-[calc(100%+4px)] z-50 flex w-full flex-col gap-[3px] rounded-[8px] border border-[rgba(0,229,153,0.65)] bg-[#0a0e18] p-[6px] shadow-[0px_10px_30px_rgba(0,0,0,0.85)]"
            >
              {CANONICAL_ROLES.map((roleKey) => {
                const cfg = ROLE_DETAILS[roleKey] || ROLE_DETAILS.DEVELOPER;
                const isSelected = selectedRole === roleKey;
                return (
                  <button
                    key={roleKey}
                    type="button"
                    role="option"
                    aria-selected={isSelected}
                    onClick={() => {
                      setSelectedRole(roleKey);
                      setIsRoleDropdownOpen(false);
                    }}
                    className={cn(
                      "flex w-full items-center justify-between rounded-[6px] p-[6px_10px] text-left transition-colors cursor-pointer",
                      isSelected
                        ? "border border-[rgba(0,194,203,0.4)] bg-[rgba(0,194,203,0.14)] text-[#00c2cb]"
                        : "text-[#dfe2f1] hover:bg-white/5",
                    )}
                  >
                    <div className="flex items-center gap-[6px]">
                      <span
                        className="size-[7px] shrink-0 rounded-full"
                        style={{ backgroundColor: cfg.dotColor }}
                      />
                      <span className="font-sans text-[12.5px] font-medium">
                        {cfg.label}
                      </span>
                    </div>
                    {isSelected && <Check className="size-3.5 text-[#00c2cb]" />}
                  </button>
                );
              })}
            </div>
          )}
        </div>

        {/* Security Callout (Figma 513:7685) */}
        <div className="mt-3.5 flex items-start gap-[10px] rounded-[8px] border border-[#262d3d] bg-[#0a0e18] p-[10px_12px]">
          <Shield className="mt-0.5 size-4 shrink-0 text-[#00c2cb]" aria-hidden="true" />
          <p className="font-sans text-[11px] leading-[14px] text-[#94a3b8]">
            Previous expired token (tkn_01J98A...) is permanently invalidated. A new single-use 48-hour cryptographic link will be dispatched.
          </p>
        </div>

        {/* Modal Footer (Figma 513:7690) */}
        <footer className="mt-3.5 flex items-center justify-between border-t border-[#262d3d] pt-[12px]">
          <span className="font-mono text-[11px] text-[#94a3b8]">
            New TTL: 48 hours
          </span>
          <div className="flex items-center gap-[10px]">
            <button
              type="button"
              onClick={onClose}
              disabled={isSubmitting}
              className="flex h-[44px] items-center justify-center rounded-[12px] border border-[#262d3d] bg-[#0a0e18] px-[16px] font-sans text-[13px] font-semibold text-[#cbd5e1] transition-colors hover:text-white cursor-pointer disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="button"
              data-testid="reissue-confirm-btn"
              onClick={handleSend}
              disabled={isSubmitting}
              className="flex h-[44px] items-center justify-center gap-2 rounded-[12px] bg-[#00e599] px-[20px] font-sans text-[13px] font-semibold text-[#0a0e18] transition-colors hover:bg-[#2df0b0] cursor-pointer disabled:opacity-50"
            >
              {isSubmitting ? (
                <>
                  <Loader2 className="size-4 animate-spin" />
                  <span>Re-issuing...</span>
                </>
              ) : (
                <span>Generate &amp; Resend Invite →</span>
              )}
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}

// =========================================================================
// 2. CONFIRM MEMBER ROLE CHANGE DIALOG (Figma 513:8103)
// =========================================================================
interface ConfirmRoleChangeDialogProps {
  isOpen: boolean;
  member: WorkspaceMember | null;
  newRole: Role;
  onClose: () => void;
  onConfirm: (memberId: string, newRole: Role) => Promise<void>;
}

export function ConfirmRoleChangeDialog({
  isOpen,
  member,
  newRole,
  onClose,
  onConfirm,
}: ConfirmRoleChangeDialogProps) {
  const [isSubmitting, setIsSubmitting] = useState(false);
  const titleId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !isSubmitting) onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [isOpen, onClose, isSubmitting]);

  if (!isOpen || !member) return null;

  const currentCfg = ROLE_DETAILS[member.role] || ROLE_DETAILS.DEVELOPER;
  const newCfg = ROLE_DETAILS[newRole] || ROLE_DETAILS.SOLUTION_ARCHITECT;

  const handleUpdate = async () => {
    setIsSubmitting(true);
    try {
      await onConfirm(member.id, newRole);
      onClose();
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div
      role="presentation"
      className="fixed inset-0 z-[95] flex items-center justify-center overflow-y-auto bg-[#05070b]/80 p-4 backdrop-blur-[3px]"
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
        className="flex w-full max-w-[540px] flex-col overflow-visible rounded-[12px] border border-[#262d3d] bg-[#171b26] pt-[22px] px-[24px] pb-[20px] shadow-[0px_25px_50px_-12px_rgba(0,0,0,0.85)] outline-none"
      >
        {/* Header (Figma 513:8104) */}
        <header className="flex shrink-0 items-start justify-between">
          <div className="flex flex-col gap-1">
            <div className="inline-flex w-fit items-center gap-[5px] rounded-[99px] border border-[rgba(96,165,250,0.35)] bg-[rgba(96,165,250,0.12)] px-[7px] py-[2px]">
              <span className="size-[5px] rounded-full bg-[#60a5fa]" aria-hidden="true" />
              <span className="font-mono text-[9.5px] font-bold tracking-[0.5px] text-[#60a5fa] uppercase">
                SRS RBAC POLICY UPDATE
              </span>
            </div>

            <h2
              id={titleId}
              className="font-sans text-[18px] font-bold leading-[23px] text-[#f8fafc]"
            >
              Confirm Member Role Change
            </h2>

            <p className="font-sans text-[12px] leading-[15px] text-[#94a3b8]">
              Update workspace role permissions for {member.name || member.email} ({member.email}).
            </p>
          </div>

          <button
            type="button"
            onClick={onClose}
            disabled={isSubmitting}
            aria-label="Close dialog"
            className="flex size-[30px] shrink-0 items-center justify-center rounded-[8px] border border-[#262d3d] bg-[#0a0e18] text-[#94a3b8] transition-colors hover:text-[#f8fafc] disabled:opacity-50"
          >
            <X className="size-3.5 stroke-[2]" aria-hidden="true" />
          </button>
        </header>

        {/* Transition Comparison Box (Figma 513:8114) */}
        <div className="mt-3.5 flex items-center justify-between rounded-[8px] border border-[rgba(96,165,250,0.4)] bg-[#0e131f] px-[16px] py-[12px]">
          {/* Current Role */}
          <div className="flex items-center gap-[6px] rounded-[6px] border border-[rgba(203,213,225,0.3)] bg-[rgba(203,213,225,0.1)] px-[10px] py-[5px]">
            <span
              className="size-[6px] shrink-0 rounded-full"
              style={{ backgroundColor: currentCfg.dotColor }}
              aria-hidden="true"
            />
            <span className="font-sans text-[12px] font-medium text-[#cbd5e1]">
              Current: {currentCfg.label}
            </span>
          </div>

          {/* Arrow */}
          <ArrowRight className="size-4 text-[#60a5fa]" aria-hidden="true" />

          {/* New Role */}
          <div className="flex items-center gap-[6px] rounded-[6px] border border-[rgba(96,165,250,0.55)] bg-[rgba(96,165,250,0.18)] px-[10px] py-[5px]">
            <span
              className="size-[6px] shrink-0 rounded-full"
              style={{ backgroundColor: newCfg.dotColor }}
              aria-hidden="true"
            />
            <span className="font-sans text-[12px] font-semibold text-[#60a5fa]">
              New: {newCfg.label}
            </span>
          </div>
        </div>

        {/* Permissions Delta List (Figma 513:8123) */}
        <div className="mt-3.5 flex flex-col gap-[6px] rounded-[8px] border border-[#262d3d] bg-[#111623] px-[14px] py-[10px]">
          <p className="font-sans text-[11px] font-medium leading-[14px] text-[#00e599]">
            + Grants: {newCfg.grants}
          </p>
          <p className="font-sans text-[11px] font-normal leading-[14px] text-[#94a3b8]">
            = Retains: Personal code quality metrics, AI refactoring PRs &amp; sandbox test verification.
          </p>
          <p className="font-sans text-[11px] font-normal leading-[14px] text-[#64748b]">
            ✕ Restricted: Tenant billing, OAuth integrations &amp; GDPR Art. 17 erasure remain Admin-only.
          </p>
        </div>

        {/* Security Callout (Figma 513:8127) */}
        <div className="mt-3.5 flex items-start gap-[10px] rounded-[8px] border border-[#262d3d] bg-[#0a0e18] p-[10px_12px]">
          <Shield className="mt-0.5 size-4 shrink-0 text-[#00c2cb]" aria-hidden="true" />
          <p className="font-sans text-[11px] leading-[14px] text-[#94a3b8]">
            Active JWT claims session policies refresh immediately upon confirmation without forcing sign-out.
          </p>
        </div>

        {/* Footer (Figma 513:8132) */}
        <footer className="mt-3.5 flex items-center justify-end gap-[10px] border-t border-[#262d3d] pt-[12px]">
          <button
            type="button"
            onClick={onClose}
            disabled={isSubmitting}
            className="flex h-[44px] items-center justify-center rounded-[12px] border border-[#262d3d] bg-[#0a0e18] px-[16px] font-sans text-[13px] font-semibold text-[#cbd5e1] transition-colors hover:text-white cursor-pointer disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            data-testid="confirm-role-btn"
            onClick={handleUpdate}
            disabled={isSubmitting}
            className="flex h-[44px] items-center justify-center gap-2 rounded-[12px] bg-[#00e599] px-[20px] font-sans text-[13px] font-semibold text-[#0a0e18] transition-colors hover:bg-[#2df0b0] cursor-pointer disabled:opacity-50"
          >
            {isSubmitting ? (
              <>
                <Loader2 className="size-4 animate-spin" />
                <span>Updating...</span>
              </>
            ) : (
              <span>Confirm Role Update</span>
            )}
          </button>
        </footer>
      </div>
    </div>
  );
}

// =========================================================================
// 3. REVOKE PENDING INVITATION DIALOG (Figma 513:8545)
// =========================================================================
interface RevokeInviteDialogProps {
  isOpen: boolean;
  member: WorkspaceMember | null;
  onClose: () => void;
  onConfirm: (memberId: string, email: string) => Promise<void>;
}

export function RevokeInviteDialog({
  isOpen,
  member,
  onClose,
  onConfirm,
}: RevokeInviteDialogProps) {
  const [isSubmitting, setIsSubmitting] = useState(false);
  const titleId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !isSubmitting) onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [isOpen, onClose, isSubmitting]);

  if (!isOpen || !member) return null;

  const initials = getInitials(member.name, member.email);
  const roleLabel = ROLE_DETAILS[member.role]?.label || member.role;

  const handleRevoke = async () => {
    setIsSubmitting(true);
    try {
      await onConfirm(member.id, member.email);
      onClose();
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div
      role="presentation"
      className="fixed inset-0 z-[95] flex items-center justify-center overflow-y-auto bg-[#05070b]/80 p-4 backdrop-blur-[3px]"
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
        className="flex w-full max-w-[530px] flex-col overflow-visible rounded-[12px] border border-[rgba(255,77,77,0.4)] bg-[#171b26] pt-[22px] px-[24px] pb-[20px] shadow-[0px_25px_50px_-12px_rgba(0,0,0,0.85)] outline-none"
      >
        {/* Header (Figma 513:8546) */}
        <header className="flex shrink-0 items-start justify-between">
          <div className="flex flex-col gap-1">
            <div className="inline-flex w-fit items-center gap-[5px] rounded-[99px] border border-[rgba(255,77,77,0.35)] bg-[rgba(255,77,77,0.12)] px-[7px] py-[2px]">
              <span className="size-[5px] rounded-full bg-[#ff4d4d]" aria-hidden="true" />
              <span className="font-mono text-[9.5px] font-bold tracking-[0.5px] text-[#ff4d4d] uppercase">
                DESTRUCTIVE ACTION · TOKEN BURN
              </span>
            </div>

            <h2
              id={titleId}
              className="font-sans text-[18px] font-bold leading-[23px] text-[#f8fafc]"
            >
              Revoke Pending Invitation?
            </h2>

            <p className="font-sans text-[12px] leading-[15px] text-[#94a3b8]">
              Immediately invalidate the active 48-hour onboarding link sent to {member.name || member.email}.
            </p>
          </div>

          <button
            type="button"
            onClick={onClose}
            disabled={isSubmitting}
            aria-label="Close dialog"
            className="flex size-[30px] shrink-0 items-center justify-center rounded-[8px] border border-[#262d3d] bg-[#0a0e18] text-[#94a3b8] transition-colors hover:text-[#f8fafc] disabled:opacity-50"
          >
            <X className="size-3.5 stroke-[2]" aria-hidden="true" />
          </button>
        </header>

        {/* Recipient summary row (Figma 513:8556) */}
        <div className="mt-3.5 flex items-center justify-between rounded-[8px] border border-[rgba(255,77,77,0.4)] bg-[#1e1319] px-[14px] py-[11px]">
          <div className="flex items-center gap-[10px]">
            <div className="flex size-[34px] shrink-0 items-center justify-center rounded-full border border-[rgba(203,213,225,0.35)] bg-[rgba(203,213,225,0.14)] font-mono text-[11px] font-bold text-[#cbd5e1]">
              {initials}
            </div>
            <div className="flex flex-col gap-0.5">
              <span className="font-sans text-[13px] font-semibold text-[#f8fafc]">
                {member.name || member.email}
              </span>
              <span className="font-mono text-[11px] text-[#94a3b8]">
                {member.email} · Role: {roleLabel}
              </span>
            </div>
          </div>
          <div className="flex h-[22px] shrink-0 items-center justify-center rounded-[99px] border border-[rgba(0,194,203,0.35)] bg-[rgba(0,194,203,0.14)] px-[8px]">
            <span className="font-mono text-[10.5px] font-medium leading-none text-[#00c2cb]">
              {member.expiresIn || "18h remaining"}
            </span>
          </div>
        </div>

        {/* Consequences Bullet Points (Figma 513:8565) */}
        <div className="mt-3.5 flex flex-col gap-[5px] px-[4px]">
          <p className="font-sans text-[11.5px] leading-[14px] text-[#cbd5e1]">
            • Visiting the emailed invitation URL will return HTTP 410 (Invitation Revoked).
          </p>
          <p className="font-sans text-[11.5px] leading-[14px] text-[#94a3b8]">
            • No workspace account is created. You can invite {member.email} again later.
          </p>
        </div>

        {/* Security Callout (Figma 513:8568) */}
        <div className="mt-3.5 flex items-start gap-[10px] rounded-[8px] border border-[#262d3d] bg-[#0a0e18] p-[10px_12px]">
          <Shield className="mt-0.5 size-4 shrink-0 text-[#00c2cb]" aria-hidden="true" />
          <p className="font-sans text-[11px] leading-[14px] text-[#94a3b8]">
            Token hash is purged immediately from the tenant OIDC allowlist.
          </p>
        </div>

        {/* Footer (Figma 513:8573) */}
        <footer className="mt-3.5 flex items-center justify-between border-t border-[#262d3d] pt-[12px]">
          <span className="font-mono text-[11px] font-medium text-[#ff7878]">
            Immediate revocation
          </span>
          <div className="flex items-center gap-[10px]">
            <button
              type="button"
              onClick={onClose}
              disabled={isSubmitting}
              className="flex h-[44px] items-center justify-center rounded-[12px] border border-[#262d3d] bg-[#0a0e18] px-[16px] font-sans text-[13px] font-semibold text-[#cbd5e1] transition-colors hover:text-white cursor-pointer disabled:opacity-50"
            >
              Keep Invite
            </button>
            <button
              type="button"
              data-testid="revoke-confirm-btn"
              onClick={handleRevoke}
              disabled={isSubmitting}
              className="flex h-[44px] items-center justify-center gap-2 rounded-[12px] bg-[#ff4d4d] px-[20px] font-sans text-[13px] font-semibold text-white transition-colors hover:bg-[#ff6666] cursor-pointer disabled:opacity-50"
            >
              {isSubmitting ? (
                <>
                  <Loader2 className="size-4 animate-spin" />
                  <span>Revoking...</span>
                </>
              ) : (
                <span>Revoke Invitation</span>
              )}
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}

// =========================================================================
// 4. REMOVE MEMBER FROM WORKSPACE DIALOG (Figma 513:8986)
// =========================================================================
interface RemoveMemberDialogProps {
  isOpen: boolean;
  member: WorkspaceMember | null;
  onClose: () => void;
  onConfirm: (memberId: string, displayName: string) => Promise<void>;
}

export function RemoveMemberDialog({
  isOpen,
  member,
  onClose,
  onConfirm,
}: RemoveMemberDialogProps) {
  const [isSubmitting, setIsSubmitting] = useState(false);
  const titleId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !isSubmitting) onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [isOpen, onClose, isSubmitting]);

  if (!isOpen || !member) return null;

  const initials = getInitials(member.name, member.email);
  const roleLabel = ROLE_DETAILS[member.role]?.label || member.role;

  const handleRemove = async () => {
    setIsSubmitting(true);
    try {
      await onConfirm(member.id, member.name || member.email);
      onClose();
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div
      role="presentation"
      className="fixed inset-0 z-[95] flex items-center justify-center overflow-y-auto bg-[#05070b]/80 p-4 backdrop-blur-[3px]"
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
        className="flex w-full max-w-[550px] flex-col overflow-visible rounded-[12px] border border-[rgba(255,77,77,0.45)] bg-[#171b26] pt-[22px] px-[24px] pb-[20px] shadow-[0px_25px_50px_-12px_rgba(0,0,0,0.85)] outline-none"
      >
        {/* Header (Figma 513:8987) */}
        <header className="flex shrink-0 items-start justify-between">
          <div className="flex flex-col gap-1">
            <div className="inline-flex w-fit items-center gap-[5px] rounded-[99px] border border-[rgba(255,77,77,0.35)] bg-[rgba(255,77,77,0.12)] px-[7px] py-[2px]">
              <span className="size-[5px] rounded-full bg-[#ff4d4d]" aria-hidden="true" />
              <span className="font-mono text-[9.5px] font-bold tracking-[0.5px] text-[#ff4d4d] uppercase">
                REVOKE TENANT ACCESS · US-12
              </span>
            </div>

            <h2
              id={titleId}
              className="font-sans text-[18px] font-bold leading-[23px] text-[#f8fafc]"
            >
              Remove Member from Workspace?
            </h2>

            <p className="font-sans text-[12px] leading-[15px] text-[#94a3b8]">
              Immediately terminate Acme Consulting access for {member.name || member.email}.
            </p>
          </div>

          <button
            type="button"
            onClick={onClose}
            disabled={isSubmitting}
            aria-label="Close dialog"
            className="flex size-[30px] shrink-0 items-center justify-center rounded-[8px] border border-[#262d3d] bg-[#0a0e18] text-[#94a3b8] transition-colors hover:text-[#f8fafc] disabled:opacity-50"
          >
            <X className="size-3.5 stroke-[2]" aria-hidden="true" />
          </button>
        </header>

        {/* Target summary row (Figma 513:8997) */}
        <div className="mt-3.5 flex items-center justify-between rounded-[8px] border border-[rgba(255,77,77,0.4)] bg-[#1e1319] px-[14px] py-[11px]">
          <div className="flex items-center gap-[10px]">
            <div className="flex size-[34px] shrink-0 items-center justify-center rounded-full border border-[rgba(203,213,225,0.35)] bg-[rgba(203,213,225,0.14)] font-mono text-[11px] font-bold text-[#cbd5e1]">
              {initials}
            </div>
            <div className="flex flex-col gap-0.5">
              <span className="font-sans text-[13px] font-semibold text-[#f8fafc]">
                {member.name || member.email}
              </span>
              <span className="font-mono text-[11px] text-[#94a3b8]">
                {member.email} · Active Member
              </span>
            </div>
          </div>
          <div className="flex h-[21px] shrink-0 items-center justify-center rounded-[6px] border border-[rgba(203,213,225,0.3)] bg-[rgba(203,213,225,0.12)] px-[8px]">
            <span className="font-sans text-[10.5px] font-medium leading-none text-[#e2e8f0]">
              {roleLabel}
            </span>
          </div>
        </div>

        {/* Consequences Bullet Card (Figma 513:9006) */}
        <div className="mt-3.5 flex flex-col gap-[6px] rounded-[8px] border border-[#262d3d] bg-[#0e131f] px-[14px] py-[10px]">
          <p className="font-sans text-[11px] leading-[14px] text-[#cbd5e1]">
            • All active OAuth2 / JWT browser sessions are revoked immediately.
          </p>
          <p className="font-sans text-[11px] leading-[14px] text-[#94a3b8]">
            • Sanitized ADR records &amp; sprint velocity history remain preserved for tenant analytics.
          </p>
          <div className="flex items-center gap-[5px] text-[11px] leading-[14px]">
            <span className="font-sans font-medium text-[#94a3b8]">
              • Need full GDPR Art. 17 PII erasure?
            </span>
            <Link
              href="/privacy-erasure"
              onClick={onClose}
              className="font-sans font-semibold text-[#00e599] transition-colors hover:underline"
            >
              Open Administration → Privacy &amp; Erasure →
            </Link>
          </div>
        </div>

        {/* Footer (Figma 513:9017) */}
        <footer className="mt-3.5 flex items-center justify-between border-t border-[#262d3d] pt-[12px]">
          <span className="font-mono text-[11px] font-medium text-[#ff7878]">
            Audit: MEMBER_REMOVED
          </span>
          <div className="flex items-center gap-[10px]">
            <button
              type="button"
              onClick={onClose}
              disabled={isSubmitting}
              className="flex h-[44px] items-center justify-center rounded-[12px] border border-[#262d3d] bg-[#0a0e18] px-[16px] font-sans text-[13px] font-semibold text-[#cbd5e1] transition-colors hover:text-white cursor-pointer disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="button"
              data-testid="remove-confirm-btn"
              onClick={handleRemove}
              disabled={isSubmitting}
              className="flex h-[44px] items-center justify-center gap-2 rounded-[12px] bg-[#ff4d4d] px-[20px] font-sans text-[13px] font-semibold text-white transition-colors hover:bg-[#ff6666] cursor-pointer disabled:opacity-50"
            >
              {isSubmitting ? (
                <>
                  <Loader2 className="size-4 animate-spin" />
                  <span>Removing...</span>
                </>
              ) : (
                <span>Remove Member</span>
              )}
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}
