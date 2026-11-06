import { apiRequest } from "@/lib/api-client";
import type {
  WorkspaceMember,
  InviteMemberRequest,
  InviteMemberResponse,
  UpdateMemberRoleResponse,
} from "@/types/member";

const MEMBERS_ENDPOINT = "/api/v1/members";
const INVITATIONS_ENDPOINT = "/api/v1/invitations";

export const INITIAL_MEMBERS: WorkspaceMember[] = [
  {
    id: "mem-001",
    name: "Pavlo Zhuk",
    email: "p.zhuk@acme.io",
    role: "TENANT_ADMIN",
    status: "ACTIVE",
    isCurrentUser: true,
    lastActive: "Now",
    avatarColor: "green",
  },
  {
    id: "mem-002",
    name: "Maksym Bielei",
    email: "m.bielei@acme.io",
    role: "SOLUTION_ARCHITECT",
    status: "ACTIVE",
    lastActive: "Active 12m ago",
    avatarColor: "blue",
  },
  {
    id: "mem-003",
    name: "Vadym Karban",
    email: "v.karban@acme.io",
    role: "PROJECT_MANAGER",
    status: "ACTIVE",
    lastActive: "Active 1h ago",
    avatarColor: "cyan",
  },
  {
    id: "mem-004",
    name: "Dmytro Hontarenko",
    email: "d.hontarenko@acme.io",
    role: "DEVELOPER",
    status: "ACTIVE",
    lastActive: "Active 3h ago",
    avatarColor: "slate",
  },
  {
    id: "mem-005",
    name: "Bohdan Shcherbak",
    email: "b.shcherbak@acme.io",
    role: "DELIVERY_DIRECTOR",
    status: "INVITE_SENT",
    expiresIn: "41h left",
    avatarColor: "purple",
  },
  {
    id: "mem-006",
    name: "Matvii Yeromenko",
    email: "m.yeromenko@acme.io",
    role: "DEVELOPER",
    status: "INVITE_SENT",
    expiresIn: "18h left",
    avatarColor: "slate",
  },
  {
    id: "mem-007",
    name: "Mykhailo Shevtsov",
    email: "m.shevtsov@acme.io",
    role: "DEVELOPER",
    status: "EXPIRED",
    expiredAgo: "2d ago",
    avatarColor: "amber",
  },
];

const STORAGE_KEY = "manta_workspace_members";

function getStoredMembers(): WorkspaceMember[] {
  if (typeof window === "undefined") {
    return INITIAL_MEMBERS;
  }
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    if (!raw) {
      window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(INITIAL_MEMBERS));
      return INITIAL_MEMBERS;
    }
    return JSON.parse(raw);
  } catch {
    return INITIAL_MEMBERS;
  }
}

function saveStoredMembers(members: WorkspaceMember[]): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(members));
  } catch {
    // Ignore storage quota errors
  }
}

function isDemoMode(): boolean {
  if (typeof window === "undefined") return false;
  return (
    window.location.search.includes("demo=true") ||
    window.sessionStorage.getItem("manta_demo") === "true"
  );
}

/**
 * Derives a display name from an email if not provided by backend.
 */
function deriveNameFromEmail(email: string): string {
  const local = email.split("@")[0] || "User";
  return local
    .split(/[._-]/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(" ");
}

/**
 * Fetches workspace members.
 * Tries the real backend /api/v1/members endpoint first.
 * In demo mode or if offline/error, falls back gracefully to managed stored members.
 */
export async function getMembers(): Promise<WorkspaceMember[]> {
  if (isDemoMode()) {
    return getStoredMembers();
  }

  try {
    interface BackendMemberRow {
      id: string;
      email: string;
      role: string;
    }

    const backendMembers = await apiRequest<BackendMemberRow[]>(MEMBERS_ENDPOINT);

    if (Array.isArray(backendMembers) && backendMembers.length > 0) {
      // Merge with any local pending invites
      const local = getStoredMembers();
      const localPending = local.filter((m) => m.status !== "ACTIVE");

      const mappedBackend: WorkspaceMember[] = backendMembers.map((bm, index) => {
        const localMatch = local.find(
          (m) => m.email.toLowerCase() === bm.email.toLowerCase(),
        );
        return {
          id: bm.id,
          name: localMatch?.name || deriveNameFromEmail(bm.email),
          email: bm.email,
          role: bm.role,
          status: "ACTIVE",
          isCurrentUser: index === 0,
          lastActive: localMatch?.lastActive || "Active recently",
        };
      });

      // Filter out any local pending invites that are now active in backend
      const stillPending = localPending.filter(
        (lp) =>
          !mappedBackend.some(
            (bm) => bm.email.toLowerCase() === lp.email.toLowerCase(),
          ),
      );

      const combined = [...mappedBackend, ...stillPending];
      saveStoredMembers(combined);
      return combined;
    }

    return getStoredMembers();
  } catch (err) {
    // Graceful offline / demo fallback
    return getStoredMembers();
  }
}

/**
 * Sends an email invitation to a new workspace member.
 */
export async function sendInvitation(
  data: InviteMemberRequest,
): Promise<InviteMemberResponse> {
  const normalizedEmail = data.email.trim().toLowerCase();

  // Update local session storage
  const current = getStoredMembers();
  const existingIdx = current.findIndex(
    (m) => m.email.toLowerCase() === normalizedEmail,
  );

  const newMember: WorkspaceMember = {
    id: `inv-${Date.now()}`,
    name: deriveNameFromEmail(normalizedEmail),
    email: normalizedEmail,
    role: data.role,
    status: "INVITE_SENT",
    expiresIn: "48h left",
    avatarColor: "cyan",
  };

  if (existingIdx >= 0) {
    current[existingIdx] = {
      ...current[existingIdx],
      role: data.role,
      status: "INVITE_SENT",
      expiresIn: "48h left",
    };
  } else {
    current.push(newMember);
  }
  saveStoredMembers(current);

  if (isDemoMode()) {
    return { status: "sent" };
  }

  try {
    return await apiRequest<InviteMemberResponse>(INVITATIONS_ENDPOINT, {
      method: "POST",
      body: JSON.stringify({
        email: normalizedEmail,
        role: data.role,
      }),
    });
  } catch (err) {
    // If backend was unreachable but local stored was updated, still allow demo feedback
    if (isDemoMode()) {
      return { status: "sent" };
    }
    throw err;
  }
}

/**
 * Updates a member's assigned role.
 */
export async function updateMemberRole(
  userId: string,
  role: string,
): Promise<UpdateMemberRoleResponse> {
  // Update local session storage
  const current = getStoredMembers();
  const memberIdx = current.findIndex((m) => m.id === userId);
  if (memberIdx >= 0) {
    current[memberIdx] = {
      ...current[memberIdx],
      role,
    };
    saveStoredMembers(current);
  }

  if (isDemoMode()) {
    return { user_id: userId, role };
  }

  try {
    return await apiRequest<UpdateMemberRoleResponse>(
      `${MEMBERS_ENDPOINT}/${encodeURIComponent(userId)}/role`,
      {
        method: "PATCH",
        body: JSON.stringify({ role }),
      },
    );
  } catch (err) {
    if (isDemoMode()) {
      return { user_id: userId, role };
    }
    throw err;
  }
}
