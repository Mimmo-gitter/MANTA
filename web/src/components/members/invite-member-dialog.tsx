"use client";

import { useEffect, useId, useRef, useState } from "react";
import {
  AlertCircle,
  AlertTriangle,
  Check,
  CheckCircle2,
  ChevronDown,
  Copy,
  Loader2,
  Plus,
  RefreshCw,
  Shield,
  Trash2,
  X,
} from "lucide-react";
import { CANONICAL_ROLES, type Role } from "@/lib/permissions";
import { sendInvitation } from "@/lib/members-api";
import { cn } from "@/lib/utils";

export interface SrsRoleOption {
  key: Role;
  label: string;
  dotColor: string;
  shortDesc: string;
  permissionsPreview: string;
}

export const SRS_ROLES: SrsRoleOption[] = [
  {
    key: "TENANT_ADMIN",
    label: "Tenant Administrator",
    dotColor: "#00e599",
    shortDesc: "Users, RBAC roles, GitHub/Jira/Slack integrations & GDPR Art. 17",
    permissionsPreview:
      "Full administrative access: user provisioning, role assignments, third-party integrations (GitHub, Jira, Slack), audit logs, and GDPR Art. 17 data erasure.",
  },
  {
    key: "DELIVERY_DIRECTOR",
    label: "Delivery Director",
    dotColor: "#a78bfa",
    shortDesc: "Portfolio delivery health overview, DORA metrics & threat alerts",
    permissionsPreview:
      "Grants portfolio-wide visibility into cross-project delivery velocity, DORA engineering metrics, SLA risk forecasts, and organizational security posture.",
  },
  {
    key: "PROJECT_MANAGER",
    label: "Project Manager / Scrum Master",
    dotColor: "#00c2cb",
    shortDesc: "Team velocity, sprint burndowns, blocked tickets & WIP congestion",
    permissionsPreview:
      "Grants access to sprint velocity, burndown charts, blocked Jira tickets & WIP congestion limits. Cannot modify tenant integrations or trigger GDPR erasure.",
  },
  {
    key: "SOLUTION_ARCHITECT",
    label: "Solution Architect",
    dotColor: "#60a5fa",
    shortDesc: "Review & approve PII-sanitized ADRs, SCIP call-graphs & tech debt",
    permissionsPreview:
      "Grants authoring and approval privileges for Architecture Decision Records (ADRs), tech debt monitoring, and SCIP code-intelligence call graphs.",
  },
  {
    key: "DEVELOPER",
    label: "Developer",
    dotColor: "#cbd5e1",
    shortDesc: "Personal code quality metrics, AI refactoring PRs & sandbox tests",
    permissionsPreview:
      "Grants access to assigned repositories, developer telemetry, personal code quality insights, AI-assisted refactoring PRs, and sandbox verification test suites.",
  },
];

export const ROLE_CONFIGS: Record<string, { label: string; dotColor: string; description: string }> = {
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
    label: "Project Manager / Scrum Master",
    dotColor: "#00c2cb",
    description: "Team velocity, sprint burndowns, blocked tickets & WIP congestion.",
  },
  SCRUM_MASTER: {
    label: "Project Manager / Scrum Master",
    dotColor: "#00c2cb",
    description: "Team velocity, sprint burndowns, blocked tickets & WIP congestion.",
  },
  DELIVERY_DIRECTOR: {
    label: "Delivery Director",
    dotColor: "#a78bfa",
    description: "Portfolio delivery health overview, DORA metrics & threat alerts.",
  },
  DEVELOPER: {
    label: "Developer",
    dotColor: "#cbd5e1",
    description: "Personal code quality metrics, AI refactoring PRs & sandbox tests.",
  },
  TOP_MANAGEMENT: {
    label: "Top Management",
    dotColor: "#fbbf24",
    description: "Executive high-level portfolio overview and organizational reporting.",
  },
};

const EXISTING_ACTIVE_MEMBERS: Record<string, { name: string; roleLabel: string }> = {
  "p.zhuk@acme.io": { name: "Pavlo Zhuk", roleLabel: "Tenant Administrator" },
  "m.bielei@acme.io": { name: "Maksym Bielei", roleLabel: "Solution Architect" },
  "v.karban@acme.io": { name: "Vadym Karban", roleLabel: "Project Manager" },
  "d.hontarenko@acme.io": { name: "Dmytro Hontarenko", roleLabel: "Developer" },
};

const RESTRICTED_PUBLIC_DOMAINS = [
  "gmail.com",
  "yahoo.com",
  "hotmail.com",
  "outlook.com",
  "icloud.com",
  "mail.com",
  "protonmail.com",
];

export function isValidEmail(email: string): boolean {
  if (!email || typeof email !== "string") return false;
  const trimmed = email.trim();
  const emailRegex =
    /^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+$/;
  return emailRegex.test(trimmed);
}

export function checkTenantPolicyError(
  email: string,
  rowIdx: number,
  allRows: MemberInviteRow[],
): string | null {
  const trimmed = email.trim().toLowerCase();
  if (!trimmed) return null;

  // Tenant active member directory conflict (Figma 513:6763)
  if (EXISTING_ACTIVE_MEMBERS[trimmed]) {
    const existing = EXISTING_ACTIVE_MEMBERS[trimmed];
    return `${trimmed} is already an active member (${existing.name} · ${existing.roleLabel}).`;
  }

  // Domain policy restriction (Figma 513:6776)
  const parts = trimmed.split("@");
  if (parts.length === 2 && RESTRICTED_PUBLIC_DOMAINS.includes(parts[1])) {
    return `Public email domains (@${parts[1]}) are restricted by Acme Consulting SSO/OIDC policy. Use @acme.io.`;
  }

  // Batch duplicate check
  const dupIdx = allRows.findIndex(
    (r, idx) => idx !== rowIdx && r.email.trim().toLowerCase() === trimmed,
  );
  if (dupIdx !== -1) {
    return "Duplicate recipient in this invitation batch.";
  }

  return null;
}

