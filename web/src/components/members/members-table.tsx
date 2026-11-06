"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  Check,
  ChevronDown,
  Link2,
  MoreVertical,
  RotateCw,
  Search,
} from "lucide-react";
import type { WorkspaceMember } from "@/types/member";
import { CANONICAL_ROLES, type Role } from "@/lib/permissions";
import { updateMemberRole, sendInvitation, removeMember } from "@/lib/members-api";
import {
  ReissueInviteDialog,
  ConfirmRoleChangeDialog,
  RevokeInviteDialog,
  RemoveMemberDialog,
} from "@/components/members/member-action-dialogs";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 7;

interface MembersTableProps {
  members: WorkspaceMember[];
  onMembersChange: () => void;
  onOpenInviteDialog: () => void;
}

export const ROLE_PILL_STYLES: Record<
  string,
  {
    label: string;
    description: string;
    bg: string;
    border: string;
    text: string;
    dot: string;
  }
> = {
  TENANT_ADMIN: {
    label: "Tenant Administrator",
    description: "Users, RBAC roles, GitHub/Jira/Slack & GDPR Art. 17",
    bg: "bg-[rgba(0,229,153,0.1)]",
    border: "border-[rgba(0,229,153,0.28)]",
    text: "text-[#00e599]",
    dot: "bg-[#00e599]",
  },
  DELIVERY_DIRECTOR: {
    label: "Delivery Director",
    description: "Portfolio delivery health, DORA metrics & threat alerts",
    bg: "bg-[rgba(167,139,250,0.1)]",
    border: "border-[rgba(167,139,250,0.28)]",
    text: "text-[#a78bfa]",
    dot: "bg-[#a78bfa]",
  },
  PROJECT_MANAGER: {
    label: "Project Manager / Scrum Master",
    description: "Velocity, sprint burndowns, blockers & WIP limits",
    bg: "bg-[rgba(0,194,203,0.1)]",
    border: "border-[rgba(0,194,203,0.28)]",
    text: "text-[#00c2cb]",
    dot: "bg-[#00c2cb]",
  },
  SCRUM_MASTER: {
    label: "Project Manager / Scrum Master",
    description: "Velocity, sprint burndowns, blockers & WIP limits",
    bg: "bg-[rgba(0,194,203,0.1)]",
    border: "border-[rgba(0,194,203,0.28)]",
    text: "text-[#00c2cb]",
    dot: "bg-[#00c2cb]",
  },
  SOLUTION_ARCHITECT: {
    label: "Solution Architect",
    description: "Approve PII-sanitized ADRs, SCIP call-graphs & tech debt",
    bg: "bg-[rgba(96,165,250,0.1)]",
    border: "border-[rgba(96,165,250,0.28)]",
    text: "text-[#60a5fa]",
    dot: "bg-[#60a5fa]",
  },
  DEVELOPER: {
    label: "Developer",
    description: "Personal code quality metrics, AI PRs & sandbox tests",
    bg: "bg-[rgba(203,213,225,0.1)]",
    border: "border-[rgba(203,213,225,0.28)]",
    text: "text-[#e2e8f0]",
    dot: "bg-[#cbd5e1]",
  },
  TOP_MANAGEMENT: {
    label: "Top Management",
    description: "Executive portfolio dashboards & strategic KPIs",
    bg: "bg-[rgba(245,158,11,0.1)]",
    border: "border-[rgba(245,158,11,0.28)]",
    text: "text-[#fbbf24]",
    dot: "bg-[#fbbf24]",
  },
};

/**
 * The 5 SRS roles displayed in the Figma Popover (513:3083) and Role Filter Menu.
 */
const SRS_ROLE_OPTIONS: Role[] = [
  "TENANT_ADMIN",
  "DELIVERY_DIRECTOR",
  "PROJECT_MANAGER",
  "SOLUTION_ARCHITECT",
  "DEVELOPER",
];

export function getInitials(name: string, email: string): string {
  if (name && name.trim()) {
    const parts = name.trim().split(/\s+/);
    if (parts.length >= 2) {
      return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
    }
    return parts[0].slice(0, 2).toUpperCase();
  }
  const cleanEmail = email.split("@")[0] || "U";
  return cleanEmail.slice(0, 2).toUpperCase();
}

function getFirstName(member: WorkspaceMember): string {
  if (member.name && member.name.trim()) {
    return member.name.trim().split(/\s+/)[0];
  }
  return (member.email.split("@")[0] || "USER").split(/[._-]/)[0];
}

function getAvatarStyles(color?: string) {
  switch (color) {
    case "green":
      return "bg-[rgba(0,229,153,0.14)] border-[rgba(0,229,153,0.38)] text-[#00e599]";
    case "blue":
      return "bg-[rgba(96,165,250,0.14)] border-[rgba(96,165,250,0.38)] text-[#60a5fa]";
    case "cyan":
      return "bg-[rgba(0,194,203,0.14)] border-[rgba(0,194,203,0.38)] text-[#00c2cb]";
    case "purple":
      return "bg-[rgba(167,139,250,0.14)] border-[rgba(167,139,250,0.38)] text-[#a78bfa]";
    case "amber":
      return "bg-[rgba(245,158,11,0.14)] border-[rgba(245,158,11,0.38)] text-[#f59e0b]";
    default:
      return "bg-[rgba(203,213,225,0.14)] border-[rgba(203,213,225,0.38)] text-[#cbd5e1]";
  }
}

export type ToastVariant = "green" | "blue" | "cyan" | "amber" | "red";

export interface ToastNotification {
  id: string;
  title: string;
  subtitle?: string;
  copyLink?: string;
  variant?: ToastVariant;
}

const TOAST_VARIANT_STYLES: Record<
  ToastVariant,
  {
    border: string;
    iconBg: string;
    iconStroke: string;
    glow: string;
  }
> = {
  green: {
    border: "border-[rgba(0,229,153,0.75)]",
    iconBg: "bg-[rgba(0,229,153,0.16)]",
    iconStroke: "#00e599",
    glow: "shadow-[0px_8px_24px_rgba(0,0,0,0.5)]",
  },
  blue: {
    border: "border-[rgba(96,165,250,0.75)]",
    iconBg: "bg-[rgba(96,165,250,0.16)]",
    iconStroke: "#60a5fa",
    glow: "shadow-[0px_8px_24px_rgba(0,0,0,0.5)]",
  },
  cyan: {
    border: "border-[rgba(0,194,203,0.75)]",
    iconBg: "bg-[rgba(0,194,203,0.16)]",
    iconStroke: "#00c2cb",
    glow: "shadow-[0px_8px_24px_rgba(0,0,0,0.5)]",
  },
  amber: {
    border: "border-[rgba(245,158,11,0.75)]",
    iconBg: "bg-[rgba(245,158,11,0.16)]",
    iconStroke: "#f59e0b",
    glow: "shadow-[0px_8px_24px_rgba(0,0,0,0.5)]",
  },
  red: {
    border: "border-[rgba(255,77,77,0.75)]",
    iconBg: "bg-[rgba(255,77,77,0.16)]",
    iconStroke: "#ff4d4d",
    glow: "shadow-[0px_8px_24px_rgba(0,0,0,0.5)]",
  },
};

