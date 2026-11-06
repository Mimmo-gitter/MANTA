"use client";

import { useCallback, useEffect, useState } from "react";
import { Plus, Users } from "lucide-react";
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
    <div className="flex w-full flex-col gap-3.5 pb-12 pt-1">
      {/* Breadcrumb */}
      <nav
        aria-label="Members Breadcrumb"
        data-testid="members-page-breadcrumb"
        className="flex items-center gap-1.5 font-mono text-[11px] text-[#94a3b8]"
      >
        <span>Administration</span>
        <span className="text-[#64748b]">/</span>
        <span className="font-medium text-[#00e599]">Access & Roles</span>
      </nav>

      {/* Page Header */}
      <header className="flex items-center justify-between">
        <div className="flex flex-col gap-0.5">
          <h1
            data-testid="members-page-title"
            aria-label="Access & Roles"
            className="font-['Plus_Jakarta_Sans:Bold'] text-[20px] font-bold leading-[26px] text-[#f8fafc]"
          >
            Members & Access
          </h1>
          <p className="font-['Plus_Jakarta_Sans:Regular'] text-[12px] leading-[16px] text-[#94a3b8]">
            Manage workspace members, role assignments, and pending invitations.
          </p>
        </div>

        {/* Primary Action Button: + Invite Member */}
        <button
          type="button"
          data-testid="invite-member-btn"
          onClick={() => setIsInviteDialogOpen(true)}
          className="inline-flex h-[44px] items-center gap-2 rounded-[12px] bg-[#00e599] px-4 font-['Plus_Jakarta_Sans:SemiBold'] text-[14px] font-semibold text-[#05080f] shadow-[0px_4px_14px_rgba(0,229,153,0.3)] transition-all hover:bg-[#2df0b0] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00e599] focus-visible:ring-offset-2 focus-visible:ring-offset-[#0f131d]"
        >
          <Plus className="size-4 stroke-[2.5]" aria-hidden="true" />
          <span>Invite Member</span>
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
