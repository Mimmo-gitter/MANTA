import { apiRequest, publicApiRequest } from "@/lib/api-client";
import type {
  InviteMemberRequest,
  InviteMemberResponse,
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
  role: string;
}

const AVATAR_TONES = ["green", "blue", "cyan", "slate", "purple"] as const;

/**
 * Derives a human-readable fallback display name from an email address
 * when the backend member row only carries `id`, `email`, and `role`.
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

/**
 * Fetches active workspace members from FastAPI `GET /api/v1/members`.
 * Matches `isCurrentUser` strictly by email against the signed-in user's claims.
 */
export async function getMembers(
  currentUserEmail?: string | null,
): Promise<WorkspaceMember[]> {
  const backendMembers = await apiRequest<BackendMemberRow[]>(MEMBERS_ENDPOINT);
  const normalizedCurrentEmail = currentUserEmail?.trim().toLowerCase() ?? "";

  const mapped: WorkspaceMember[] = backendMembers.map((bm) => {
    const emailLower = bm.email.trim().toLowerCase();
    return {
      id: bm.id,
      name: deriveNameFromEmail(bm.email),
      email: bm.email,
      role: bm.role,
      status: "ACTIVE",
      isCurrentUser: normalizedCurrentEmail
        ? emailLower === normalizedCurrentEmail
        : false,
      lastActive: "Active",
      avatarColor: deriveAvatarColor(emailLower),
    };
  });

  // Float current signed-in user to the top of the table when matched.
  return mapped.sort((a, b) => {
    if (a.isCurrentUser && !b.isCurrentUser) return -1;
    if (!a.isCurrentUser && b.isCurrentUser) return 1;
    return 0;
  });
}

/**
 * Dispatches an email invitation via `POST /api/v1/invitations`.
 */
export async function sendInvitation(
  data: InviteMemberRequest,
): Promise<InviteMemberResponse> {
  const normalizedEmail = data.email.trim().toLowerCase();
  return apiRequest<InviteMemberResponse>(INVITATIONS_ENDPOINT, {
    method: "POST",
    body: JSON.stringify({
      email: normalizedEmail,
      role: data.role,
    }),
  });
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
