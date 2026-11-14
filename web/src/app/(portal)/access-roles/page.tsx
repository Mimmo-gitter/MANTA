"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertCircle, RefreshCw, Users } from "lucide-react";
import { useAuth } from "@/providers/auth-provider";
import { MembersTable } from "@/components/members/members-table";
import { InviteMemberDialog } from "@/components/members/invite-member-dialog";
import { getMembers } from "@/lib/members-api";
import type { WorkspaceMember } from "@/types/member";

export default function AccessRolesPage() {
  const { email } = useAuth();
  const [members, setMembers] = useState<WorkspaceMember[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [hasLoadedOnce, setHasLoadedOnce] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [isInviteDialogOpen, setIsInviteDialogOpen] = useState(false);

  const loadMembers = useCallback(async () => {
    setIsLoading(true);
    setLoadError(null);
    try {
      const data = await getMembers(email);
      setMembers(data);
      setHasLoadedOnce(true);
    } catch (err) {
      setLoadError(
        err instanceof Error ? err.message : "Failed to load workspace members.",
      );
    } finally {
      setIsLoading(false);
    }
  }, [email]);

  useEffect(() => {
    loadMembers();
  }, [loadMembers]);

  return (
    <div className="flex w-full flex-col gap-[10px] px-[24px] py-[14px]">
      {/* Breadcrumb (Figma 512:586) */}
      <nav
        aria-label="Members Breadcrumb"
        data-testid="members-page-breadcrumb"
        className="flex items-center gap-[6px] font-mono text-[11px] leading-normal text-[#94a3b8]"
      >
        <span className="font-normal text-[#94a3b8]">Administration</span>
        <span className="font-normal text-[#64748b]">/</span>
        <span className="font-medium text-[#00e599]">Access & Roles</span>
      </nav>

      {/* Page Header (Figma 512:590) */}
      <header className="flex w-full items-center justify-between">
        <div className="flex flex-col items-start gap-[2px]">
          <h1
            data-testid="members-page-title"
            className="font-sans text-[20px] font-bold leading-[26px] text-[#f8fafc]"
          >
            Members & Access
          </h1>
          <p className="font-sans text-[12px] font-normal leading-[16px] text-[#94a3b8]">
            Manage workspace members, role assignments, and pending invitations.
          </p>
        </div>

        {/* Primary Action Button: + Invite Member (Figma 512:594) */}
        <button
          type="button"
          data-testid="invite-member-btn"
          onClick={() => setIsInviteDialogOpen(true)}
          className="inline-flex h-[44px] shrink-0 items-center justify-center rounded-[12px] bg-[#00e599] px-[18px] font-sans text-[14px] font-semibold leading-normal text-[#05080f] transition-colors hover:bg-[#2df0b0] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00e599] focus-visible:ring-offset-2 focus-visible:ring-offset-[#0f131d]"
        >
          <span className="whitespace-pre">+  Invite Member</span>
        </button>
      </header>

      {/* Content Area */}
      {isLoading && !hasLoadedOnce ? (
        <div className="flex h-[300px] w-full items-center justify-center rounded-[12px] border border-[#262d3d] bg-[#131722]">
          <div className="flex flex-col items-center gap-3 text-[#94a3b8]">
            <Users className="size-8 animate-pulse text-[#00e599]" aria-hidden="true" />
            <span className="text-[13px]">Loading workspace members...</span>
          </div>
        </div>
      ) : loadError ? (
        <div
          role="alert"
          data-testid="members-load-error"
          className="flex flex-col items-center justify-center gap-4 rounded-[12px] border border-[#ef4444]/30 bg-[#131722] p-8 text-center"
        >
          <div className="flex size-10 items-center justify-center rounded-full border border-[#ef4444]/30 bg-[#ef4444]/10 text-[#f87171]">
            <AlertCircle className="size-5" aria-hidden="true" />
          </div>
          <div className="flex flex-col gap-1">
            <h2 className="text-[15px] font-semibold text-[#f8fafc]">
              Unable to load workspace members
            </h2>
            <p className="max-w-md text-[13px] text-[#94a3b8]">{loadError}</p>
          </div>
          <button
            type="button"
            data-testid="members-retry-btn"
            onClick={() => void loadMembers()}
            className="inline-flex h-[36px] items-center gap-2 rounded-[8px] border border-[#262d3d] bg-[#0a0e18] px-4 text-[12px] font-medium text-[#e2e8f0] transition-colors hover:border-[#00e599]/50 hover:text-[#00e599] cursor-pointer"
          >
            <RefreshCw className="size-3.5" aria-hidden="true" />
            <span>Retry</span>
          </button>
        </div>
      ) : (
        <MembersTable
          members={members}
          onMembersChange={loadMembers}
          onOpenInviteDialog={() => setIsInviteDialogOpen(true)}
        />
      )}

      {/* Invite Member Dialog Modal */}
      <InviteMemberDialog
        isOpen={isInviteDialogOpen}
        onClose={() => setIsInviteDialogOpen(false)}
        onSuccess={loadMembers}
        existingMembers={members}
      />
    </div>
  );
}