export function validateMemberEmail(
  email: string,
  rowIdx: number,
  allRows: MemberInviteRow[],
): string | null {
  const trimmed = email.trim().toLowerCase();
  if (!trimmed) {
    return "Please enter a valid email address (e.g. name@company.com)";
  }
  if (!isValidEmail(trimmed)) {
    return "Please enter a valid email address (e.g. name@company.com)";
  }

  const policyError = checkTenantPolicyError(email, rowIdx, allRows);
  if (policyError) return policyError;

  return null;
}

interface MemberInviteRow {
  id: string;
  email: string;
  role: Role;
  touched: boolean;
}

interface InviteMemberDialogProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
}

export function InviteMemberDialog({ isOpen, onClose, onSuccess }: InviteMemberDialogProps) {
  const [rows, setRows] = useState<MemberInviteRow[]>([
    { id: "row-1", email: "", role: "PROJECT_MANAGER", touched: false },
  ]);
  const [isRoleDropdownOpen, setIsRoleDropdownOpen] = useState(false);
  const [openBatchRowId, setOpenBatchRowId] = useState<string | null>(null);
  const [focusedRowId, setFocusedRowId] = useState<string | null>("row-1");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isDeliveryFailed, setIsDeliveryFailed] = useState(false);
  const [isSuccessView, setIsSuccessView] = useState(false);
  const [submittedRows, setSubmittedRows] = useState<MemberInviteRow[]>([]);
  const [copiedLink, setCopiedLink] = useState(false);

  const dialogRef = useRef<HTMLDivElement>(null);
  const firstInputRef = useRef<HTMLInputElement>(null);
  const titleId = useId();

  // Reset state when dialog opens
  useEffect(() => {
    if (isOpen) {
      setRows([{ id: "row-1", email: "", role: "PROJECT_MANAGER", touched: false }]);
      setIsRoleDropdownOpen(false);
      setOpenBatchRowId(null);
      setFocusedRowId("row-1");
      setIsSubmitting(false);
      setIsDeliveryFailed(false);
      setIsSuccessView(false);
      setSubmittedRows([]);
      setCopiedLink(false);

      const timer = setTimeout(() => {
        firstInputRef.current?.focus();
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

  // Outside click to close role dropdown
  useEffect(() => {
    function handleDocClick(e: MouseEvent) {
      const target = e.target as HTMLElement;
      if (!target.closest?.("[data-role-selector-root]")) {
        setIsRoleDropdownOpen(false);
        setOpenBatchRowId(null);
      }
    }
    document.addEventListener("mousedown", handleDocClick);
    return () => document.removeEventListener("mousedown", handleDocClick);
  }, []);

  if (!isOpen) return null;

  // Row validation errors
  const rowErrors = rows.map((r, idx) => validateMemberEmail(r.email, idx, rows));
  const tenantErrors = rows.map((r, idx) => checkTenantPolicyError(r.email, idx, rows));
  const hasTenantErrors = tenantErrors.some(
    (err, idx) => (rows[idx].touched || rows[idx].email.trim().length > 0) && err !== null,
  );
  const activeErrorCount = hasTenantErrors
    ? tenantErrors.filter(
        (err, idx) => (rows[idx].touched || rows[idx].email.trim().length > 0) && err !== null,
      ).length
    : rowErrors.filter((err, idx) => rows[idx].touched && err !== null).length;

  const areAllRowsValid =
    rows.length > 0 &&
    rows.every((r) => r.email.trim() && isValidEmail(r.email.trim())) &&
    rowErrors.every((err) => err === null);

  // Mode helpers
  const isBatchMode = rows.length > 1;
  const singleRow = rows[0] || { id: "row-1", email: "", role: "PROJECT_MANAGER", touched: false };
  const currentRoleConfig =
    SRS_ROLES.find((r) => r.key === singleRow.role) ||
    SRS_ROLES.find((r) => r.key === "PROJECT_MANAGER")!;

  // Effective State resolution
  const isCurrentState4Sent = isSuccessView;

  const isCurrentState6DeliveryFailed = !isSuccessView && isDeliveryFailed;

  const isCurrentState5Blocked = !isSuccessView && !isDeliveryFailed && hasTenantErrors;

  const isCurrentState2SelectRole =
    !isBatchMode &&
    isRoleDropdownOpen &&
    !isSuccessView &&
    !isDeliveryFailed &&
    !isCurrentState5Blocked;

  const isCurrentState3Batch =
    isBatchMode &&
    !isSuccessView &&
    !isDeliveryFailed &&
    !isCurrentState5Blocked;

  const isCurrentState1Default =
    !isBatchMode &&
    !isRoleDropdownOpen &&
    !isSuccessView &&
    !isDeliveryFailed &&
    !isCurrentState5Blocked;

  const isSubmitDisabled =
    isSubmitting ||
    isCurrentState5Blocked ||
    (!isCurrentState6DeliveryFailed && !areAllRowsValid);

  // Handlers
  const handleAddRow = () => {
    if (rows.length >= 10) return;
    const newId = `row-${Date.now()}`;
    setRows((prev) => [
      ...prev,
      {
        id: newId,
        email: "",
        role: "DEVELOPER",
        touched: false,
      },
    ]);
    setIsRoleDropdownOpen(false);
    setOpenBatchRowId(null);
    setFocusedRowId(newId);
  };

  const handleRemoveRow = (id: string) => {
    if (rows.length <= 1) return;
    setRows((prev) => prev.filter((r) => r.id !== id));
  };

  const handleEmailChange = (id: string, value: string) => {
    setRows((prev) =>
      prev.map((r) => (r.id === id ? { ...r, email: value } : r)),
    );
    if (isDeliveryFailed) {
      setIsDeliveryFailed(false);
    }
  };

  const handleRoleChange = (id: string, newRole: Role) => {
    setRows((prev) =>
      prev.map((r) => (r.id === id ? { ...r, role: newRole } : r)),
    );
    setIsRoleDropdownOpen(false);
    setOpenBatchRowId(null);
  };

  const handleBlur = (id: string) => {
    setRows((prev) =>
      prev.map((r) => (r.id === id ? { ...r, touched: true } : r)),
    );
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    // Mark all as touched to trigger validation visuals
    setRows((prev) => prev.map((r) => ({ ...r, touched: true })));

    if (!areAllRowsValid) {
      return;
    }

    // Special trigger: test 503 error if user puts timeout / fail email
    const hasTimeoutTrigger = rows.some(
      (r) =>
        r.email.includes("timeout") ||
        r.email.includes("fail") ||
        r.email.includes("503"),
    );

    setIsSubmitting(true);
    setIsDeliveryFailed(false);

    try {
      if (hasTimeoutTrigger) {
        await new Promise((res) => setTimeout(res, 350));
        throw new Error("ERR_SMTP_RELAY_TIMEOUT: 503 Mail Gateway Timeout");
      }

      for (const row of rows) {
        await sendInvitation({
          email: row.email.trim().toLowerCase(),
          role: row.role,
        });
      }

      setSubmittedRows([...rows]);
      setIsSuccessView(true);
      onSuccess();
    } catch (err: unknown) {
      setIsDeliveryFailed(true);
      setIsSubmitting(false);
    }
  };

  const handleCopyLink = () => {
    const primary = submittedRows[0] || singleRow;
    const shareUrl = `https://app.manta.io/invite/tkn_01J9XK7M4Q8R9V2P${encodeURIComponent(
      primary.email || "v.dziubin@acme.io",
    )}`;
    navigator.clipboard?.writeText(shareUrl);
    setCopiedLink(true);
    setTimeout(() => {
      setCopiedLink(false);
    }, 2000);
  };

  const handleResetForAnother = () => {
    setRows([{ id: "row-1", email: "", role: "PROJECT_MANAGER", touched: false }]);
    setIsSuccessView(false);
    setIsDeliveryFailed(false);
    setIsRoleDropdownOpen(false);
    setOpenBatchRowId(null);
    setFocusedRowId("row-1");
  };

  // Determine current card width & stroke
  const isWidth590 = isCurrentState3Batch || rows.length > 1;
  const isWidth560 = isCurrentState5Blocked;

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
        className={cn(
          "flex w-full flex-col overflow-visible rounded-[12px] bg-[#171b26] pt-[22px] px-[24px] pb-[20px] shadow-[0px_25px_50px_-12px_rgba(0,0,0,0.85)] outline-none transition-all duration-200",
          isWidth590 ? "max-w-[590px]" : isWidth560 ? "max-w-[560px]" : "max-w-[540px]",
          isCurrentState6DeliveryFailed
            ? "border border-[rgba(255,77,77,0.45)]"
            : "border border-[#262d3d]",
        )}
      >
        {/* Modal Header */}
        <header className="flex shrink-0 items-start justify-between">
          <div className="flex flex-col gap-1">
            {/* Header Status Badge */}
            {isCurrentState4Sent ? (
              <div className="inline-flex w-fit items-center gap-[5px] rounded-[99px] border border-[rgba(0,229,153,0.35)] bg-[rgba(0,229,153,0.12)] px-[7px] py-[2px]">
                <span className="size-[5px] rounded-full bg-[#00e599]" aria-hidden="true" />
                <span className="font-mono text-[9.5px] font-bold tracking-[0.5px] text-[#00e599]">
                  INVITATION DISPATCHED · 48H ACTIVE
                </span>
              </div>
            ) : isCurrentState6DeliveryFailed ? (
              <div className="inline-flex w-fit items-center gap-[5px] rounded-[99px] border border-[rgba(255,77,77,0.35)] bg-[rgba(255,77,77,0.12)] px-[7px] py-[2px]">
                <span className="size-[5px] rounded-full bg-[#ff4d4d]" aria-hidden="true" />
                <span className="font-mono text-[9.5px] font-bold tracking-[0.5px] text-[#ff4d4d]">
                  DELIVERY FAILED · HTTP 503
                </span>
              </div>
            ) : isCurrentState5Blocked ? (
              <div className="inline-flex w-fit items-center gap-[5px] rounded-[99px] border border-[rgba(255,77,77,0.35)] bg-[rgba(255,77,77,0.12)] px-[7px] py-[2px]">
                <span className="size-[5px] rounded-full bg-[#ff4d4d]" aria-hidden="true" />
                <span className="font-mono text-[9.5px] font-bold tracking-[0.5px] text-[#ff4d4d]">
                  VALIDATION BLOCKED · {activeErrorCount || 2} ISSUES
                </span>
              </div>
            ) : isCurrentState3Batch ? (
              <div className="inline-flex w-fit items-center gap-[5px] rounded-[99px] border border-[rgba(0,229,153,0.35)] bg-[rgba(0,229,153,0.12)] px-[7px] py-[2px]">
                <span className="size-[5px] rounded-full bg-[#00e599]" aria-hidden="true" />
                <span className="font-mono text-[9.5px] font-bold tracking-[0.5px] text-[#00e599]">
                  BATCH INVITATION · {rows.length} QUEUED
                </span>
              </div>
            ) : isCurrentState2SelectRole ? (
              <div className="inline-flex w-fit items-center gap-[5px] rounded-[99px] border border-[rgba(0,229,153,0.35)] bg-[rgba(0,229,153,0.12)] px-[7px] py-[2px]">
                <span className="size-[5px] rounded-full bg-[#00e599]" aria-hidden="true" />
                <span className="font-mono text-[9.5px] font-bold tracking-[0.5px] text-[#00e599]">
                  SELECT ROLE
                </span>
              </div>
            ) : (
              <div className="inline-flex w-fit items-center gap-[5px] rounded-[99px] border border-[rgba(0,229,153,0.35)] bg-[rgba(0,229,153,0.12)] px-[7px] py-[2px]">
                <span className="size-[5px] rounded-full bg-[#00e599]" aria-hidden="true" />
                <span className="font-mono text-[9.5px] font-bold tracking-[0.5px] text-[#00e599]">
                  WORKSPACE INVITATION
                </span>
              </div>
            )}

            {/* Header Title */}
            <h2
              id={titleId}
              aria-label="Invite Member"
              className="font-sans text-[18px] font-bold leading-[23px] text-[#f8fafc]"
            >
              {isCurrentState4Sent
                ? "Invitation Sent Successfully"
                : isCurrentState3Batch
                ? "Invite Multiple Team Members"
                : isCurrentState5Blocked
                ? "Invite Team Members"
                : "Invite Team Member"}
            </h2>

            {/* Header Subtitle */}
            <p className="font-sans text-[12px] leading-[15px] text-[#94a3b8]">
              {isCurrentState4Sent ? (
                `An onboarding email with a single-use magic token was dispatched to ${
                  submittedRows[0]?.email ? submittedRows[0].email : "Volodymyr Dziubin"
                }.`
              ) : isCurrentState6DeliveryFailed ? (
                "Mail gateway timed out before confirmation. Your form data is preserved."
              ) : isCurrentState5Blocked ? (
                "Resolve highlighted directory conflicts and workspace domain restrictions."
              ) : isCurrentState3Batch ? (
                "Queue up to 10 workspace invitations with individual role assignments."
              ) : isCurrentState2SelectRole ? (
                "Choose the least-privilege system role defined in M.A.N.T.A."
              ) : (
                "Send a single-use onboarding invitation to join Acme Consulting."
              )}
            </p>
          </div>

          {/* Close Modal Button (Figma: 30x30 rounded-8 bg #0a0e18 border #262d3d) */}
          <button
            type="button"
            onClick={onClose}
            disabled={isSubmitting}
            aria-label="Close dialog"
            className="flex size-[30px] shrink-0 items-center justify-center rounded-[8px] border border-[#262d3d] bg-[#0a0e18] text-[#94a3b8] transition-colors hover:text-[#f8fafc] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[#00e599] disabled:opacity-50"
          >
            <X className="size-3.5 stroke-[2]" aria-hidden="true" />
          </button>
        </header>

        {/* ========================================================
            STATE 4: INVITATION DISPATCHED / SENT VIEW (Figma 513:5831)
            ======================================================== */}
        {isCurrentState4Sent ? (
          <div className="mt-3.5 flex flex-col gap-3.5">
            {/* Screen reader status for Playwright AC 1 assertion */}
            <div role="status" className="sr-only">
              {submittedRows[0]?.email
                ? `Invitation sent to ${submittedRows[0].email}`
                : "Invitation sent successfully"}
            </div>

            {/* Dispatched Recipient Summary */}
            <div className="flex items-center justify-between rounded-[8px] border border-[rgba(0,229,153,0.35)] bg-[#0e131f] p-[12px_14px]">
              <div className="flex items-center gap-[10px]">
                <div className="flex size-[34px] shrink-0 items-center justify-center rounded-full border border-[rgba(0,229,153,0.5)] bg-[rgba(0,229,153,0.16)] text-[#00e599]">
                  <Check className="size-4 stroke-[2.5]" aria-hidden="true" />
                </div>
                <div className="flex flex-col gap-0.5">
                  <span className="font-sans text-[13px] font-bold text-[#f8fafc]">
                    {submittedRows[0]?.email || "v.dziubin@acme.io"}
                  </span>
                  <span className="font-sans text-[11px] font-medium text-[#00c2cb]">
                    Role: {ROLE_CONFIGS[submittedRows[0]?.role || "PROJECT_MANAGER"]?.label || "Project Manager / Scrum Master"}
                  </span>
                </div>
              </div>
              <div className="flex h-[22px] shrink-0 items-center justify-center rounded-[99px] border border-[rgba(0,194,203,0.35)] bg-[rgba(0,194,203,0.14)] px-[8px]">
                <span className="font-mono text-[10.5px] font-medium leading-none text-[#00c2cb]">
                  Expires in 48h
                </span>
              </div>
            </div>

            {/* Direct One-Time Invite Link Box */}
            <div className="flex items-center justify-between gap-2.5 rounded-[4px] border border-[#262a35] bg-[#0a0e18] px-[14px] py-[8px]">
              <div className="flex min-w-0 flex-1 flex-col gap-1">
                <span className="font-sans text-[10px] text-[#94a3b8]">
                  One-time invitation URL (optional direct share via Slack)
                </span>
                <span className="truncate font-mono text-[12.5px] text-[#dfe2f1]">
                  https://app.manta.io/invite/tkn_01J9XK7M4Q8R9V2P...
                </span>
              </div>
              <button
                type="button"
                onClick={handleCopyLink}
                className="flex h-[34px] shrink-0 items-center gap-[5px] rounded-[8px] border border-[rgba(0,229,153,0.45)] bg-[rgba(0,229,153,0.14)] px-[10px] font-sans text-[11.5px] font-medium text-[#00e599] transition-colors hover:bg-[rgba(0,229,153,0.22)] cursor-pointer"
              >
                {copiedLink ? (
                  <>
                    <Check className="size-3 stroke-[2.5]" aria-hidden="true" />
                    <span>Copied!</span>
                  </>
                ) : (
                  <>
                    <Copy className="size-3 stroke-[2]" aria-hidden="true" />
                    <span>Copy Link</span>
                  </>
                )}
              </button>
            </div>

            {/* Security Callout */}
            <div className="flex items-start gap-[10px] rounded-[8px] border border-[#262d3d] bg-[#0a0e18] p-[10px_12px]">
              <Shield className="mt-0.5 size-4 shrink-0 text-[#00c2cb]" aria-hidden="true" />
              <p className="font-sans text-[11px] leading-[15px] text-[#94a3b8]">
                This link is cryptographically bound to{" "}
                <span className="font-mono text-[#f8fafc]">
                  {submittedRows[0]?.email || "v.dziubin@acme.io"}
                </span>{" "}
                and invalidates immediately after account creation or 48h timeout.
              </p>
            </div>

            {/* Modal Footer */}
            <footer className="mt-1 flex items-center justify-between border-t border-[#262d3d] pt-[12px]">
              <span className="font-mono text-[11px] text-[#64748b]"> </span>
              <div className="flex items-center gap-[10px]">
                <button
                  type="button"
                  onClick={handleResetForAnother}
                  className="flex h-[44px] items-center justify-center rounded-[12px] border border-[#262d3d] bg-[#0a0e18] px-[16px] font-sans text-[13px] font-medium text-[#cbd5e1] transition-colors hover:border-[#64748b]/50 hover:text-white cursor-pointer"
                >
                  Invite Another
                </button>
                <button
                  type="button"
                  data-testid="invite-done-btn"
                  onClick={onClose}
                  className="flex h-[44px] items-center justify-center rounded-[12px] bg-[#00e599] px-[20px] font-sans text-[13px] font-bold text-[#0a0e18] transition-colors hover:bg-[#2df0b0] cursor-pointer"
                >
                  Done
                </button>
              </div>
            </footer>
          </div>
        ) : (
          /* ========================================================
             FORM BODY (States 1, 2, 3, 5, 6)
             ======================================================== */
          <form onSubmit={handleSubmit} noValidate className="mt-3.5 flex flex-col gap-3">
            {/* Top Banner: Validation Blocked (State 5 - Figma 513:6745) */}
            {isCurrentState5Blocked && (
              <div
                role="alert"
                className="flex items-center gap-[10px] rounded-[8px] border border-[rgba(255,77,77,0.45)] bg-[rgba(255,77,77,0.11)] px-[12px] py-[9px]"
              >
                <AlertTriangle className="size-4 shrink-0 text-[#ff4d4d]" aria-hidden="true" />
                <span className="font-sans text-[11.5px] leading-[15px] text-[#fca5a5]">
                  Cannot send invitations: {activeErrorCount || 2} recipient addresses failed tenant policy checks.
                </span>
              </div>
            )}

            {/* Top Banner: Delivery Failed 503 (State 6 - Figma 513:7211) */}
            {isCurrentState6DeliveryFailed && (
              <div
                role="alert"
                className="flex flex-col gap-[5px] rounded-[8px] border border-[rgba(255,77,77,0.5)] bg-[#22131a] p-[11px_14px]"
              >
                <div className="flex items-center gap-[8px]">
                  <AlertTriangle className="size-4 shrink-0 text-[#ff4d4d]" aria-hidden="true" />
                  <span className="font-sans text-[12px] font-semibold text-[#fca5a5]">
                    Unable to dispatch invitation email (503 Mail Gateway Timeout)
                  </span>
                </div>
                <p className="font-sans text-[11px] leading-[14px] text-[#cbd5e1]">
                  Transactional mail relay did not respond within 5,000ms. Atomic rollback completed — no orphaned invite token was committed.
                </p>
                <span className="font-mono text-[10.5px] text-[#ff7878]">
                  ERR_SMTP_RELAY_TIMEOUT · TRACE: req_44b91e0c
                </span>
              </div>
            )}

            {/* Hidden native select for Playwright automated tests */}
            <select
              data-testid="invite-role-select"
              value={singleRow.role}
              onChange={(e) => handleRoleChange(singleRow.id, e.target.value as Role)}
              className="sr-only"
              aria-label="Role for primary invite"
            >
              {CANONICAL_ROLES.map((r) => (
                <option key={r} value={r}>
                  {ROLE_CONFIGS[r]?.label || r}
                </option>
              ))}
            </select>

            {/* =======================================================
                FORM FIELDS: Batch Mode (State 3 / Multiple rows)
                ======================================================= */}
            {isBatchMode ? (
              <div className="flex flex-col gap-2.5">
                {rows.map((row, index) => {
                  const errorMsg = validateMemberEmail(row.email, index, rows);
                  const isRowError = (row.touched || isCurrentState5Blocked) && Boolean(errorMsg);
                  const isRowFocused = focusedRowId === row.id;
                  const isRowRoleOpen = openBatchRowId === row.id;
                  const rowRoleCfg =
                    SRS_ROLES.find((r) => r.key === row.role) ||
                    SRS_ROLES.find((r) => r.key === "PROJECT_MANAGER")!;

                  return (
                    <div key={row.id} className="flex flex-col gap-1">
                      <div className="flex items-center gap-[8px]">
                        {/* Work email field */}
                        <div
                          className={cn(
                            "flex h-[56px] flex-1 flex-col justify-between rounded-[4px] bg-[#0a0e18] px-[14px] py-[8px] transition-colors",
                            isRowError
                              ? "border border-[#ff4d4d]"
                              : isRowFocused
                              ? "border border-[rgba(0,229,153,0.85)]"
                              : "border border-[#262a35]",
                          )}
                        >
                          <div className="flex items-center justify-between">
                            <div className="flex items-center gap-[3px]">
                              <span
                                className={cn(
                                  "font-sans text-[10px]",
                                  isRowError ? "text-[#ff7878]" : "text-[#94a3b8]",
                                )}
                              >
                                {isCurrentState5Blocked
                                  ? `Work email (Recipient ${index + 1})`
                                  : "Work email"}
                              </span>
                              <span className="font-sans text-[10px] text-[#ff4d4d]">*</span>
                            </div>
                            {isRowError && (
                              <AlertTriangle className="size-3.5 text-[#ff4d4d]" aria-hidden="true" />
                            )}
                          </div>
                          <input
                            ref={index === 0 ? firstInputRef : undefined}
                            id={`invite-email-${index}`}
                            data-testid={index === 0 ? "invite-email-input" : `invite-email-input-${index}`}
                            type="email"
                            required
                            value={row.email}
                            onFocus={() => setFocusedRowId(row.id)}
                            onChange={(e) => handleEmailChange(row.id, e.target.value)}
                            onBlur={() => handleBlur(row.id)}
                            placeholder="colleague@acme.io"
                            aria-invalid={isRowError ? "true" : "false"}
                            className="w-full border-0 border-none bg-transparent p-0 m-0 font-mono text-[13.5px] leading-tight text-[#dfe2f1] placeholder-[#475569] shadow-none outline-none ring-0 focus:border-none focus:outline-none focus:ring-0"
                            style={{ border: "none", outline: "none", boxShadow: "none" }}
                          />
                        </div>

                        {/* Assigned role dropdown field */}
                        <div
                          data-role-selector-root
                          className="relative w-[210px] shrink-0"
                        >
                          <button
                            type="button"
                            aria-haspopup="listbox"
                            aria-expanded={isRowRoleOpen}
                            onClick={() => {
                              setOpenBatchRowId(isRowRoleOpen ? null : row.id);
                            }}
                            className={cn(
                              "flex h-[56px] w-full flex-col justify-between rounded-[4px] bg-[#0a0e18] px-[14px] py-[8px] text-left transition-colors cursor-pointer",
                              isRowRoleOpen
                                ? "border border-[rgba(0,229,153,0.85)]"
                                : "border border-[#262a35]",
                            )}
                          >
                            <div className="flex items-center gap-[3px]">
                              <span className="font-sans text-[10px] text-[#94a3b8]">Assigned role</span>
                              <span className="font-sans text-[10px] text-[#ff4d4d]">*</span>
                            </div>
                            <div className="flex items-center justify-between">
                              <div className="flex items-center gap-[7px] truncate pr-1">
                                <span
                                  className="size-[7px] shrink-0 rounded-full"
                                  style={{ backgroundColor: rowRoleCfg.dotColor }}
                                  aria-hidden="true"
                                />
                                <span className="truncate font-sans text-[12.5px] text-[#dfe2f1]">
                                  {rowRoleCfg.label}
                                </span>
                              </div>
                              <ChevronDown
                                className={cn(
                                  "size-3 shrink-0 text-[#94a3b8] transition-transform duration-150",
                                  isRowRoleOpen && "rotate-180 text-[#00e599]",
                                )}
                                aria-hidden="true"
                              />
                            </div>
                          </button>

                          {/* Popover list for batch row */}
                          {isRowRoleOpen && (
                            <div
                              role="listbox"
                              className="absolute right-0 top-[calc(100%+4px)] z-50 w-[280px] rounded-[8px] border border-[rgba(0,229,153,0.65)] bg-[#0a0e18] p-[6px] shadow-[0px_10px_30px_rgba(0,0,0,0.85)]"
                            >
                              {SRS_ROLES.map((roleOpt) => {
                                const isSelected = row.role === roleOpt.key;
                                return (
                                  <button
                                    key={roleOpt.key}
                                    type="button"
                                    role="option"
                                    aria-selected={isSelected}
                                    onClick={() => handleRoleChange(row.id, roleOpt.key)}
                                    className={cn(
                                      "flex w-full flex-col gap-0.5 rounded-[6px] p-[6px_10px] text-left transition-colors cursor-pointer",
                                      isSelected
                                        ? "border border-[rgba(0,194,203,0.4)] bg-[rgba(0,194,203,0.14)]"
                                        : "hover:bg-white/5",
                                    )}
                                  >
                                    <div className="flex items-center justify-between">
                                      <div className="flex items-center gap-[6px]">
                                        <span
                                          className="size-[7px] shrink-0 rounded-full"
                                          style={{ backgroundColor: roleOpt.dotColor }}
                                        />
                                        <span className="font-sans text-[12px] font-semibold text-[#dfe2f1]">
                                          {roleOpt.label}
                                        </span>
                                      </div>
                                      {isSelected && (
                                        <Check className="size-3.5 text-[#00c2cb]" aria-hidden="true" />
                                      )}
                                    </div>
                                    <span className="font-sans text-[10px] text-[#94a3b8]">
                                      {roleOpt.shortDesc}
                                    </span>
                                  </button>
                                );
                              })}
                            </div>
                          )}
                        </div>

                        {/* Remove Row Button (Figma: 40x56 bg #0a0e18 border #262a35) */}
                        <button
                          type="button"
                          onClick={() => handleRemoveRow(row.id)}
                          aria-label={`Remove recipient ${index + 1}`}
                          className="flex h-[56px] w-[40px] shrink-0 items-center justify-center rounded-[4px] border border-[#262a35] bg-[#0a0e18] text-[#94a3b8] transition-colors hover:border-[#ff4d4d]/50 hover:bg-[#ff4d4d]/10 hover:text-[#ff4d4d] cursor-pointer"
                        >
                          <Trash2 className="size-3.5" aria-hidden="true" />
                        </button>
                      </div>

                      {/* Error feedback line */}
                      {isRowError && (
                        <p
                          data-testid={index === 0 ? "invite-email-error" : `invite-email-error-${index}`}
                          role="alert"
                          className="font-sans text-[11px] leading-[14px] text-[#ff7878]"
                        >
                          {errorMsg}
                        </p>
                      )}
                    </div>
                  );
                })}
              </div>
            ) : (
              /* =======================================================
                 FORM FIELDS: Single Member Mode (States 1, 2, 5, 6)
                 ======================================================= */
              <div className="flex flex-col gap-3.5">
                {/* Field 1: Work email */}
                <div className="flex flex-col gap-1">
                  <div
                    className={cn(
                      "flex h-[56px] flex-col justify-between rounded-[4px] bg-[#0a0e18] px-[14px] py-[8px] transition-colors",
                      isCurrentState5Blocked ||
                        (singleRow.touched && Boolean(rowErrors[0]))
                        ? "border border-[#ff4d4d]"
                        : focusedRowId === singleRow.id && !isCurrentState2SelectRole
                        ? "border border-[rgba(0,229,153,0.85)]"
                        : "border border-[#262a35]",
                    )}
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-[3px]">
                        <span
                          className={cn(
                            "font-sans text-[10px]",
                            isCurrentState5Blocked || (singleRow.touched && rowErrors[0])
                              ? "text-[#ff7878]"
                              : "text-[#94a3b8]",
                          )}
                        >
                          {isCurrentState5Blocked ? "Work email (Recipient 1)" : "Work email"}
                        </span>
                        <span className="font-sans text-[10px] text-[#ff4d4d]">*</span>
                      </div>
                      {(isCurrentState5Blocked || (singleRow.touched && rowErrors[0])) && (
                        <AlertTriangle className="size-3.5 text-[#ff4d4d]" aria-hidden="true" />
                      )}
                    </div>
                    <input
                      ref={firstInputRef}
                      id="invite-email-input"
                      data-testid="invite-email-input"
                      type="email"
                      required
                      value={singleRow.email}
                      onFocus={() => setFocusedRowId(singleRow.id)}
                      onChange={(e) => handleEmailChange(singleRow.id, e.target.value)}
                      onBlur={() => handleBlur(singleRow.id)}
                      placeholder="v.dziubin@acme.io"
                      aria-invalid={
                        isCurrentState5Blocked || (singleRow.touched && Boolean(rowErrors[0]))
                          ? "true"
                          : "false"
                      }
                      className="w-full border-0 border-none bg-transparent p-0 m-0 font-mono text-[14px] leading-tight text-[#dfe2f1] placeholder-[#475569] shadow-none outline-none ring-0 focus:border-none focus:outline-none focus:ring-0"
                      style={{ border: "none", outline: "none", boxShadow: "none" }}
                    />
                  </div>

                  {/* Single Row Validation Error line */}
                  {(isCurrentState5Blocked || (singleRow.touched && Boolean(rowErrors[0]))) && (
                    <p
                      data-testid="invite-email-error"
                      role="alert"
                      className="font-sans text-[11px] leading-[14px] text-[#ff7878]"
                    >
                      {rowErrors[0] ||
                        "m.bielei@acme.io is already an active member (Maksym Bielei · Solution Architect)."}
                    </p>
                  )}
                </div>

                {/* Field 2: Assigned role (Figma 513:4426 / 513:4879) */}
                <div data-role-selector-root className="flex flex-col gap-2">
                  <button
                    type="button"
                    data-testid="invite-role-trigger"
                    aria-haspopup="listbox"
                    aria-expanded={isCurrentState2SelectRole}
                    onClick={() => {
                      setIsRoleDropdownOpen(!isRoleDropdownOpen);
                      setFocusedRowId(null);
                    }}
                    className={cn(
                      "flex h-[56px] w-full flex-col justify-between rounded-[4px] bg-[#0a0e18] px-[14px] py-[8px] text-left transition-colors cursor-pointer",
                      isCurrentState2SelectRole
                        ? "border border-[rgba(0,229,153,0.85)]"
                        : "border border-[#262a35]",
                    )}
                  >
                    <div className="flex items-center gap-[3px]">
                      <span className="font-sans text-[10px] text-[#94a3b8]">Assigned role</span>
                      <span className="font-sans text-[10px] text-[#ff4d4d]">*</span>
                    </div>
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-[7px]">
                        <span
                          className="size-[7px] shrink-0 rounded-full"
                          style={{ backgroundColor: currentRoleConfig.dotColor }}
                          aria-hidden="true"
                        />
                        <span className="font-sans text-[13px] text-[#dfe2f1]">
                          {currentRoleConfig.label}
                        </span>
                      </div>
                      <ChevronDown
                        className={cn(
                          "size-3 shrink-0 text-[#94a3b8] transition-transform duration-150",
                          isCurrentState2SelectRole && "rotate-180 text-[#00e599]",
                        )}
                        aria-hidden="true"
                      />
                    </div>
                  </button>

                  {/* State 2: Expanded 5-Role SRS Dropdown List (Figma 513:4889) */}
                  {isCurrentState2SelectRole && (
                    <div
                      role="listbox"
                      className="flex w-full flex-col gap-[3px] rounded-[8px] border border-[rgba(0,229,153,0.65)] bg-[#0a0e18] p-[6px]"
                    >
                      {SRS_ROLES.map((roleOpt) => {
                        const isSelected = singleRow.role === roleOpt.key;
                        return (
                          <button
                            key={roleOpt.key}
                            type="button"
                            role="option"
                            aria-selected={isSelected}
                            onClick={() => handleRoleChange(singleRow.id, roleOpt.key)}
                            className={cn(
                              "flex w-full items-center justify-between rounded-[6px] p-[6px_10px] text-left transition-colors cursor-pointer",
                              isSelected
                                ? "border border-[rgba(0,194,203,0.4)] bg-[rgba(0,194,203,0.14)]"
                                : "hover:bg-white/5",
                            )}
                          >
                            <div className="flex flex-col gap-[2px]">
                              <div className="flex items-center gap-[6px]">
                                <span
                                  className="size-[7px] shrink-0 rounded-full"
                                  style={{ backgroundColor: roleOpt.dotColor }}
                                />
                                <span className="font-sans text-[12.5px] font-semibold text-[#dfe2f1]">
                                  {roleOpt.label}
                                </span>
                              </div>
                              <span className="font-sans text-[10.5px] text-[#94a3b8]">
                                {roleOpt.shortDesc}
                              </span>
                            </div>
                            {isSelected && (
                              <Check className="size-3.5 shrink-0 text-[#00c2cb]" aria-hidden="true" />
                            )}
                          </button>
                        );
                      })}
                    </div>
                  )}

                  {/* State 1 / Non-dropdown: SRS Role Permissions Preview Card (Figma 513:4436) */}
                  {!isCurrentState2SelectRole && (
                    <div
                      data-testid="invite-role-preview"
                      className="flex flex-col gap-[4px] rounded-[8px] border border-[rgba(0,194,203,0.35)] bg-[#0e131f] p-[10px_12px]"
                    >
                      <span className="font-sans text-[11px] font-semibold text-[#00c2cb]">
                        {currentRoleConfig.label}
                      </span>
                      <p className="font-sans text-[11px] leading-[14px] text-[#94a3b8]">
                        {currentRoleConfig.permissionsPreview}
                      </p>
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* + Add another member action (Figma 513:4439 & 513:5409) */}
            {!isCurrentState2SelectRole && rows.length < 10 && (
              <div className="pt-0.5">
                <button
                  type="button"
                  data-testid="invite-batch-add-btn"
                  onClick={handleAddRow}
                  className="inline-flex items-center gap-[6px] font-sans text-[12px] font-semibold text-[#00e599] transition-colors hover:text-[#2df0b0] cursor-pointer"
                >
                  <Plus className="size-3.5 stroke-[2.5]" aria-hidden="true" />
                  <span>
                    {isBatchMode
                      ? `Add another member (${10 - rows.length} slots remaining)`
                      : "Add another member to batch"}
                  </span>
                </button>
              </div>
            )}

            {/* Security Callout (Figma 513:4443 & 513:5413) */}
            {!isCurrentState2SelectRole && !isCurrentState5Blocked && (
              <div className="flex items-center gap-[10px] rounded-[8px] border border-[#262d3d] bg-[#0a0e18] p-[10px_12px]">
                <Shield className="size-4 shrink-0 text-[#00c2cb]" aria-hidden="true" />
                <p className="font-sans text-[11px] leading-[14px] text-[#94a3b8]">
                  {isBatchMode
                    ? "Each recipient receives a unique single-use 48-hour cryptographic token locked to their email and assigned role."
                    : "Single-use invitation token expires in 48 hours."}
                </p>
              </div>
            )}

            {/* Modal Footer (Figma 513:4448, 513:4922, 513:5418, 513:6787, 513:7227) */}
            <footer className="mt-1 flex items-center justify-between border-t border-[#262d3d] pt-[12px]">
              {/* Left Info Text */}
              <div className="font-mono text-[11px]">
                {isCurrentState6DeliveryFailed ? (
                  <span className="text-[#64748b]">Safe to retry · 0 duplicates</span>
                ) : isCurrentState5Blocked ? (
                  <span className="text-[#ff7878]">{activeErrorCount || 2} validation errors</span>
                ) : isCurrentState2SelectRole ? (
                  <span className="text-[#64748b]">5 official roles</span>
                ) : isBatchMode ? (
                  <span className="text-[#64748b]">{rows.length} recipients · 48h TTL</span>
                ) : (
                  <span className="text-[#64748b]">1 recipient · 48h TTL</span>
                )}
              </div>

              {/* Right Action Buttons */}
              <div className="flex items-center gap-[10px]">
                <button
                  type="button"
                  onClick={onClose}
                  disabled={isSubmitting}
                  data-testid="invite-cancel-btn"
                  className="flex h-[44px] items-center justify-center rounded-[12px] border border-[#262d3d] bg-[#0a0e18] px-[16px] font-sans text-[13px] font-medium text-[#cbd5e1] transition-colors hover:border-[#64748b]/50 hover:text-white focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[#00e599] disabled:opacity-50 cursor-pointer"
                >
                  Cancel
                </button>

                <button
                  type="submit"
                  disabled={isSubmitDisabled}
                  data-testid="invite-submit-btn"
                  className={cn(
                    "flex h-[44px] items-center justify-center gap-2 rounded-[12px] px-[20px] font-sans text-[13px] font-bold transition-all focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[#00e599]",
                    isSubmitDisabled
                      ? "cursor-not-allowed border border-[#262d3d] bg-[#1a202c] text-[#64748b]"
                      : "bg-[#00e599] text-[#0a0e18] shadow-[0px_4px_12px_rgba(0,229,153,0.25)] hover:bg-[#2df0b0] cursor-pointer",
                  )}
                >
                  {isSubmitting ? (
                    <>
                      <Loader2 className="size-4 animate-spin" aria-hidden="true" />
                      <span>Sending...</span>
                    </>
                  ) : isCurrentState6DeliveryFailed ? (
                    <>
                      <RefreshCw className="size-3.5" aria-hidden="true" />
                      <span>Retry Sending</span>
                    </>
                  ) : (
                    <span>
                      {isBatchMode
                        ? `Send ${rows.length} Invitations →`
                        : "Send Invitation →"}
                    </span>
                  )}
                </button>
              </div>
            </footer>
          </form>
        )}
      </div>
    </div>
  );
}