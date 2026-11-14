import { apiRequest, publicApiRequest } from "@/lib/api-client";
import type {
  InviteMemberRequest,
  InviteMemberResponse,
  MemberStatus,
  UpdateMemberRoleResponse,
  WorkspaceMember,
} from "@/types/member";

export type {
  InviteMemberRequest,
  InviteMemberResponse,
  UpdateMemberRoleResponse,
  WorkspaceMember,
};

const MEMBERS_ENDPOINT = "/api/v1/members";
const INVITATIONS_ENDPOINT = "/api/v1/invitations";

interface BackendMemberRow {
  id: string;
  email: string;
  name?: string | null;
  role: string;
  status?: string | null;
  last_active?: string | null;
}

interface BackendInvitationRow {
  id: string;
  email: string;
  role: string;
  status: "INVITE_SENT" | "EXPIRED";
  expires_at: string;
  created_at?: string | null;
  invited_by?: string | null;
}

const recentInviteUrlsByKey = new Map<string, string>();

export function getCachedInviteUrl(email: string, id?: string): string | undefined {
  if (id && recentInviteUrlsByKey.has(`id:${id}`)) {
    return recentInviteUrlsByKey.get(`id:${id}`);
  }
  return recentInviteUrlsByKey.get(`email:${email.trim().toLowerCase()}`);
}

const AVATAR_TONES = ["green", "blue", "cyan", "slate", "purple"] as const;

/**
 * Derives a human-readable fallback display name from an email address
 * when the backend member row does not supply a full name.
 */
