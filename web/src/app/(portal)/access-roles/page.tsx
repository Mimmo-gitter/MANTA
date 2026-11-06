"use client";

import { useCallback, useEffect, useState } from "react";
import { Users } from "lucide-react";
import { MembersTable } from "@/components/members/members-table";
import { InviteMemberDialog } from "@/components/members/invite-member-dialog";
import { getMembers } from "@/lib/members-api";
import type { WorkspaceMember } from "@/types/member";

export default function AccessRolesPage() {
  const [members, setMembers] = useState<WorkspaceMember[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isInviteDialogOpen, setIsInviteDialogOpen] = useState(false);

  const loadMembers = useCallback(async () => {
    try {
      const data = await getMembers();
      setMembers(data);
    } catch {
      // In case of error, setMembers remains as is
    } finally {
      setIsLoading(false);
    }
  }, []);

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
            aria-label="Access & Roles"
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
      {isLoading ? (
        <div className="flex h-[300px] w-full items-center justify-center rounded-[12px] border border-[#262d3d] bg-[#131722]">
          <div className="flex flex-col items-center gap-3 text-[#94a3b8]">
            <Users className="size-8 animate-pulse text-[#00e599]" aria-hidden="true" />
            <span className="text-[13px]">Loading workspace members...</span>
          </div>
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
      />
    </div>
  );
}
