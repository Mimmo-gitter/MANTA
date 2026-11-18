import type { Role } from "@/lib/permissions";

export type MemberStatus = "ACTIVE" | "INVITE_SENT" | "EXPIRED";

export interface WorkspaceMember {
  id: string;
  name: string;
  email: string;
  role: Role | string;
  status: MemberStatus;
  recordType?: "MEMBER" | "INVITATION";
  isCurrentUser?: boolean;
  lastActive?: string;
  expiresIn?: string;
  expiredAgo?: string;
  invitedBy?: string;
  inviteUrl?: string;
  avatarColor?: string;
}

export interface InviteMemberRequest {
  email: string;
  role: string;
}

export interface InviteMemberResponse {
  status: string;
  id?: string | null;
  expires_at?: string | null;
  invite_url?: string | null;
}

export interface UpdateMemberRoleRequest {
  role: string;
}

export interface UpdateMemberRoleResponse {
  user_id: string;
  role: string;
}

export interface MembersKpiSummary {
  totalMembers: number;
  activeCount: number;
  inviteSentCount: number;
  expiredCount: number;
}