export function MembersTable({
  members,
  onMembersChange,
}: MembersTableProps) {
  const [searchQuery, setSearchQuery] = useState("");
  const [roleFilter, setRoleFilter] = useState<string>("ALL");
  const [statusFilter, setStatusFilter] = useState<string>("ALL");
  const [currentPage, setCurrentPage] = useState<number>(1);

  // Mutual-exclusive popover states (only ONE open at a time)
  const [isRoleDropdownOpen, setIsRoleDropdownOpen] = useState(false);
  const [isStatusDropdownOpen, setIsStatusDropdownOpen] = useState(false);
  const [activeRoleSelectorMemberId, setActiveRoleSelectorMemberId] = useState<string | null>(null);
  const [activeMenuMemberId, setActiveMenuMemberId] = useState<string | null>(null);
  const [showSoleAdminTooltip, setShowSoleAdminTooltip] = useState(false);

  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [resendingId, setResendingId] = useState<string | null>(null);
  const [toast, setToast] = useState<ToastNotification | null>(null);
  const [toastCopied, setToastCopied] = useState(false);

  // 4 Action Confirmation Dialog States (Figma 513:7655, 513:8103, 513:8545, 513:8986)
  const [reissueMember, setReissueMember] = useState<WorkspaceMember | null>(null);
  const [roleChangeTarget, setRoleChangeTarget] = useState<{ member: WorkspaceMember; newRole: Role } | null>(null);
  const [revokeMember, setRevokeMember] = useState<WorkspaceMember | null>(null);
  const [removeMemberTarget, setRemoveMemberTarget] = useState<WorkspaceMember | null>(null);

  const roleDropdownRef = useRef<HTMLDivElement>(null);
  const statusDropdownRef = useRef<HTMLDivElement>(null);

  // Close all popovers on outside click
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      const target = event.target as HTMLElement | null;
      if (!target) return;

      if (
        roleDropdownRef.current &&
        !roleDropdownRef.current.contains(target)
      ) {
        setIsRoleDropdownOpen(false);
      }
      if (
        statusDropdownRef.current &&
        !statusDropdownRef.current.contains(target)
      ) {
        setIsStatusDropdownOpen(false);
      }
      if (!target.closest("[data-role-selector]")) {
        setActiveRoleSelectorMemberId(null);
      }
      if (!target.closest("[data-row-menu]")) {
        setActiveMenuMemberId(null);
      }
    }

    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  // Show floating toast with auto-dismiss (Figma 513:6316 / 513:9429)
  const showToast = (
    content:
      | string
      | {
          title: string;
          subtitle?: string;
          copyLink?: string;
          variant?: ToastVariant;
        }
  ) => {
    if (typeof content === "string") {
      const lower = content.toLowerCase();
      const variant: ToastVariant = lower.includes("fail")
        ? "red"
        : lower.includes("revok") || lower.includes("remov")
          ? "amber"
          : lower.includes("copy")
            ? "cyan"
            : lower.includes("role")
              ? "blue"
              : "green";

      setToast({
        id: String(Date.now()),
        title: content,
        subtitle: content.includes("copied")
          ? "Single-use token valid for 48 hours"
          : "Role: Developer · Expires in 48h",
        variant,
      });
    } else {
      setToast({
        id: String(Date.now()),
        variant: "green",
        ...content,
      });
    }
    setToastCopied(false);
  };

  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => {
      setToast(null);
      setToastCopied(false);
    }, 4500);
    return () => clearTimeout(timer);
  }, [toast]);

  // Reset page when filters change
  useEffect(() => {
    setCurrentPage(1);
  }, [searchQuery, roleFilter, statusFilter]);

  // Dynamic KPI & Role counts
  const kpiCounts = useMemo(() => {
    let active = 0;
    let inviteSent = 0;
    let expired = 0;

    for (const m of members) {
      if (m.status === "ACTIVE") active++;
      else if (m.status === "INVITE_SENT") inviteSent++;
      else if (m.status === "EXPIRED") expired++;
    }

    return {
      total: members.length,
      active,
      inviteSent,
      expired,
    };
  }, [members]);

  const roleCounts = useMemo(() => {
    const counts: Record<string, number> = {
      TENANT_ADMIN: 0,
      DELIVERY_DIRECTOR: 0,
      PROJECT_MANAGER: 0,
      SOLUTION_ARCHITECT: 0,
      DEVELOPER: 0,
    };
    for (const m of members) {
      if (m.role === "PROJECT_MANAGER" || m.role === "SCRUM_MASTER") {
        counts.PROJECT_MANAGER++;
      } else if (counts[m.role] !== undefined) {
        counts[m.role]++;
      }
    }
    return counts;
  }, [members]);

  // Filtered members list
  const filteredMembers = useMemo(() => {
    const q = searchQuery.toLowerCase().trim();
    return members.filter((m) => {
      if (q) {
        const matchesName = m.name?.toLowerCase().includes(q);
        const matchesEmail = m.email?.toLowerCase().includes(q);
        if (!matchesName && !matchesEmail) return false;
      }
      if (roleFilter !== "ALL") {
        if (roleFilter === "PROJECT_MANAGER") {
          if (m.role !== "PROJECT_MANAGER" && m.role !== "SCRUM_MASTER") {
            return false;
          }
        } else if (m.role !== roleFilter) {
          return false;
        }
      }
      if (statusFilter !== "ALL" && m.status !== statusFilter) {
        return false;
      }
      return true;
    });
  }, [members, searchQuery, roleFilter, statusFilter]);

  // Pagination calculation: strictly 7 per page
  const totalPages = Math.max(1, Math.ceil(filteredMembers.length / PAGE_SIZE));
  const startIndex = (currentPage - 1) * PAGE_SIZE;
  const paginatedMembers = useMemo(() => {
    return filteredMembers.slice(startIndex, startIndex + PAGE_SIZE);
  }, [filteredMembers, startIndex]);

  // Handlers
  const handleCopyInviteLink = (memberId: string, email: string) => {
    const dummyLink = `${window.location.origin}/accept-invite?token=manta_demo_${encodeURIComponent(email)}`;
    navigator.clipboard?.writeText(dummyLink);
    setCopiedId(memberId);
    showToast({
      title: "One-time invite link copied",
      subtitle: `Invite link copied to clipboard · ${email}`,
      copyLink: dummyLink,
      variant: "cyan",
    });
    setTimeout(() => {
      setCopiedId(null);
    }, 2000);
  };

  const handleCopyEmail = (email: string) => {
    navigator.clipboard?.writeText(email);
    showToast(`Email ${email} copied to clipboard`);
  };

  const handleConfirmReissue = async (targetMember: WorkspaceMember, role: Role) => {
    setResendingId(targetMember.id);
    try {
      await sendInvitation({
        email: targetMember.email,
        role,
      });
      const roleTitle = ROLE_PILL_STYLES[role]?.label || role;
      const inviteUrl = `${window.location.origin}/accept-invite?token=manta_demo_${encodeURIComponent(targetMember.email)}`;
      showToast({
        title: "Invitation re-issued (48h)",
        subtitle: `Invitation resent to ${targetMember.email} · Role: ${roleTitle} · Expires in 48h`,
        copyLink: inviteUrl,
        variant: "green",
      });
      onMembersChange();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "";
      const isRateLimit = msg.includes("429");
      showToast({
        title: isRateLimit ? "Action failed (HTTP 429)" : "Action failed",
        subtitle: isRateLimit
          ? "Rate limit reached. Please retry in 30s."
          : msg || "Failed to resend invitation.",
        variant: "red",
      });
    } finally {
      setResendingId(null);
    }
  };

  const handleResend = async (member: WorkspaceMember) => {
    setResendingId(member.id);
    try {
      await sendInvitation({
        email: member.email,
        role: member.role,
      });
      const roleTitle =
        ROLE_PILL_STYLES[member.role as keyof typeof ROLE_PILL_STYLES]?.label ||
        member.role;
      const inviteUrl = `${window.location.origin}/accept-invite?token=manta_demo_${encodeURIComponent(member.email)}`;
      showToast({
        title: "Invitation re-issued (48h)",
        subtitle: `Invitation resent to ${member.email} · Role: ${roleTitle}`,
        copyLink: inviteUrl,
        variant: "green",
      });
      onMembersChange();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "";
      const isRateLimit = msg.includes("429");
      showToast({
        title: isRateLimit ? "Action failed (HTTP 429)" : "Action failed",
        subtitle: isRateLimit
          ? "Rate limit reached. Please retry in 30s."
          : msg || "Failed to resend invitation.",
        variant: "red",
      });
    } finally {
      setResendingId(null);
    }
  };

  const handleConfirmRevoke = async (memberId: string, email: string) => {
    try {
      await removeMember(memberId);
      showToast({
        title: "Pending invitation revoked",
        subtitle: `Invitation revoked for ${email} · Token invalidated`,
        variant: "amber",
      });
      onMembersChange();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "";
      const isRateLimit = msg.includes("429");
      showToast({
        title: isRateLimit ? "Action failed (HTTP 429)" : "Action failed",
        subtitle: isRateLimit
          ? "Rate limit reached. Please retry in 30s."
          : msg || "Failed to revoke invitation.",
        variant: "red",
      });
    }
  };

  const handleConfirmRemoveMember = async (memberId: string, displayName: string) => {
    try {
      await removeMember(memberId);
      showToast({
        title: "Member removed from workspace",
        subtitle: `Member ${displayName} removed from workspace · Tokens revoked`,
        variant: "amber",
      });
      onMembersChange();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "";
      const isRateLimit = msg.includes("429");
      showToast({
        title: isRateLimit ? "Action failed (HTTP 429)" : "Action failed",
        subtitle: isRateLimit
          ? "Rate limit reached. Please retry in 30s."
          : msg || "Failed to remove member.",
        variant: "red",
      });
    }
  };

  const handleConfirmRoleChange = async (memberId: string, newRole: Role) => {
    try {
      await updateMemberRole(memberId, newRole);
      onMembersChange();
      const roleLabel = ROLE_PILL_STYLES[newRole]?.label || newRole;
      const memberObj = members.find((m) => m.id === memberId);
      const memberName = memberObj?.name || "Member";
      showToast({
        title: "Member role updated",
        subtitle: `Role updated to ${roleLabel} · ${memberName} → ${roleLabel}`,
        variant: "blue",
      });
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "";
      const isRateLimit = msg.includes("429");
      showToast({
        title: isRateLimit ? "Action failed (HTTP 429)" : "Action failed",
        subtitle: isRateLimit
          ? "Rate limit reached. Please retry in 30s."
          : msg || "Failed to update role.",
        variant: "red",
      });
    }
  };

  return (
    <div className="flex w-full flex-col gap-[10px]">
      {/* Floating Notification Toast (Figma 513:6316 / 513:9429) */}
      {toast && (
        <aside
          role="status"
          aria-live="polite"
          data-testid="notification-toast"
          className={cn(
            "fixed top-[58px] right-[24px] z-[100] flex w-[410px] min-h-[56px] items-center justify-between rounded-[10px] border bg-[#131a26] pl-[14px] pr-[12px] py-[12px] backdrop-blur-md animate-in fade-in slide-in-from-top-2 duration-200",
            (TOAST_VARIANT_STYLES[toast.variant || "green"] || TOAST_VARIANT_STYLES.green).border,
            (TOAST_VARIANT_STYLES[toast.variant || "green"] || TOAST_VARIANT_STYLES.green).glow,
          )}
        >
          {/* Left Block: Check / Status Icon + Titles */}
          <div className="flex items-center gap-[10px] overflow-hidden">
            <div
              className={cn(
                "flex size-[28px] shrink-0 items-center justify-center rounded-full",
                (TOAST_VARIANT_STYLES[toast.variant || "green"] || TOAST_VARIANT_STYLES.green).iconBg,
              )}
            >
              {toast.variant === "amber" ? (
                <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true" className="shrink-0">
                  <circle cx="7" cy="7" r="5" stroke="#F59E0B" strokeWidth="1.25" />
                  <line x1="3.5" y1="3.5" x2="10.5" y2="10.5" stroke="#F59E0B" strokeWidth="1.25" strokeLinecap="round" />
                </svg>
              ) : toast.variant === "red" ? (
                <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true" className="shrink-0">
                  <path d="M7 2L12.5 11.5H1.5L7 2Z" stroke="#FF4D4D" strokeWidth="1.25" strokeLinejoin="round" />
                  <line x1="7" y1="5.5" x2="7" y2="8" stroke="#FF4D4D" strokeWidth="1.25" strokeLinecap="round" />
                  <circle cx="7" cy="9.8" r="0.6" fill="#FF4D4D" />
                </svg>
              ) : (
                <svg
                  width="14"
                  height="14"
                  viewBox="0 0 14 14"
                  fill="none"
                  aria-hidden="true"
                  className="shrink-0"
                >
                  <path
                    d="M3.0625 6.5625L5.6875 9.1875L10.9375 3.0625"
                    stroke={(TOAST_VARIANT_STYLES[toast.variant || "green"] || TOAST_VARIANT_STYLES.green).iconStroke}
                    strokeWidth="1.4875"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              )}
            </div>

            <div className="flex flex-col gap-[2px] overflow-hidden text-left">
              <span className="truncate font-sans text-[12px] font-semibold leading-[15px] text-[#f8fafc]">
                {toast.title}
              </span>
              {toast.subtitle && (
                <span className="truncate font-sans text-[10.5px] font-normal leading-[13px] text-[#94a3b8]">
                  {toast.subtitle}
                </span>
              )}
            </div>
          </div>

          {/* Right Block: Copy Link Action + Close Icon */}
          <div className="flex shrink-0 items-center gap-[8px]">
            {toast.copyLink && (
              <button
                type="button"
                data-testid="toast-copy-link-btn"
                onClick={() => {
                  navigator.clipboard?.writeText(toast.copyLink || "");
                  setToastCopied(true);
                  setTimeout(() => setToastCopied(false), 2000);
                }}
                className={cn(
                  "font-sans text-[11px] font-semibold leading-[14px] transition-colors hover:underline focus:outline-none",
                  toast.variant === "cyan" ? "text-[#00c2cb]" : "text-[#00e599]",
                )}
              >
                {toastCopied ? "Copied!" : "Copy link"}
              </button>
            )}

            <button
              type="button"
              data-testid="toast-dismiss-btn"
              onClick={() => setToast(null)}
              aria-label="Dismiss notification"
              className="flex size-[14px] items-center justify-center text-[#64748b] transition-colors hover:text-[#94a3b8] focus:outline-none"
            >
              <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
                <path
                  d="M3 3L9 9M9 3L3 9"
                  stroke="#64748B"
                  strokeWidth="1.125"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </button>
          </div>
        </aside>
      )}

      {/* KPI Summary Strip (Figma 512:596) */}
      <div
        data-testid="members-kpi-strip"
        className="flex h-[38px] w-full shrink-0 items-center justify-between rounded-[8px] border border-[#1e2636] bg-[#111622] px-[16px]"
      >
        <div className="flex items-center gap-[16px] text-[12px] leading-normal">
          <span className="font-sans font-semibold text-[#f8fafc]">
            {kpiCounts.total} members
          </span>
          <span className="size-[3px] shrink-0 rounded-full bg-[#475569]" aria-hidden="true" />
          <span className="font-sans font-medium text-[#00e599]">
            {kpiCounts.active} active
          </span>
          <span className="size-[3px] shrink-0 rounded-full bg-[#475569]" aria-hidden="true" />
          <span className="font-sans font-medium text-[#00c2cb]">
            {kpiCounts.inviteSent} invite sent
          </span>
          <span className="size-[3px] shrink-0 rounded-full bg-[#475569]" aria-hidden="true" />
          <span className="font-sans font-medium text-[#f59e0b]">
            {kpiCounts.expired} expired
          </span>
        </div>
      </div>

      {/* Members Directory Card (Figma 512:610) */}
      <div className="flex w-full flex-col overflow-visible rounded-[12px] border border-[#262d3d] bg-[#131722]">
        {/* Table Toolbar (Figma 513:614) */}
        <div className="flex h-[46px] w-full shrink-0 items-center justify-between rounded-t-[12px] border-b border-[#262d3d] bg-[#171b26] px-[16px] py-[8px]">
          {/* Left Title & Pill (Figma 513:615) */}
          <div className="flex items-center gap-[8px]">
            <h2 className="font-mono text-[11px] font-bold leading-normal text-[#94a3b8]">
              WORKSPACE MEMBERS
            </h2>
            <span className="flex items-start rounded-[99px] border border-[rgba(0,229,153,0.35)] bg-[rgba(0,229,153,0.12)] px-[7px] py-[2px] font-mono text-[11px] font-bold leading-normal text-[#00e599]">
              {filteredMembers.length}
            </span>
          </div>

          {/* Right Controls (Figma 513:619) */}
          <div className="flex items-center gap-[8px]">
            {/* Search Input (Figma 513:620) */}
            <div className="relative flex h-[30px] w-[236px] shrink-0 items-center gap-[8px] rounded-[6px] border border-[#262d3d] bg-[#0a0e18] px-[10px] transition-colors focus-within:border-[#00e599]">
              <Search className="size-[14px] shrink-0 text-[#64748b]" aria-hidden="true" />
              <input
                data-testid="members-search-input"
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search by name or email..."
                className="w-full border-0 border-none bg-transparent p-0 font-sans text-[12px] font-normal leading-normal text-[#f8fafc] placeholder-[#64748b] shadow-none outline-none ring-0 focus:border-none focus:outline-none focus:ring-0"
                style={{ border: "none", outline: "none", boxShadow: "none" }}
              />
              {searchQuery && (
                <button
                  type="button"
                  onClick={() => setSearchQuery("")}
                  aria-label="Clear search"
                  className="text-[#64748b] hover:text-[#e2e8f0]"
                >
                  &times;
                </button>
              )}
            </div>

            {/* Custom Role Filter Dropdown (Styled identically to Figma 513:3059) */}
            <div ref={roleDropdownRef} className="relative shrink-0">
              {/* Native hidden select for E2E testing compatibility */}
              <select
                data-testid="members-role-filter"
                value={roleFilter}
                onChange={(e) => setRoleFilter(e.target.value)}
                className="sr-only"
                aria-label="Filter members by role"
              >
                <option value="ALL">All roles (5)</option>
                {CANONICAL_ROLES.map((r) => (
                  <option key={r} value={r}>
                    {ROLE_PILL_STYLES[r]?.label || r}
                  </option>
                ))}
              </select>

              {/* Styled Trigger Button (Figma 513:625) */}
              <button
                type="button"
                aria-haspopup="listbox"
                aria-expanded={isRoleDropdownOpen}
                onClick={() => {
                  const next = !isRoleDropdownOpen;
                  setIsRoleDropdownOpen(next);
                  setIsStatusDropdownOpen(false);
                  setActiveRoleSelectorMemberId(null);
                  setActiveMenuMemberId(null);
                }}
                className={cn(
                  "flex h-[30px] items-center gap-[6px] rounded-[6px] border pl-[10px] pr-[9px] font-sans text-[12px] font-medium leading-normal transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[#00e599]",
                  isRoleDropdownOpen || roleFilter !== "ALL"
                    ? "border-[rgba(0,229,153,0.55)] bg-[rgba(0,229,153,0.1)] text-[#00e599]"
                    : "border-[#262d3d] bg-[#0a0e18] text-[#cbd5e1] hover:border-[#64748b]/50",
                )}
              >
                <span className="whitespace-nowrap">
                  {roleFilter === "ALL"
                    ? "All roles (5)"
                    : ROLE_PILL_STYLES[roleFilter]?.label || roleFilter}
                </span>
                <ChevronDown
                  className={cn(
                    "size-[12px] shrink-0 transition-transform duration-200",
                    isRoleDropdownOpen || roleFilter !== "ALL"
                      ? "text-[#00e599]"
                      : "text-[#94a3b8]",
                    isRoleDropdownOpen && "rotate-180",
                  )}
                  aria-hidden="true"
                />
              </button>

              {/* Popover — Role Filter Menu (Matching Figma 513:3059 style) */}
              {isRoleDropdownOpen && (
                <div
                  role="listbox"
                  data-testid="role-filter-popover"
                  className="absolute right-0 top-[calc(100%+6px)] z-50 flex w-[264px] flex-col gap-[2px] rounded-[10px] border border-[#2e374b] bg-[#171b26] px-[6px] py-[8px] shadow-[0px_14px_34px_rgba(0,0,0,0.65)]"
                >
                  <div className="px-[8px] pb-[4px] pt-[2px]">
                    <span className="font-mono text-[9.5px] font-bold leading-[13px] text-[#64748b]">
                      FILTER BY ASSIGNED ROLE
                    </span>
                  </div>

                  {/* All roles option */}
                  <button
                    type="button"
                    role="option"
                    aria-selected={roleFilter === "ALL"}
                    onClick={() => {
                      setRoleFilter("ALL");
                      setIsRoleDropdownOpen(false);
                    }}
                    className={cn(
                      "flex h-[27px] w-full items-center justify-between rounded-[6px] px-[8px] py-[6px] text-left transition-colors",
                      roleFilter === "ALL"
                        ? "bg-[rgba(0,229,153,0.12)]"
                        : "hover:bg-[rgba(248,250,252,0.06)]",
                    )}
                  >
                    <div className="flex items-center gap-[7px]">
                      {roleFilter === "ALL" ? (
                        <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true" className="shrink-0">
                          <path d="M2.625 6.375L4.875 8.625L9.375 3.375" stroke="#00E599" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
                        </svg>
                      ) : (
                        <span className="size-[6px] shrink-0 rounded-full bg-[#64748b]" aria-hidden="true" />
                      )}
                      <span
                        className={cn(
                          "font-sans text-[12px] leading-[15px]",
                          roleFilter === "ALL"
                            ? "font-semibold text-[#00e599]"
                            : "font-medium text-[#e2e8f0]",
                        )}
                      >
                        All roles
                      </span>
                    </div>
                    <span className="font-mono text-[11px] font-medium leading-[15px] text-[#64748b]">
                      {kpiCounts.total}
                    </span>
                  </button>

                  {/* 5 SRS Role options */}
                  {SRS_ROLE_OPTIONS.map((r) => {
                    const pill = ROLE_PILL_STYLES[r];
                    const isSelected = roleFilter === r;
                    const count = roleCounts[r] ?? 0;
                    return (
                      <button
                        key={r}
                        type="button"
                        role="option"
                        aria-selected={isSelected}
                        onClick={() => {
                          setRoleFilter(r);
                          setIsRoleDropdownOpen(false);
                        }}
                        className={cn(
                          "flex h-[27px] w-full items-center justify-between rounded-[6px] px-[8px] py-[6px] text-left transition-colors",
                          isSelected
                            ? "bg-[rgba(0,229,153,0.12)]"
                            : "hover:bg-[rgba(248,250,252,0.06)]",
                        )}
                      >
                        <div className="flex items-center gap-[7px] truncate pr-2">
                          {isSelected ? (
                            <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true" className="shrink-0">
                              <path d="M2.625 6.375L4.875 8.625L9.375 3.375" stroke="#00E599" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
                            </svg>
                          ) : (
                            <span
                              className={cn("size-[6px] shrink-0 rounded-full", pill?.dot)}
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
                            {pill?.label || r}
                          </span>
                        </div>
                        <span className="shrink-0 font-mono text-[11px] font-medium leading-[15px] text-[#64748b]">
                          {count}
                        </span>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>

            {/* Custom Status Filter Dropdown (Figma 513:629 & 513:3059) */}
            <div ref={statusDropdownRef} className="relative shrink-0">
              {/* Native hidden select for E2E testing compatibility */}
              <select
                data-testid="members-status-filter"
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
                className="sr-only"
                aria-label="Filter members by status"
              >
                <option value="ALL">All statuses (3)</option>
                <option value="ACTIVE">Active</option>
                <option value="INVITE_SENT">Invite Sent</option>
                <option value="EXPIRED">Expired</option>
              </select>

              {/* Styled Trigger Button (Figma 513:629 & 513:2654) */}
              <button
                type="button"
                aria-haspopup="listbox"
                aria-expanded={isStatusDropdownOpen}
                onClick={() => {
                  const next = !isStatusDropdownOpen;
                  setIsStatusDropdownOpen(next);
                  setIsRoleDropdownOpen(false);
                  setActiveRoleSelectorMemberId(null);
                  setActiveMenuMemberId(null);
                }}
                className={cn(
                  "flex h-[30px] items-center gap-[6px] rounded-[6px] border pl-[10px] pr-[9px] font-sans text-[12px] font-medium leading-normal transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[#00e599]",
                  isStatusDropdownOpen || statusFilter !== "ALL"
                    ? "border-[rgba(0,229,153,0.55)] bg-[rgba(0,229,153,0.1)] text-[#00e599]"
                    : "border-[#262d3d] bg-[#0a0e18] text-[#cbd5e1] hover:border-[#64748b]/50",
                )}
              >
                <span className="whitespace-nowrap">
                  {statusFilter === "ALL"
                    ? "All statuses (3)"
                    : statusFilter === "ACTIVE"
                      ? "Active"
                      : statusFilter === "INVITE_SENT"
                        ? "Invite Sent"
                        : "Expired"}
                </span>
                <ChevronDown
                  className={cn(
                    "size-[12px] shrink-0 transition-transform duration-200",
                    isStatusDropdownOpen || statusFilter !== "ALL"
                      ? "text-[#00e599]"
                      : "text-[#94a3b8]",
                    isStatusDropdownOpen && "rotate-180",
                  )}
                  aria-hidden="true"
                />
              </button>

              {/* Popover — Status Filter Menu (Figma 513:3059) */}
              {isStatusDropdownOpen && (
                <div
                  role="listbox"
                  data-testid="status-filter-popover"
                  className="absolute right-0 top-[calc(100%+6px)] z-50 flex w-[228px] flex-col gap-[2px] rounded-[10px] border border-[#2e374b] bg-[#171b26] px-[6px] py-[8px] shadow-[0px_14px_34px_rgba(0,0,0,0.65)]"
                >
                  <div className="px-[8px] pb-[4px] pt-[2px]">
                    <span className="font-mono text-[9.5px] font-bold leading-[13px] text-[#64748b]">
                      FILTER BY INVITE STATUS
                    </span>
                  </div>

                  {/* All statuses option (Figma 513:3062) */}
                  <button
                    type="button"
                    role="option"
                    aria-selected={statusFilter === "ALL"}
                    onClick={() => {
                      setStatusFilter("ALL");
                      setIsStatusDropdownOpen(false);
                    }}
                    className={cn(
                      "flex h-[27px] w-full items-center justify-between rounded-[6px] px-[8px] py-[6px] text-left transition-colors",
                      statusFilter === "ALL"
                        ? "bg-[rgba(0,229,153,0.12)]"
                        : "hover:bg-[rgba(248,250,252,0.06)]",
                    )}
                  >
                    <div className="flex items-center gap-[7px]">
                      {statusFilter === "ALL" ? (
                        <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true" className="shrink-0">
                          <path d="M2.625 6.375L4.875 8.625L9.375 3.375" stroke="#00E599" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
                        </svg>
                      ) : (
                        <span className="size-[6px] shrink-0 rounded-full bg-[#64748b]" aria-hidden="true" />
                      )}
                      <span
                        className={cn(
                          "font-sans text-[12px] leading-[15px]",
                          statusFilter === "ALL"
                            ? "font-semibold text-[#00e599]"
                            : "font-medium text-[#e2e8f0]",
                        )}
                      >
                        All statuses
                      </span>
                    </div>
                    <span className="font-mono text-[11px] font-medium leading-[15px] text-[#64748b]">
                      {kpiCounts.total}
                    </span>
                  </button>

                  {/* Individual statuses (Figma 513:3068, 513:3073, 513:3078) */}
                  {[
                    { key: "ACTIVE", label: "Active", dot: "bg-[#00e599]", count: kpiCounts.active },
                    { key: "INVITE_SENT", label: "Invite Sent (48h)", dot: "bg-[#00c2cb]", count: kpiCounts.inviteSent },
                    { key: "EXPIRED", label: "Expired", dot: "bg-[#f59e0b]", count: kpiCounts.expired },
                  ].map((s) => {
                    const isSelected = statusFilter === s.key;
                    return (
                      <button
                        key={s.key}
                        type="button"
                        role="option"
                        aria-selected={isSelected}
                        onClick={() => {
                          setStatusFilter(s.key);
                          setIsStatusDropdownOpen(false);
                        }}
                        className={cn(
                          "flex h-[27px] w-full items-center justify-between rounded-[6px] px-[8px] py-[6px] text-left transition-colors",
                          isSelected
                            ? "bg-[rgba(0,229,153,0.12)]"
                            : "hover:bg-[rgba(248,250,252,0.06)]",
                        )}
                      >
                        <div className="flex items-center gap-[7px]">
                          {isSelected ? (
                            <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true" className="shrink-0">
                              <path d="M2.625 6.375L4.875 8.625L9.375 3.375" stroke="#00E599" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
                            </svg>
                          ) : (
                            <span className={cn("size-[6px] shrink-0 rounded-full", s.dot)} aria-hidden="true" />
                          )}
                          <span
                            className={cn(
                              "font-sans text-[12px] leading-[15px]",
                              isSelected
                                ? "font-semibold text-[#00e599]"
                                : "font-medium text-[#e2e8f0]",
                            )}
                          >
                            {s.label}
                          </span>
                        </div>
                        <span className="font-mono text-[11px] font-medium leading-[15px] text-[#64748b]">
                          {s.count}
                        </span>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Column Header (Figma 513:633) */}
        <div className="grid h-[28px] w-full shrink-0 grid-cols-[330fr_256fr_200fr_160fr] items-center gap-[12px] border-b border-[#262d3d] bg-[#0e121b] px-[16px] font-mono text-[10px] font-bold leading-normal text-[#64748b]">
          <div>MEMBER</div>
          <div>ASSIGNED ROLE</div>
          <div>STATUS</div>
          <div className="text-right">ACTIONS</div>
        </div>

        {/* Table Rows (Paginated: strictly 7 items per page) */}
        {paginatedMembers.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-center">
            <p className="font-sans text-[13px] font-medium text-[#94a3b8]">
              No members matching your filters
            </p>
            <button
              type="button"
              onClick={() => {
                setSearchQuery("");
                setRoleFilter("ALL");
                setStatusFilter("ALL");
              }}
              className="mt-2 font-sans text-[12px] font-semibold text-[#00e599] hover:underline"
            >
              Reset filters
            </button>
          </div>
        ) : (
          <div className="divide-y divide-[#1e2433]">
            {paginatedMembers.map((member, index) => {
              const pillStyle = ROLE_PILL_STYLES[member.role] || {
                label: member.role,
                description: "Workspace member role",
                bg: "bg-[rgba(203,213,225,0.1)]",
                border: "border-[rgba(203,213,225,0.28)]",
                text: "text-[#e2e8f0]",
                dot: "bg-[#cbd5e1]",
              };
              const initials = getInitials(member.name, member.email);
              const avatarCls = getAvatarStyles(member.avatarColor);
              const isEven = index % 2 === 1;
              const isMenuOpen = activeMenuMemberId === member.id;
              const isRoleSelectorOpen = activeRoleSelectorMemberId === member.id;
              const openUpwards = index >= 4;

              return (
                <div
                  key={member.id}
                  data-testid={`member-row-${member.id}`}
                  className={cn(
                    "grid h-[50px] w-full grid-cols-[330fr_256fr_200fr_160fr] items-center gap-[12px] px-[16px] transition-colors hover:bg-[#181d2a]",
                    isMenuOpen && member.status === "EXPIRED"
                      ? "bg-[rgba(245,158,11,0.06)]"
                      : isEven
                        ? "bg-[#151a26]"
                        : "bg-[#131722]",
                  )}
                >
                  {/* MEMBER Cell (Figma 513:643) */}
                  <div className="flex items-center gap-[10px] overflow-hidden">
                    <div
                      aria-hidden="true"
                      className={cn(
                        "flex size-[30px] shrink-0 items-center justify-center rounded-[99px] border font-mono text-[11px] font-bold leading-normal",
                        avatarCls,
                      )}
                    >
                      {initials}
                    </div>

                    <div className="flex min-w-0 flex-col items-start gap-[2px]">
                      <div className="flex items-center gap-[6px]">
                        <span className="truncate font-sans text-[13px] font-semibold leading-normal text-[#f8fafc]">
                          {member.name || member.email}
                        </span>
                        {member.isCurrentUser && (
                          <span className="shrink-0 rounded-[4px] border border-[rgba(0,229,153,0.4)] bg-[rgba(0,229,153,0.14)] px-[5px] py-px font-mono text-[9px] font-bold leading-normal text-[#00e599]">
                            YOU
                          </span>
                        )}
                      </div>
                      <span className="truncate font-mono text-[11px] font-normal leading-normal text-[#94a3b8]">
                        {member.email}
                      </span>
                    </div>
                  </div>

                  {/* ASSIGNED ROLE Cell with Inline SRS Role Selector Popover (Figma 513:3083) */}
                  <div className="flex items-center" data-role-selector>
                    <div className="relative inline-flex items-center">
                      {/* Hidden native select for E2E test compatibility */}
                      {!member.isCurrentUser && (
                        <select
                          data-testid={`member-role-select-${member.id}`}
                          value={member.role}
                          onChange={(e) => {
                            const nextRole = e.target.value as Role;
                            if (nextRole !== member.role) {
                              setRoleChangeTarget({ member, newRole: nextRole });
                            }
                          }}
                          aria-label={`Change role for ${member.name || member.email}`}
                          className="sr-only"
                        >
                          {CANONICAL_ROLES.map((r) => (
                            <option key={r} value={r}>
                              {ROLE_PILL_STYLES[r]?.label || r}
                            </option>
                          ))}
                        </select>
                      )}

                      {/* Styled Figma Role Pill Button */}
                      {member.isCurrentUser ? (
                        <div
                          className={cn(
                            "inline-flex items-center gap-[6px] rounded-[6px] border px-[8px] py-[4px] font-sans text-[11px] font-medium leading-normal",
                            pillStyle.bg,
                            pillStyle.border,
                            pillStyle.text,
                          )}
                        >
                          <span
                            className={cn("size-[6px] shrink-0 rounded-full", pillStyle.dot)}
                            aria-hidden="true"
                          />
                          <span className="whitespace-nowrap font-sans font-medium">
                            {pillStyle.label}
                          </span>
                        </div>
                      ) : (
                        <button
                          type="button"
                          aria-haspopup="listbox"
                          aria-expanded={isRoleSelectorOpen}
                          onClick={() => {
                            setActiveRoleSelectorMemberId(
                              isRoleSelectorOpen ? null : member.id,
                            );
                            setActiveMenuMemberId(null);
                            setIsRoleDropdownOpen(false);
                            setIsStatusDropdownOpen(false);
                          }}
                          className={cn(
                            "inline-flex items-center gap-[6px] rounded-[6px] border px-[8px] py-[4px] font-sans text-[11px] font-medium leading-normal transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[#60a5fa]",
                            pillStyle.bg,
                            isRoleSelectorOpen
                              ? "border-[rgba(96,165,250,0.65)] ring-1 ring-[rgba(96,165,250,0.35)]"
                              : pillStyle.border,
                            pillStyle.text,
                          )}
                        >
                          <span
                            className={cn("size-[6px] shrink-0 rounded-full", pillStyle.dot)}
                            aria-hidden="true"
                          />
                          <span className="whitespace-nowrap font-sans font-medium">
                            {pillStyle.label}
                          </span>
                          <ChevronDown
                            className={cn(
                              "size-[11px] shrink-0 opacity-80 transition-transform duration-150",
                              pillStyle.text,
                              isRoleSelectorOpen && "rotate-180",
                            )}
                            aria-hidden="true"
                          />
                        </button>
                      )}

                      {/* Popover — Inline SRS Role Selector (Figma 513:3083) */}
                      {isRoleSelectorOpen && !member.isCurrentUser && (
                        <div
                          role="listbox"
                          data-testid={`inline-role-popover-${member.id}`}
                          className={cn(
                            "absolute left-0 z-50 flex w-[366px] flex-col gap-[3px] rounded-[10px] border border-[rgba(96,165,250,0.6)] bg-[#171b26] p-[8px] shadow-[0px_14px_34px_rgba(0,0,0,0.7)]",
                            openUpwards
                              ? "bottom-[calc(100%+6px)]"
                              : "top-[calc(100%+6px)]",
                          )}
                        >
                          <div className="px-[6px] pb-[4px]">
                            <span className="font-mono text-[9.5px] font-bold leading-[13px] text-[#94a3b8] uppercase">
                              ASSIGN — {(member.name || member.email).toUpperCase()}
                            </span>
                          </div>

                          {SRS_ROLE_OPTIONS.map((roleOpt) => {
                            const optStyle = ROLE_PILL_STYLES[roleOpt];
                            const isSelected =
                              member.role === roleOpt ||
                              (roleOpt === "PROJECT_MANAGER" &&
                                member.role === "SCRUM_MASTER");

                            return (
                              <button
                                key={roleOpt}
                                type="button"
                                role="option"
                                aria-selected={isSelected}
                                onClick={() => {
                                  if (roleOpt !== member.role) {
                                    setRoleChangeTarget({ member, newRole: roleOpt });
                                  }
                                  setActiveRoleSelectorMemberId(null);
                                }}
                                className={cn(
                                  "flex w-full items-center justify-between rounded-[7px] px-[8px] py-[6px] text-left transition-colors",
                                  isSelected
                                    ? "border border-[rgba(96,165,250,0.35)] bg-[rgba(96,165,250,0.14)]"
                                    : "border border-transparent hover:bg-[rgba(248,250,252,0.06)]",
                                )}
                              >
                                <div className="flex flex-col gap-[2px]">
                                  <div className="flex items-center gap-[6px]">
                                    <span
                                      className={cn(
                                        "size-[6px] shrink-0 rounded-full",
                                        optStyle.dot,
                                      )}
                                      aria-hidden="true"
                                    />
                                    <span
                                      className={cn(
                                        "font-sans text-[12px] font-semibold leading-[15px]",
                                        isSelected ? "text-[#60a5fa]" : "text-[#f8fafc]",
                                      )}
                                    >
                                      {optStyle.label}
                                    </span>
                                  </div>
                                  <span className="font-sans text-[10.5px] font-normal leading-[13px] text-[#94a3b8]">
                                    {optStyle.description}
                                  </span>
                                </div>

                                {isSelected && (
                                  <svg
                                    width="14"
                                    height="14"
                                    viewBox="0 0 14 14"
                                    fill="none"
                                    aria-hidden="true"
                                    className="shrink-0"
                                  >
                                    <path
                                      d="M3.0625 7.4375L5.6875 10.0625L10.9375 3.9375"
                                      stroke="#60A5FA"
                                      strokeWidth="1.4"
                                      strokeLinecap="round"
                                      strokeLinejoin="round"
                                    />
                                  </svg>
                                )}
                              </button>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  </div>

                  {/* STATUS Cell (Figma 513:656) */}
                  <div className="flex items-center gap-[6px]">
                    {member.status === "ACTIVE" && (
                      <div className="inline-flex items-center gap-[6px] rounded-[99px] border border-[rgba(0,229,153,0.32)] bg-[rgba(0,229,153,0.12)] px-[8px] py-[3px] font-sans text-[11px] font-semibold leading-normal text-[#00e599]">
                        <span className="size-[6px] shrink-0 rounded-full bg-[#00e599]" aria-hidden="true" />
                        <span>Active</span>
                      </div>
                    )}

                    {member.status === "INVITE_SENT" && (
                      <div className="flex items-center gap-[6px]">
                        <div className="inline-flex items-center gap-[6px] rounded-[99px] border border-[rgba(0,194,203,0.32)] bg-[rgba(0,194,203,0.12)] px-[8px] py-[3px] font-sans text-[11px] font-semibold leading-normal text-[#00c2cb]">
                          <span className="size-[6px] shrink-0 rounded-full bg-[#00c2cb]" aria-hidden="true" />
                          <span>Invite Sent</span>
                        </div>
                        {member.expiresIn && (
                          <span className="whitespace-nowrap font-mono text-[11px] font-normal leading-normal text-[#94a3b8]">
                            · {member.expiresIn}
                          </span>
                        )}
                      </div>
                    )}

                    {member.status === "EXPIRED" && (
                      <div className="flex items-center gap-[6px]">
                        <div className="inline-flex items-center gap-[6px] rounded-[99px] border border-[rgba(245,158,11,0.32)] bg-[rgba(245,158,11,0.12)] px-[8px] py-[3px] font-sans text-[11px] font-semibold leading-normal text-[#f59e0b]">
                          <span className="size-[6px] shrink-0 rounded-full bg-[#f59e0b]" aria-hidden="true" />
                          <span>Expired</span>
                        </div>
                        {member.expiredAgo && (
                          <span className="whitespace-nowrap font-mono text-[11px] font-normal leading-normal text-[#94a3b8]">
                            · {member.expiredAgo}
                          </span>
                        )}
                      </div>
                    )}
                  </div>

                  {/* ACTIONS Cell with Context Menus (Figma 513:3530, 513:3957, 513:3979) */}
                  <div
                    className="relative flex items-center justify-end gap-[8px]"
                    data-row-menu
                    onMouseEnter={() => {
                      if (member.isCurrentUser) setShowSoleAdminTooltip(true);
                    }}
                    onMouseLeave={() => {
                      if (member.isCurrentUser) setShowSoleAdminTooltip(false);
                    }}
                  >
                    {/* Sole-Admin Lockout Protection Tooltip (Figma 513:3523) */}
                    {member.isCurrentUser && showSoleAdminTooltip && (
                      <div
                        role="tooltip"
                        className="absolute bottom-[calc(100%+6px)] right-0 z-50 flex w-[378px] items-start gap-[10px] rounded-[10px] border border-[rgba(0,229,153,0.45)] bg-[#111622] px-[14px] py-[10px] shadow-[0px_12px_30px_rgba(0,0,0,0.75)]"
                      >
                        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true" className="mt-0.5 shrink-0">
                          <rect x="3" y="7" width="10" height="7" rx="1.5" stroke="#00E599" strokeWidth="1.3" />
                          <path d="M5.5 7V5C5.5 3.61929 6.61929 2.5 8 2.5C9.38071 2.5 10.5 3.61929 10.5 5V7" stroke="#00E599" strokeWidth="1.3" strokeLinecap="round" />
                        </svg>
                        <div className="flex flex-col gap-[2px] text-left">
                          <span className="font-sans text-[11.5px] font-semibold leading-[14px] text-[#00e599]">
                            Sole Tenant Administrator Protected
                          </span>
                          <span className="font-sans text-[11px] font-normal leading-[14px] text-[#cbd5e1]">
                            You cannot demote or remove yourself while you are the sole active Tenant Administrator. Assign another admin first.
                          </span>
                        </div>
                      </div>
                    )}

                    {member.isCurrentUser ? (
                      <span className="whitespace-nowrap font-mono text-[10px] font-medium leading-normal text-[#64748b]">
                        Sole Admin
                      </span>
                    ) : member.status === "INVITE_SENT" ? (
                      <button
                        type="button"
                        data-testid={`copy-link-btn-${member.id}`}
                        onClick={() => handleCopyInviteLink(member.id, member.email)}
                        className="inline-flex items-center gap-[4px] rounded-[6px] border border-[#262d3d] bg-[#0a0e18] px-[7px] py-[4px] font-sans text-[11px] font-medium leading-normal text-[#cbd5e1] transition-colors hover:border-[#64748b]/50 hover:text-white"
                      >
                        {copiedId === member.id ? (
                          <>
                            <Check className="size-[11px] shrink-0 text-[#00e599]" aria-hidden="true" />
                            <span className="text-[#00e599]">Copied</span>
                          </>
                        ) : (
                          <>
                            <Link2 className="size-[11px] shrink-0 text-[#94a3b8]" aria-hidden="true" />
                            <span>Copy link</span>
                          </>
                        )}
                      </button>
                    ) : member.status === "EXPIRED" ? (
                      <button
                        type="button"
                        data-testid={`resend-btn-${member.id}`}
                        disabled={resendingId === member.id}
                        onClick={() => setReissueMember(member)}
                        className="inline-flex items-center gap-[4px] rounded-[6px] border border-[rgba(245,158,11,0.45)] bg-[rgba(245,158,11,0.14)] px-[8px] py-[4px] font-sans text-[11px] font-semibold leading-normal text-[#fbbf24] transition-colors hover:bg-[rgba(245,158,11,0.22)]"
                      >
                        <RotateCw
                          className={cn("size-[11px] shrink-0 text-[#fbbf24]", resendingId === member.id && "animate-spin")}
                          aria-hidden="true"
                        />
                        <span>Resend</span>
                      </button>
                    ) : (
                      <span className="whitespace-nowrap font-sans text-[11px] font-normal leading-normal text-[#64748b]">
                        {member.lastActive || "Active recently"}
                      </span>
                    )}

                    {/* Context Row Menu Trigger (Vertical 3 dots "⋮") */}
                    <button
                      type="button"
                      aria-label={`More actions for ${member.name || member.email}`}
                      disabled={member.isCurrentUser}
                      onClick={() => {
                        setActiveMenuMemberId(isMenuOpen ? null : member.id);
                        setActiveRoleSelectorMemberId(null);
                        setIsRoleDropdownOpen(false);
                        setIsStatusDropdownOpen(false);
                      }}
                      className={cn(
                        "flex size-[26px] shrink-0 items-center justify-center rounded-[6px] border transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[#00e599]",
                        member.isCurrentUser
                          ? "border-[rgba(38,45,61,0.4)] bg-[rgba(10,14,24,0.4)] text-[#64748b] opacity-40 cursor-not-allowed"
                          : isMenuOpen
                            ? member.status === "EXPIRED"
                              ? "border-[#f59e0b] bg-[rgba(245,158,11,0.14)] text-[#fbbf24]"
                              : member.status === "INVITE_SENT"
                                ? "border-[#00c2cb] bg-[rgba(0,194,203,0.14)] text-[#00c2cb]"
                                : "border-[#00e599] bg-[rgba(0,229,153,0.14)] text-[#00e599]"
                            : "border-[#262d3d] bg-[#0a0e18] text-[#94a3b8] hover:border-[#64748b]/50 hover:text-[#e2e8f0]",
                      )}
                    >
                      <MoreVertical className="size-[14px]" aria-hidden="true" />
                    </button>

                    {/* Context Menu Popover (Only ONE open at a time) */}
                    {isMenuOpen && (
                      <div
                        role="menu"
                        className={cn(
                          "absolute right-0 z-50 flex flex-col gap-[2px] rounded-[10px] border border-[#2e374b] bg-[#171b26] px-[6px] py-[7px] shadow-[0px_14px_34px_rgba(0,0,0,0.75)]",
                          member.status === "ACTIVE" ? "w-[250px]" : "w-[258px]",
                          openUpwards
                            ? "bottom-[calc(100%+6px)]"
                            : "top-[calc(100%+6px)]",
                        )}
                      >
                        {member.status === "ACTIVE" && (
                          <>
                            {/* Context Menu — Active Member (Figma 513:3530) */}
                            <div className="px-[8px] pb-[4px] pt-[2px] text-left">
                              <span className="font-mono text-[9.5px] font-bold leading-[13px] text-[#64748b] uppercase">
                                {(member.name || member.email).toUpperCase()} · ACTIVE
                              </span>
                            </div>

                            <button
                              type="button"
                              role="menuitem"
                              onClick={() => {
                                setActiveMenuMemberId(null);
                                setActiveRoleSelectorMemberId(member.id);
                              }}
                              className="flex h-[27px] w-full items-center gap-[8px] rounded-[6px] bg-[rgba(248,250,252,0.1)] px-[8px] py-[6px] text-left font-sans text-[12px] font-semibold leading-[15px] text-[#f8fafc] transition-colors hover:bg-[rgba(248,250,252,0.16)]"
                            >
                              <svg width="13" height="13" viewBox="0 0 13 13" fill="none" aria-hidden="true" className="shrink-0">
                                <path d="M7.71875 4.46875C7.71875 5.59058 6.80933 6.5 5.6875 6.5C4.56567 6.5 3.65625 5.59058 3.65625 4.46875C3.65625 3.34692 4.56567 2.4375 5.6875 2.4375C6.80933 2.4375 7.71875 3.34692 7.71875 4.46875Z" stroke="#F8FAFC" strokeWidth="1.05625" />
                                <path d="M2.03125 10.5625C2.03125 8.775 3.65625 7.71875 5.6875 7.71875M8.53125 10.1562L10.9688 7.71875L9.75 6.5L7.3125 8.9375V10.1562H8.53125Z" stroke="#F8FAFC" strokeWidth="1.05625" strokeLinecap="round" strokeLinejoin="round" />
                              </svg>
                              <span>Change assigned role...</span>
                            </button>

                            <button
                              type="button"
                              role="menuitem"
                              onClick={() => {
                                handleCopyEmail(member.email);
                                setActiveMenuMemberId(null);
                              }}
                              className="flex h-[27px] w-full items-center gap-[8px] rounded-[6px] px-[8px] py-[6px] text-left font-sans text-[12px] font-medium leading-[15px] text-[#cbd5e1] transition-colors hover:bg-[rgba(248,250,252,0.08)] hover:text-[#f8fafc]"
                            >
                              <svg width="13" height="13" viewBox="0 0 13 13" fill="none" aria-hidden="true" className="shrink-0">
                                <path d="M5.6875 4.46875H9.34375C10.0168 4.46875 10.5625 5.0144 10.5625 5.6875V9.34375C10.5625 10.0168 10.0168 10.5625 9.34375 10.5625H5.6875C5.0144 10.5625 4.46875 10.0168 4.46875 9.34375V5.6875C4.46875 5.0144 5.0144 4.46875 5.6875 4.46875Z" stroke="#CBD5E1" strokeWidth="1.05625" />
                                <path d="M8.53125 4.46875V3.25C8.53125 2.92677 8.40285 2.61677 8.17429 2.38821C7.94573 2.15965 7.63573 2.03125 7.3125 2.03125H3.25C2.92677 2.03125 2.61677 2.15965 2.38821 2.38821C2.15965 2.61677 2.03125 2.92677 2.03125 3.25V7.3125C2.03125 7.63573 2.15965 7.94573 2.38821 8.17429C2.61677 8.40285 2.92677 8.53125 3.25 8.53125H4.46875" stroke="#CBD5E1" strokeWidth="1.05625" />
                              </svg>
                              <span>Copy work email</span>
                            </button>

                            <button
                              type="button"
                              role="menuitem"
                              onClick={() => {
                                setActiveMenuMemberId(null);
                                setActiveRoleSelectorMemberId(member.id);
                              }}
                              className="flex h-[27px] w-full items-center gap-[8px] rounded-[6px] px-[8px] py-[6px] text-left font-sans text-[12px] font-medium leading-[15px] text-[#cbd5e1] transition-colors hover:bg-[rgba(248,250,252,0.08)] hover:text-[#f8fafc]"
                            >
                              <svg width="13" height="13" viewBox="0 0 13 13" fill="none" aria-hidden="true" className="shrink-0">
                                <path d="M6.5 1.625L10.5625 3.25V6.3375C10.5625 8.85625 8.85625 10.8875 6.5 11.5375C4.14375 10.8875 2.4375 8.85625 2.4375 6.3375V3.25L6.5 1.625Z" stroke="#CBD5E1" strokeWidth="1.05625" strokeLinejoin="round" />
                              </svg>
                              <span>View role permissions</span>
                            </button>

                            <div className="my-px h-px w-full bg-[#262d3d]" />

                            <button
                              type="button"
                              role="menuitem"
                              onClick={() => {
                                setRemoveMemberTarget(member);
                                setActiveMenuMemberId(null);
                              }}
                              className="flex h-[27px] w-full items-center gap-[8px] rounded-[6px] px-[8px] py-[6px] text-left font-sans text-[12px] font-semibold leading-[15px] text-[#ff6b6b] transition-colors hover:bg-[rgba(255,107,107,0.12)]"
                            >
                              <svg width="13" height="13" viewBox="0 0 13 13" fill="none" aria-hidden="true" className="shrink-0">
                                <path d="M2.4375 3.65625H3.65625M3.65625 3.65625H4.875M3.65625 3.65625L4.225 10.5625C4.225 10.778 4.3106 10.9847 4.46298 11.137C4.61535 11.2894 4.82201 11.375 5.0375 11.375H7.9625C8.17799 11.375 8.38465 11.2894 8.53702 11.137C8.6894 10.9847 8.775 10.778 8.775 10.5625L9.34375 3.65625M4.875 3.65625H8.125M4.875 3.65625V2.4375C4.875 2.22201 4.9606 2.01535 5.11298 1.86298C5.26535 1.7106 5.47201 1.625 5.6875 1.625H7.3125C7.52799 1.625 7.73465 1.7106 7.88702 1.86298C8.0394 2.01535 8.125 2.22201 8.125 2.4375V3.65625M8.125 3.65625H9.34375M9.34375 3.65625H10.5625" stroke="#FF6B6B" strokeWidth="1.05625" strokeLinecap="round" strokeLinejoin="round" />
                              </svg>
                              <span>Remove from workspace...</span>
                            </button>
                          </>
                        )}

                        {member.status === "INVITE_SENT" && (
                          <>
                            {/* Context Menu — Invite Sent (Figma 513:3957) */}
                            <div className="px-[8px] pb-[4px] pt-[2px] text-left">
                              <span className="font-mono text-[9.5px] font-bold leading-[13px] text-[#00c2cb] uppercase">
                                INVITE SENT · {(member.expiresIn || "48h left").toUpperCase()} ({getFirstName(member).toUpperCase()})
                              </span>
                            </div>

                            <button
                              type="button"
                              role="menuitem"
                              onClick={() => {
                                handleCopyInviteLink(member.id, member.email);
                                setActiveMenuMemberId(null);
                              }}
                              className="flex h-[27px] w-full items-center gap-[8px] rounded-[6px] bg-[rgba(0,194,203,0.1)] px-[8px] py-[6px] text-left font-sans text-[12px] font-semibold leading-[15px] text-[#00c2cb] transition-colors hover:bg-[rgba(0,194,203,0.16)]"
                            >
                              <svg width="13" height="13" viewBox="0 0 13 13" fill="none" aria-hidden="true" className="shrink-0">
                                <path d="M5.28119 7.71869L7.71869 5.28119M4.46869 6.09369L3.65619 6.90619C3.33296 7.22942 3.15137 7.66782 3.15137 8.12494C3.15137 8.58206 3.33296 9.02046 3.65619 9.34369C3.97942 9.66692 4.41782 9.84851 4.87494 9.84851C5.33206 9.84851 5.77046 9.66692 6.09369 9.34369L6.90619 8.53119M8.53119 6.90619L9.34369 6.09369C9.66692 5.77046 9.84851 5.33206 9.84851 4.87494C9.84851 4.41782 9.66692 3.97942 9.34369 3.65619C9.02046 3.33296 8.58206 3.15137 8.12494 3.15137C7.66782 3.15137 7.22942 3.33296 6.90619 3.65619L6.09369 4.46869" stroke="#00C2CB" strokeWidth="1.05625" strokeLinecap="round" />
                              </svg>
                              <span>Copy one-time invite link</span>
                            </button>

                            <button
                              type="button"
                              role="menuitem"
                              onClick={() => {
                                setReissueMember(member);
                                setActiveMenuMemberId(null);
                              }}
                              className="flex h-[27px] w-full items-center gap-[8px] rounded-[6px] px-[8px] py-[6px] text-left font-sans text-[12px] font-medium leading-[15px] text-[#e2e8f0] transition-colors hover:bg-[rgba(248,250,252,0.08)]"
                            >
                              <svg width="13" height="13" viewBox="0 0 13 13" fill="none" aria-hidden="true" className="shrink-0">
                                <path d="M10.5626 6.5C10.576 7.4405 10.2626 8.35651 9.67593 9.09172C9.08927 9.82694 8.26566 10.3358 7.34564 10.5315C6.42562 10.7272 5.46621 10.5975 4.63111 10.1647C3.79602 9.73189 3.13698 9.0227 2.76646 8.15816C2.39594 7.29361 2.3369 6.32729 2.59941 5.42406C2.86192 4.52084 3.42971 3.7367 4.2059 3.20543C4.9821 2.67416 5.91859 2.42869 6.85558 2.51091C7.79258 2.59312 8.67201 2.99793 9.34383 3.65625M10.5626 2.4375V4.875H8.12508" stroke="#E2E8F0" strokeWidth="1.1375" strokeLinecap="round" strokeLinejoin="round" />
                              </svg>
                              <span>Resend invitation email</span>
                            </button>

                            <button
                              type="button"
                              role="menuitem"
                              onClick={() => {
                                setActiveMenuMemberId(null);
                                setActiveRoleSelectorMemberId(member.id);
                              }}
                              className="flex h-[27px] w-full items-center gap-[8px] rounded-[6px] px-[8px] py-[6px] text-left font-sans text-[12px] font-medium leading-[15px] text-[#cbd5e1] transition-colors hover:bg-[rgba(248,250,252,0.08)] hover:text-[#f8fafc]"
                            >
                              <svg width="13" height="13" viewBox="0 0 13 13" fill="none" aria-hidden="true" className="shrink-0">
                                <path d="M7.71875 4.46875C7.71875 5.59058 6.80933 6.5 5.6875 6.5C4.56567 6.5 3.65625 5.59058 3.65625 4.46875C3.65625 3.34692 4.56567 2.4375 5.6875 2.4375C6.80933 2.4375 7.71875 3.34692 7.71875 4.46875Z" stroke="#CBD5E1" strokeWidth="1.05625" />
                                <path d="M2.03125 10.5625C2.03125 8.775 3.65625 7.71875 5.6875 7.71875M8.53125 10.1562L10.9688 7.71875L9.75 6.5L7.3125 8.9375V10.1562H8.53125Z" stroke="#CBD5E1" strokeWidth="1.05625" strokeLinecap="round" strokeLinejoin="round" />
                              </svg>
                              <span>Change invited role...</span>
                            </button>

                            <div className="my-px h-px w-full bg-[#262d3d]" />

                            <button
                              type="button"
                              role="menuitem"
                              onClick={() => {
                                setRevokeMember(member);
                                setActiveMenuMemberId(null);
                              }}
                              className="flex h-[27px] w-full items-center gap-[8px] rounded-[6px] px-[8px] py-[6px] text-left font-sans text-[12px] font-semibold leading-[15px] text-[#ff6b6b] transition-colors hover:bg-[rgba(255,107,107,0.12)]"
                            >
                              <svg width="13" height="13" viewBox="0 0 13 13" fill="none" aria-hidden="true" className="shrink-0">
                                <path d="M11.2126 6.5001C11.2126 9.10274 9.10274 11.2126 6.5001 11.2126C3.89746 11.2126 1.7876 9.10274 1.7876 6.5001C1.7876 3.89746 3.89746 1.7876 6.5001 1.7876C9.10274 1.7876 11.2126 3.89746 11.2126 6.5001Z" stroke="#FF6B6B" strokeWidth="1.05625" />
                                <path d="M3.25 9.75L9.75 3.25" stroke="#FF6B6B" strokeWidth="1.05625" />
                              </svg>
                              <span>Revoke invitation...</span>
                            </button>
                          </>
                        )}

                        {member.status === "EXPIRED" && (
                          <>
                            {/* Context Menu — Expired Invite (Figma 513:3979) */}
                            <div className="px-[8px] pb-[4px] pt-[2px] text-left">
                              <span className="font-mono text-[9.5px] font-bold leading-[13px] text-[#fbbf24] uppercase">
                                EXPIRED · {(member.expiredAgo || "2d ago").toUpperCase()} ({getFirstName(member).toUpperCase()})
                              </span>
                            </div>

                            <button
                              type="button"
                              role="menuitem"
                              onClick={() => {
                                setReissueMember(member);
                                setActiveMenuMemberId(null);
                              }}
                              className="flex h-[27px] w-full items-center gap-[8px] rounded-[6px] bg-[rgba(251,191,36,0.1)] px-[8px] py-[6px] text-left font-sans text-[12px] font-semibold leading-[15px] text-[#fbbf24] transition-colors hover:bg-[rgba(251,191,36,0.16)]"
                            >
                              <svg width="13" height="13" viewBox="0 0 13 13" fill="none" aria-hidden="true" className="shrink-0">
                                <path d="M10.5626 6.5C10.576 7.4405 10.2626 8.35651 9.67593 9.09172C9.08927 9.82694 8.26566 10.3358 7.34564 10.5315C6.42562 10.7272 5.46621 10.5975 4.63111 10.1647C3.79602 9.73189 3.13698 9.0227 2.76646 8.15816C2.39594 7.29361 2.3369 6.32729 2.59941 5.42406C2.86192 4.52084 3.42971 3.7367 4.2059 3.20543C4.9821 2.67416 5.91859 2.42869 6.85558 2.51091C7.79258 2.59312 8.67201 2.99793 9.34383 3.65625M10.5626 2.4375V4.875H8.12508" stroke="#FBBF24" strokeWidth="1.1375" strokeLinecap="round" strokeLinejoin="round" />
                              </svg>
                              <span>Re-issue invite (new 48h)...</span>
                            </button>

                            <button
                              type="button"
                              role="menuitem"
                              onClick={() => {
                                setActiveMenuMemberId(null);
                                setActiveRoleSelectorMemberId(member.id);
                              }}
                              className="flex h-[27px] w-full items-center gap-[8px] rounded-[6px] px-[8px] py-[6px] text-left font-sans text-[12px] font-medium leading-[15px] text-[#cbd5e1] transition-colors hover:bg-[rgba(248,250,252,0.08)] hover:text-[#f8fafc]"
                            >
                              <svg width="13" height="13" viewBox="0 0 13 13" fill="none" aria-hidden="true" className="shrink-0">
                                <path d="M7.71875 4.46875C7.71875 5.59058 6.80933 6.5 5.6875 6.5C4.56567 6.5 3.65625 5.59058 3.65625 4.46875C3.65625 3.34692 4.56567 2.4375 5.6875 2.4375C6.80933 2.4375 7.71875 3.34692 7.71875 4.46875Z" stroke="#CBD5E1" strokeWidth="1.05625" />
                                <path d="M2.03125 10.5625C2.03125 8.775 3.65625 7.71875 5.6875 7.71875M8.53125 10.1562L10.9688 7.71875L9.75 6.5L7.3125 8.9375V10.1562H8.53125Z" stroke="#CBD5E1" strokeWidth="1.05625" strokeLinecap="round" strokeLinejoin="round" />
                              </svg>
                              <span>Change invited role...</span>
                            </button>

                            <button
                              type="button"
                              role="menuitem"
                              onClick={() => {
                                handleCopyEmail(member.email);
                                setActiveMenuMemberId(null);
                              }}
                              className="flex h-[27px] w-full items-center gap-[8px] rounded-[6px] px-[8px] py-[6px] text-left font-sans text-[12px] font-medium leading-[15px] text-[#cbd5e1] transition-colors hover:bg-[rgba(248,250,252,0.08)] hover:text-[#f8fafc]"
                            >
                              <svg width="13" height="13" viewBox="0 0 13 13" fill="none" aria-hidden="true" className="shrink-0">
                                <path d="M5.6875 4.46875H9.34375C10.0168 4.46875 10.5625 5.0144 10.5625 5.6875V9.34375C10.5625 10.0168 10.0168 10.5625 9.34375 10.5625H5.6875C5.0144 10.5625 4.46875 10.0168 4.46875 9.34375V5.6875C4.46875 5.0144 5.0144 4.46875 5.6875 4.46875Z" stroke="#CBD5E1" strokeWidth="1.05625" />
                                <path d="M8.53125 4.46875V3.25C8.53125 2.92677 8.40285 2.61677 8.17429 2.38821C7.94573 2.15965 7.63573 2.03125 7.3125 2.03125H3.25C2.92677 2.03125 2.61677 2.15965 2.38821 2.38821C2.15965 2.61677 2.03125 2.92677 2.03125 3.25V7.3125C2.03125 7.63573 2.15965 7.94573 2.38821 8.17429C2.61677 8.40285 2.92677 8.53125 3.25 8.53125H4.46875" stroke="#CBD5E1" strokeWidth="1.05625" />
                              </svg>
                              <span>Copy work email</span>
                            </button>

                            <div className="my-px h-px w-full bg-[#262d3d]" />

                            <button
                              type="button"
                              role="menuitem"
                              onClick={() => {
                                setRevokeMember(member);
                                setActiveMenuMemberId(null);
                              }}
                              className="flex h-[27px] w-full items-center gap-[8px] rounded-[6px] px-[8px] py-[6px] text-left font-sans text-[12px] font-semibold leading-[15px] text-[#ff6b6b] transition-colors hover:bg-[rgba(255,107,107,0.12)]"
                            >
                              <svg width="13" height="13" viewBox="0 0 13 13" fill="none" aria-hidden="true" className="shrink-0">
                                <path d="M2.4375 3.65625H3.65625M3.65625 3.65625H4.875M3.65625 3.65625L4.225 10.5625C4.225 10.778 4.3106 10.9847 4.46298 11.137C4.61535 11.2894 4.82201 11.375 5.0375 11.375H7.9625C8.17799 11.375 8.38465 11.2894 8.53702 11.137C8.6894 10.9847 8.775 10.778 8.775 10.5625L9.34375 3.65625M4.875 3.65625H8.125M4.875 3.65625V2.4375C4.875 2.22201 4.9606 2.01535 5.11298 1.86298C5.26535 1.7106 5.47201 1.625 5.6875 1.625H7.3125C7.52799 1.625 7.73465 1.7106 7.88702 1.86298C8.0394 2.01535 8.125 2.22201 8.125 2.4375V3.65625M8.125 3.65625H9.34375M9.34375 3.65625H10.5625" stroke="#FF6B6B" strokeWidth="1.05625" strokeLinecap="round" strokeLinejoin="round" />
                              </svg>
                              <span>Delete expired invite...</span>
                            </button>
                          </>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {/* Table Footer with Strict 7-per-page Pagination (Figma 513:829) */}
        <div className="flex h-[34px] w-full shrink-0 items-center justify-between rounded-b-[12px] border-t border-[#262d3d] bg-[#0e121b] px-[16px] font-sans text-[11px] font-normal leading-normal text-[#64748b]">
          <span>
            Showing {filteredMembers.length === 0 ? 0 : startIndex + 1}–
            {Math.min(startIndex + PAGE_SIZE, filteredMembers.length)} of {filteredMembers.length} workspace members · Single-use invite links expire after 48h
          </span>

          <div className="flex items-center gap-[4px] font-mono text-[10px] leading-normal">
            {/* Prev Button */}
            <button
              type="button"
              disabled={currentPage <= 1}
              onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
              className={cn(
                "flex h-[22px] items-center justify-center rounded-[5px] border px-[8px] font-medium transition-colors",
                currentPage <= 1
                  ? "border-[rgba(38,45,61,0.6)] bg-[rgba(10,14,24,0.5)] text-[#475569] cursor-not-allowed"
                  : "border-[#262d3d] bg-[#111622] text-[#cbd5e1] hover:bg-[#1a2130] hover:text-[#f8fafc] cursor-pointer",
              )}
            >
              Prev
            </button>

            {/* Page Buttons */}
            {Array.from({ length: totalPages }, (_, i) => i + 1).map((p) => {
              const isActive = p === currentPage;
              return (
                <button
                  key={p}
                  type="button"
                  onClick={() => setCurrentPage(p)}
                  className={cn(
                    "flex h-[22px] items-center justify-center rounded-[5px] border px-[8px] font-bold transition-colors cursor-pointer",
                    isActive
                      ? "border-[rgba(0,229,153,0.4)] bg-[rgba(0,229,153,0.14)] text-[#00e599]"
                      : "border-[rgba(38,45,61,0.6)] bg-[rgba(10,14,24,0.5)] text-[#64748b] hover:bg-[#1a2130] hover:text-[#f8fafc]",
                  )}
                >
                  {p}
                </button>
              );
            })}

            {/* Next Button */}
            <button
              type="button"
              disabled={currentPage >= totalPages}
              onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
              className={cn(
                "flex h-[22px] items-center justify-center rounded-[5px] border px-[8px] font-medium transition-colors",
                currentPage >= totalPages
                  ? "border-[rgba(38,45,61,0.6)] bg-[rgba(10,14,24,0.5)] text-[#475569] cursor-not-allowed"
                  : "border-[#262d3d] bg-[#111622] text-[#cbd5e1] hover:bg-[#1a2130] hover:text-[#f8fafc] cursor-pointer",
              )}
            >
              Next
            </button>
          </div>
        </div>
      </div>

      {/* 4 Action Confirmation Dialogs (Figma 513:7655, 513:8103, 513:8545, 513:8986) */}
      <ReissueInviteDialog
        isOpen={Boolean(reissueMember)}
        member={reissueMember}
        onClose={() => setReissueMember(null)}
        onConfirm={handleConfirmReissue}
      />

      <ConfirmRoleChangeDialog
        isOpen={Boolean(roleChangeTarget)}
        member={roleChangeTarget?.member || null}
        newRole={roleChangeTarget?.newRole || "DEVELOPER"}
        onClose={() => setRoleChangeTarget(null)}
        onConfirm={handleConfirmRoleChange}
      />

      <RevokeInviteDialog
        isOpen={Boolean(revokeMember)}
        member={revokeMember}
        onClose={() => setRevokeMember(null)}
        onConfirm={handleConfirmRevoke}
      />

      <RemoveMemberDialog
        isOpen={Boolean(removeMemberTarget)}
        member={removeMemberTarget}
        onClose={() => setRemoveMemberTarget(null)}
        onConfirm={handleConfirmRemoveMember}
      />
    </div>
  );
}