function deriveNameFromEmail(email: string): string {
  const local = email.split("@")[0] || "User";
  return local
    .split(/[._-]/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(" ");
}

function deriveAvatarColor(email: string): string {
  let hash = 0;
  for (let i = 0; i < email.length; i += 1) {
    hash = (hash * 31 + email.charCodeAt(i)) >>> 0;
  }
  return AVATAR_TONES[hash % AVATAR_TONES.length];
}

function formatLastActive(iso?: string | null): string {
  if (!iso) return "Active";
  const ts = Date.parse(iso);
  if (Number.isNaN(ts)) return "Active";
  const diffSec = Math.max(0, Math.floor((Date.now() - ts) / 1000));
  if (diffSec < 120) return "Just now";
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHours = Math.floor(diffMin / 60);
  if (diffHours < 24) return `${diffHours}h ago`;
  const diffDays = Math.floor(diffHours / 24);
  return `${diffDays}d ago`;
}

function formatExpiresIn(iso?: string | null): string {
  if (!iso) return "48h left";
  const ts = Date.parse(iso);
  if (Number.isNaN(ts)) return "48h left";
  const diffMin = Math.max(1, Math.floor((ts - Date.now()) / 60000));
  if (diffMin < 60) return `${diffMin}m left`;
  const diffHours = Math.ceil(diffMin / 60);
  return `${diffHours}h left`;
}

function formatExpiredAgo(iso?: string | null): string {
  if (!iso) return "Expired";
  const ts = Date.parse(iso);
  if (Number.isNaN(ts)) return "Expired";
  const diffHours = Math.max(1, Math.floor((Date.now() - ts) / 3600000));
  if (diffHours < 24) return `${diffHours}h ago`;
  const diffDays = Math.floor(diffHours / 24);
  return `${diffDays}d ago`;
}

/**
 * Fetches active workspace members (`GET /api/v1/members`) and pending/expired
 * invitations (`GET /api/v1/invitations`) and merges them into the unified table model.
 */
export async function getMembers(
  currentUserEmail?: string | null,
): Promise<WorkspaceMember[]> {
  const [backendMembers, rawInvitations] = await Promise.all([
    apiRequest<BackendMemberRow[]>(MEMBERS_ENDPOINT),
    apiRequest<BackendInvitationRow[]>(INVITATIONS_ENDPOINT).catch(
      () => [] as BackendInvitationRow[],
    ),
  ]);
  const backendInvitations = Array.isArray(rawInvitations) ? rawInvitations : [];

  const normalizedCurrentEmail = currentUserEmail?.trim().toLowerCase() ?? "";

  const mappedMembers: WorkspaceMember[] = backendMembers.map((bm) => {
    const emailLower = bm.email.trim().toLowerCase();
    const status: MemberStatus =
      bm.status === "INVITE_SENT" || bm.status === "EXPIRED"
        ? bm.status
        : "ACTIVE";
    return {
      id: bm.id,
      name: bm.name?.trim() || deriveNameFromEmail(bm.email),
      email: bm.email,
      role: bm.role,
      status,
      recordType: "MEMBER",
      isCurrentUser: normalizedCurrentEmail
        ? emailLower === normalizedCurrentEmail
        : false,
      lastActive: formatLastActive(bm.last_active),
      avatarColor: deriveAvatarColor(emailLower),
    };
  });

  const activeEmails = new Set(
    mappedMembers.map((m) => m.email.trim().toLowerCase()),
  );

  const mappedInvitations: WorkspaceMember[] = [];
  for (const inv of backendInvitations) {
    const emailLower = inv.email.trim().toLowerCase();
    if (activeEmails.has(emailLower)) continue;
    mappedInvitations.push({
      id: inv.id,
      name: deriveNameFromEmail(inv.email),
      email: inv.email,
      role: inv.role,
      status: inv.status,
      recordType: "INVITATION",
      isCurrentUser: false,
      expiresIn: inv.status === "INVITE_SENT" ? formatExpiresIn(inv.expires_at) : undefined,
      expiredAgo: inv.status === "EXPIRED" ? formatExpiredAgo(inv.expires_at) : undefined,
      inviteUrl: getCachedInviteUrl(inv.email, inv.id),
      avatarColor: deriveAvatarColor(emailLower),
    });
  }

  const combined = [...mappedMembers, ...mappedInvitations];

  // Float current signed-in user to the top of the table when matched.
  return combined.sort((a, b) => {
    if (a.isCurrentUser && !b.isCurrentUser) return -1;
    if (!a.isCurrentUser && b.isCurrentUser) return 1;
    return 0;
  });
}

/**
 * Dispatches an email invitation via `POST /api/v1/invitations` and caches any returned one-time link.
 */
export async function sendInvitation(
  data: InviteMemberRequest,
): Promise<InviteMemberResponse> {
  const normalizedEmail = data.email.trim().toLowerCase();
  const res = await apiRequest<InviteMemberResponse>(INVITATIONS_ENDPOINT, {
    method: "POST",
    body: JSON.stringify({
      email: normalizedEmail,
      role: data.role,
    }),
  });
  if (res.invite_url) {
    recentInviteUrlsByKey.set(`email:${normalizedEmail}`, res.invite_url);
    if (res.id) {
      recentInviteUrlsByKey.set(`id:${res.id}`, res.invite_url);
    }
  }
  return res;
}

/**
 * Updates a workspace member's role via `PATCH /api/v1/members/{user_id}/role`.
 */
export async function updateMemberRole(
  userId: string,
  role: string,
): Promise<UpdateMemberRoleResponse> {
  return apiRequest<UpdateMemberRoleResponse>(
    `${MEMBERS_ENDPOINT}/${encodeURIComponent(userId)}/role`,
    {
      method: "PATCH",
      body: JSON.stringify({ role }),
    },
  );
}

/**
 * Updates a pending or expired invitation's assigned role via `PATCH /api/v1/invitations/{invitation_id}/role`.
 */
export async function updateInvitationRole(
  invitationId: string,
  role: string,
): Promise<{ id: string; role: string }> {
  return apiRequest<{ id: string; role: string }>(
    `${INVITATIONS_ENDPOINT}/${encodeURIComponent(invitationId)}/role`,
    {
      method: "PATCH",
      body: JSON.stringify({ role }),
    },
  );
}

/**
 * Removes/deactivates an active workspace member via `DELETE /api/v1/members/{user_id}`.
 */
export async function removeMember(userId: string): Promise<void> {
  await apiRequest<void>(`${MEMBERS_ENDPOINT}/${encodeURIComponent(userId)}`, {
    method: "DELETE",
  });
}

/**
 * Revokes a pending invitation or deletes an expired invitation via `DELETE /api/v1/invitations/{invitation_id}`.
 */
export async function revokeInvitation(invitationId: string): Promise<void> {
  await apiRequest<void>(
    `${INVITATIONS_ENDPOINT}/${encodeURIComponent(invitationId)}`,
    {
      method: "DELETE",
    },
  );
}

/**
 * Accepts a workspace invitation token via public `POST /api/v1/invitations/accept`.
 */
export async function acceptInvitation(
  token: string,
): Promise<InviteMemberResponse> {
  return publicApiRequest<InviteMemberResponse>(
    `${INVITATIONS_ENDPOINT}/accept`,
    {
      method: "POST",
      body: JSON.stringify({ token: token.trim() }),
    },
  );
}
