"use client";

import { useMemo, useState } from "react";
import {
  Check,
  ChevronDown,
  Copy,
  Mail,
  MoreHorizontal,
  RefreshCw,
  Search,
  Shield,
  UserCheck,
} from "lucide-react";
import type { WorkspaceMember, MemberStatus } from "@/types/member";
import { CANONICAL_ROLES, type Role } from "@/lib/permissions";
import { ROLE_CONFIGS } from "./invite-member-dialog";
import { updateMemberRole, sendInvitation } from "@/lib/members-api";
import { cn } from "@/lib/utils";

interface MembersTableProps {
  members: WorkspaceMember[];
  onMembersChange: () => void;
  onOpenInviteDialog: () => void;
}

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

export function MembersTable({
  members,
  onMembersChange,
  onOpenInviteDialog,
}: MembersTableProps) {
  const [searchQuery, setSearchQuery] = useState("");
  const [roleFilter, setRoleFilter] = useState<string>("ALL");
  const [statusFilter, setStatusFilter] = useState<string>("ALL");
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [resendingId, setResendingId] = useState<string | null>(null);
  const [actionSuccessMsg, setActionSuccessMsg] = useState<string | null>(null);

  // Dynamic KPI counts
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

  // Filter members
  const filteredMembers = useMemo(() => {
    const q = searchQuery.toLowerCase().trim();
    return members.filter((m) => {
      // Search
      if (q) {
        const matchesName = m.name?.toLowerCase().includes(q);
        const matchesEmail = m.email?.toLowerCase().includes(q);
        if (!matchesName && !matchesEmail) return false;
      }
      // Role filter
      if (roleFilter !== "ALL" && m.role !== roleFilter) {
        return false;
      }
      // Status filter
      if (statusFilter !== "ALL" && m.status !== statusFilter) {
        return false;
      }
      return true;
    });
  }, [members, searchQuery, roleFilter, statusFilter]);

  const handleCopyInviteLink = (memberId: string, email: string) => {
    const dummyLink = `${window.location.origin}/accept-invite?token=manta_demo_${encodeURIComponent(email)}`;
    navigator.clipboard?.writeText(dummyLink);
    setCopiedId(memberId);
    setActionSuccessMsg(`Invite link copied to clipboard`);
    setTimeout(() => {
      setCopiedId(null);
      setActionSuccessMsg(null);
    }, 2500);
  };

  const handleResend = async (member: WorkspaceMember) => {
    setResendingId(member.id);
    try {
      await sendInvitation({
        email: member.email,
        role: member.role,
      });
      setActionSuccessMsg(`Invitation resent to ${member.email}`);
      onMembersChange();
      setTimeout(() => setActionSuccessMsg(null), 3000);
    } catch {
      setActionSuccessMsg(`Failed to resend invitation`);
    } finally {
      setResendingId(null);
    }
  };

  const handleRoleChange = async (memberId: string, newRole: Role) => {
    try {
      await updateMemberRole(memberId, newRole);
      onMembersChange();
      setActionSuccessMsg(`Role updated to ${ROLE_CONFIGS[newRole]?.label || newRole}`);
      setTimeout(() => setActionSuccessMsg(null), 2500);
    } catch {
      setActionSuccessMsg(`Failed to update role`);
    }
  };

  return (
    <div className="flex w-full flex-col gap-3.5">
      {/* Toast Feedback */}
      {actionSuccessMsg && (
        <div
          role="status"
          aria-live="polite"
          className="flex items-center gap-2 rounded-[8px] border border-[rgba(0,229,153,0.35)] bg-[rgba(0,229,153,0.1)] px-3.5 py-2 text-[12px] text-[#00e599]"
        >
          <Check className="size-4 shrink-0" aria-hidden="true" />
          <span>{actionSuccessMsg}</span>
        </div>
      )}

      {/* KPI Summary Strip */}
      <div
        data-testid="members-kpi-strip"
        className="flex h-[38px] shrink-0 items-center justify-between rounded-[8px] border border-[#1e2636] bg-[#111622] px-4"
      >
        <div className="flex items-center gap-4 text-[12px]">
          <span className="font-['Plus_Jakarta_Sans:SemiBold'] font-semibold text-[#f8fafc]">
            {kpiCounts.total} members
          </span>
          <span className="size-[3px] rounded-full bg-[#334155]" aria-hidden="true" />
          <span className="font-['Plus_Jakarta_Sans:Medium'] font-medium text-[#00e599]">
            {kpiCounts.active} active
          </span>
          <span className="size-[3px] rounded-full bg-[#334155]" aria-hidden="true" />
          <span className="font-['Plus_Jakarta_Sans:Medium'] font-medium text-[#00c2cb]">
            {kpiCounts.inviteSent} invite sent
          </span>
          <span className="size-[3px] rounded-full bg-[#334155]" aria-hidden="true" />
          <span className="font-['Plus_Jakarta_Sans:Medium'] font-medium text-[#f59e0b]">
            {kpiCounts.expired} expired
          </span>
        </div>
      </div>

      {/* Members Directory Card */}
      <div className="flex w-full flex-col overflow-hidden rounded-[12px] border border-[#262d3d] bg-[#131722] shadow-[0px_4px_20px_rgba(0,0,0,0.25)]">
        {/* Table Toolbar */}
        <div className="flex h-[48px] shrink-0 items-center justify-between border-b border-[#262d3d] bg-[#171b26] px-4">
          {/* Left Title & Pill */}
          <div className="flex items-center gap-2">
            <h2 className="font-mono text-[11px] font-bold tracking-[0.45px] text-[#94a3b8]">
              WORKSPACE MEMBERS
            </h2>
            <span className="rounded-full border border-[rgba(0,229,153,0.35)] bg-[rgba(0,229,153,0.12)] px-2 py-0.5 font-mono text-[11px] font-bold text-[#00e599]">
              {filteredMembers.length}
            </span>
          </div>

          {/* Right Controls */}
          <div className="flex items-center gap-2">
            {/* Search Input */}
            <div className="relative flex h-[30px] w-[230px] items-center rounded-[6px] border border-[#262d3d] bg-[#0a0e18] px-2.5">
              <Search className="size-3.5 shrink-0 text-[#64748b]" aria-hidden="true" />
              <input
                data-testid="members-search-input"
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search by name or email..."
                className="w-full bg-transparent pl-2 text-[12px] text-[#f8fafc] placeholder-[#64748b] focus:outline-none"
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

            {/* Role Filter */}
            <div className="relative">
              <select
                data-testid="members-role-filter"
                value={roleFilter}
                onChange={(e) => setRoleFilter(e.target.value)}
                className="h-[30px] appearance-none rounded-[6px] border border-[#262d3d] bg-[#0a0e18] pl-2.5 pr-6 text-[12px] font-medium text-[#cbd5e1] focus:border-[#00e599] focus:outline-none"
              >
                <option value="ALL">All roles ({CANONICAL_ROLES.length})</option>
                {CANONICAL_ROLES.map((r) => (
                  <option key={r} value={r}>
                    {ROLE_CONFIGS[r]?.label || r}
                  </option>
                ))}
              </select>
              <ChevronDown
                className="pointer-events-none absolute right-2 top-2 size-3 text-[#64748b]"
                aria-hidden="true"
              />
            </div>

            {/* Status Filter */}
            <div className="relative">
              <select
                data-testid="members-status-filter"
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
                className="h-[30px] appearance-none rounded-[6px] border border-[#262d3d] bg-[#0a0e18] pl-2.5 pr-6 text-[12px] font-medium text-[#cbd5e1] focus:border-[#00e599] focus:outline-none"
              >
                <option value="ALL">All statuses (3)</option>
                <option value="ACTIVE">Active</option>
                <option value="INVITE_SENT">Invite Sent</option>
                <option value="EXPIRED">Expired</option>
              </select>
              <ChevronDown
                className="pointer-events-none absolute right-2 top-2 size-3 text-[#64748b]"
                aria-hidden="true"
              />
            </div>
          </div>
        </div>

        {/* Column Header */}
        <div className="flex h-[28px] shrink-0 items-center gap-3 border-b border-[#262d3d] bg-[#0e121b] px-4 font-mono text-[10px] font-bold text-[#64748b]">
          <div className="w-[330px] shrink-0">MEMBER</div>
          <div className="w-[256px] shrink-0">ASSIGNED ROLE (SRS RBAC)</div>
          <div className="w-[200px] shrink-0">STATUS</div>
          <div className="w-[160px] shrink-0 text-right">ACTIONS</div>
        </div>

        {/* Table Rows */}
        {filteredMembers.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-center">
            <p className="font-['Plus_Jakarta_Sans:Medium'] text-[13px] text-[#94a3b8]">
              No members matching your filters
            </p>
            <button
              type="button"
              onClick={() => {
                setSearchQuery("");
                setRoleFilter("ALL");
                setStatusFilter("ALL");
              }}
              className="mt-2 text-[12px] font-semibold text-[#00e599] hover:underline"
            >
              Reset filters
            </button>
          </div>
        ) : (
          <div className="divide-y divide-[#1e2433]">
            {filteredMembers.map((member, index) => {
              const roleMeta = ROLE_CONFIGS[member.role] || {
                label: member.role,
                dotColor: "#cbd5e1",
              };
              const initials = getInitials(member.name, member.email);
              const avatarCls = getAvatarStyles(member.avatarColor);
              const isEven = index % 2 === 1;

              return (
                <div
                  key={member.id}
                  data-testid={`member-row-${member.id}`}
                  className={cn(
                    "flex h-[52px] items-center gap-3 px-4 transition-colors hover:bg-[#181d2a]",
                    isEven ? "bg-[#151a26]" : "bg-[#131722]",
                  )}
                >
                  {/* MEMBER Cell (330px) */}
                  <div className="flex w-[330px] shrink-0 items-center gap-2.5">
                    {/* Avatar Initials Circle */}
                    <div
                      aria-hidden="true"
                      className={cn(
                        "flex size-[30px] shrink-0 items-center justify-center rounded-full border font-mono text-[11px] font-bold",
                        avatarCls,
                      )}
                    >
                      {initials}
                    </div>

                    {/* Identity Details */}
                    <div className="flex flex-col gap-0.5 overflow-hidden">
                      <div className="flex items-center gap-1.5">
                        <span className="truncate font-['Plus_Jakarta_Sans:SemiBold'] text-[13px] font-semibold text-[#f8fafc]">
                          {member.name || member.email}
                        </span>
                        {member.isCurrentUser && (
                          <span className="rounded-[4px] border border-[rgba(0,229,153,0.4)] bg-[rgba(0,229,153,0.14)] px-1.5 py-px font-mono text-[9px] font-bold text-[#00e599]">
                            YOU
                          </span>
                        )}
                      </div>
                      <span className="truncate font-mono text-[11px] text-[#94a3b8]">
                        {member.email}
                      </span>
                    </div>
                  </div>

                  {/* ASSIGNED ROLE Cell (256px) */}
                  <div className="flex w-[256px] shrink-0 items-center">
                    {member.isCurrentUser ? (
                      <div className="inline-flex items-center gap-1.5 rounded-[6px] border border-[rgba(0,229,153,0.28)] bg-[rgba(0,229,153,0.1)] px-2 py-1 text-[11px] font-medium text-[#00e599]">
                        <span
                          className="size-1.5 rounded-full"
                          style={{ backgroundColor: roleMeta.dotColor }}
                          aria-hidden="true"
                        />
                        <span>{roleMeta.label}</span>
                      </div>
                    ) : (
                      <div className="relative inline-flex items-center">
                        <select
                          data-testid={`member-role-select-${member.id}`}
                          value={member.role}
                          onChange={(e) =>
                            handleRoleChange(member.id, e.target.value as Role)
                          }
                          aria-label={`Change role for ${member.name || member.email}`}
                          className="h-[28px] appearance-none rounded-[6px] border border-[#262d3d] bg-[#0a0e18] pl-5 pr-6 text-[11px] font-medium text-[#cbd5e1] hover:border-[#64748b]/50 focus:border-[#00e599] focus:outline-none"
                        >
                          {CANONICAL_ROLES.map((r) => (
                            <option key={r} value={r}>
                              {ROLE_CONFIGS[r]?.label || r}
                            </option>
                          ))}
                        </select>
                        <span
                          className="pointer-events-none absolute left-2 size-1.5 rounded-full"
                          style={{ backgroundColor: roleMeta.dotColor }}
                          aria-hidden="true"
                        />
                        <ChevronDown
                          className="pointer-events-none absolute right-2 size-3 text-[#64748b]"
                          aria-hidden="true"
                        />
                      </div>
                    )}
                  </div>

                  {/* STATUS Cell (200px) */}
                  <div className="flex w-[200px] shrink-0 items-center gap-1.5">
                    {member.status === "ACTIVE" && (
                      <div className="inline-flex items-center gap-1.5 rounded-full border border-[rgba(0,229,153,0.32)] bg-[rgba(0,229,153,0.12)] px-2 py-0.5 font-['Plus_Jakarta_Sans:SemiBold'] text-[11px] font-semibold text-[#00e599]">
                        <span className="size-1.5 rounded-full bg-[#00e599]" aria-hidden="true" />
                        <span>Active</span>
                      </div>
                    )}

                    {member.status === "INVITE_SENT" && (
                      <>
                        <div className="inline-flex items-center gap-1.5 rounded-full border border-[rgba(0,194,203,0.32)] bg-[rgba(0,194,203,0.12)] px-2 py-0.5 font-['Plus_Jakarta_Sans:SemiBold'] text-[11px] font-semibold text-[#00c2cb]">
                          <span className="size-1.5 rounded-full bg-[#00c2cb]" aria-hidden="true" />
                          <span>Invite Sent</span>
                        </div>
                        {member.expiresIn && (
                          <span className="font-mono text-[11px] text-[#94a3b8]">
                            · {member.expiresIn}
                          </span>
                        )}
                      </>
                    )}

                    {member.status === "EXPIRED" && (
                      <>
                        <div className="inline-flex items-center gap-1.5 rounded-full border border-[rgba(245,158,11,0.32)] bg-[rgba(245,158,11,0.12)] px-2 py-0.5 font-['Plus_Jakarta_Sans:SemiBold'] text-[11px] font-semibold text-[#f59e0b]">
                          <span className="size-1.5 rounded-full bg-[#f59e0b]" aria-hidden="true" />
                          <span>Expired</span>
                        </div>
                        {member.expiredAgo && (
                          <span className="font-mono text-[11px] text-[#94a3b8]">
                            · {member.expiredAgo}
                          </span>
                        )}
                      </>
                    )}
                  </div>

                  {/* ACTIONS Cell (160px) */}
                  <div className="flex w-[160px] shrink-0 items-center justify-end gap-2">
                    {member.isCurrentUser ? (
                      <span className="font-mono text-[10px] text-[#64748b]">Sole Admin</span>
                    ) : member.status === "INVITE_SENT" ? (
                      <button
                        type="button"
                        data-testid={`copy-link-btn-${member.id}`}
                        onClick={() => handleCopyInviteLink(member.id, member.email)}
                        className="inline-flex h-[26px] items-center gap-1 rounded-[6px] border border-[#262d3d] bg-[#0a0e18] px-2 text-[11px] font-medium text-[#cbd5e1] transition-colors hover:border-[#64748b]/50 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00e599]"
                      >
                        {copiedId === member.id ? (
                          <>
                            <Check className="size-3 text-[#00e599]" aria-hidden="true" />
                            <span className="text-[#00e599]">Copied</span>
                          </>
                        ) : (
                          <>
                            <Copy className="size-3 text-[#94a3b8]" aria-hidden="true" />
                            <span>Copy link</span>
                          </>
                        )}
                      </button>
                    ) : member.status === "EXPIRED" ? (
                      <button
                        type="button"
                        data-testid={`resend-btn-${member.id}`}
                        disabled={resendingId === member.id}
                        onClick={() => handleResend(member)}
                        className="inline-flex h-[26px] items-center gap-1 rounded-[6px] border border-[rgba(245,158,11,0.45)] bg-[rgba(245,158,11,0.14)] px-2 text-[11px] font-semibold text-[#fbbf24] transition-colors hover:bg-[rgba(245,158,11,0.22)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#fbbf24]"
                      >
                        <RefreshCw
                          className={cn(
                            "size-3",
                            resendingId === member.id && "animate-spin",
                          )}
                          aria-hidden="true"
                        />
                        <span>Resend</span>
                      </button>
                    ) : (
                      <span className="text-[11px] text-[#64748b]">
                        {member.lastActive || "Active recently"}
                      </span>
                    )}

                    {/* Context Row Menu Trigger */}
                    <button
                      type="button"
                      aria-label={`More actions for ${member.name || member.email}`}
                      className="flex size-[26px] items-center justify-center rounded-[6px] border border-[#262d3d] bg-[#0a0e18] text-[#64748b] transition-colors hover:border-[#64748b]/50 hover:text-[#e2e8f0] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00e599]"
                    >
                      <MoreHorizontal className="size-3.5" aria-hidden="true" />
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {/* Table Footer */}
        <div className="flex h-[36px] shrink-0 items-center justify-between border-t border-[#262d3d] bg-[#0e121b] px-4 font-['Plus_Jakarta_Sans:Regular'] text-[11px] text-[#64748b]">
          <span>
            Showing 1–{filteredMembers.length} of {members.length} workspace members · Single-use invite links expire after 48h
          </span>
          <div className="flex items-center gap-1 font-mono text-[10px]">
            <span>Page 1 of 1</span>
          </div>
        </div>
      </div>
    </div>
  );
}
