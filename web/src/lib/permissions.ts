import matrixData from "@/config/roles-permissions.json";

export type Role =
  | "DEVELOPER"
  | "SCRUM_MASTER"
  | "PROJECT_MANAGER"
  | "SOLUTION_ARCHITECT"
  | "DELIVERY_DIRECTOR"
  | "TOP_MANAGEMENT"
  | "TENANT_ADMIN";

export type ActionId =
  | "VIEW_PROJECT_DORA"
  | "VIEW_PORTFOLIO_OVERVIEW"
  | "INGEST_PROJECT_DEPLOYMENTS"
  | "VIEW_SPRINT_BURNDOWN_WIP"
  | "RECEIVE_SCOPE_CREEP_ALERT"
  | "REASSIGN_PR_REVIEWER_MOBILE"
  | "VIEW_OWN_CODE_QUALITY"
  | "VIEW_TECH_DEBT_AND_SCIP_GRAPH"
  | "TRIGGER_REFACTOR_ANALYSIS"
  | "SEARCH_ADR"
  | "APPROVE_ADR_TENANT_PRIVATE"
  | "PUBLISH_GLOBAL_ADR"
  | "MANAGE_WEBHOOK_INTEGRATIONS"
  | "MANAGE_TENANT_USERS_AND_ROLES";

export type PermissionDecision = "ALLOW" | "DENY";

export const CANONICAL_ROLES: readonly Role[] = [
  "DEVELOPER",
  "SCRUM_MASTER",
  "PROJECT_MANAGER",
  "SOLUTION_ARCHITECT",
  "DELIVERY_DIRECTOR",
  "TOP_MANAGEMENT",
  "TENANT_ADMIN",
] as const;

export const CANONICAL_ACTIONS: readonly ActionId[] = [
  "VIEW_PROJECT_DORA",
  "VIEW_PORTFOLIO_OVERVIEW",
  "INGEST_PROJECT_DEPLOYMENTS",
  "VIEW_SPRINT_BURNDOWN_WIP",
  "RECEIVE_SCOPE_CREEP_ALERT",
  "REASSIGN_PR_REVIEWER_MOBILE",
  "VIEW_OWN_CODE_QUALITY",
  "VIEW_TECH_DEBT_AND_SCIP_GRAPH",
  "TRIGGER_REFACTOR_ANALYSIS",
  "SEARCH_ADR",
  "APPROVE_ADR_TENANT_PRIVATE",
  "PUBLISH_GLOBAL_ADR",
  "MANAGE_WEBHOOK_INTEGRATIONS",
  "MANAGE_TENANT_USERS_AND_ROLES",
] as const;

/**
 * Backward-compatible role aliases mapped to canonical roles.
 * e.g. DELIVERY_EXEC -> DELIVERY_DIRECTOR
 */
export const ROLE_ALIASES: Record<string, Role> = {
  DELIVERY_EXEC: "DELIVERY_DIRECTOR",
};

/**
 * Normalizes a role string to its canonical Role identifier if known.
 */
export function normalizeRole(role: string): Role | string {
  const upper = role.trim().toUpperCase();
  if (ROLE_ALIASES[upper]) {
    return ROLE_ALIASES[upper];
  }
  return upper;
}

/**
 * Checks if user roles include a specific canonical role (accounting for aliases).
 */
export function hasRole(roles: string[] | null | undefined, targetRole: Role): boolean {
  if (!roles || !Array.isArray(roles) || roles.length === 0) {
    return false;
  }
  const normalizedTarget = normalizeRole(targetRole);
  return roles.some((r) => normalizeRole(r) === normalizedTarget);
}

interface ActionMatrixEntry {
  decision: "ALLOW" | "DENY";
  source?: string;
  condition?: string | null;
  notes?: string;
}

type MatrixType = Record<string, Record<string, ActionMatrixEntry>>;

const permissionsMatrix: MatrixType = matrixData.matrix as unknown as MatrixType;

/**
 * Pure evaluation function: checks if a set of user roles has permission to perform `action`.
 * Evaluates against the authoritative M.A.N.T.A. Role-Permission Matrix.
 * If user holds multiple roles, returns true if ANY role has decision === "ALLOW".
 * Defaults to false (deny-by-default).
 */
export function canUser(
  roles: string[] | null | undefined,
  action: ActionId
): boolean {
  if (!roles || !Array.isArray(roles) || roles.length === 0) {
    return false;
  }

  for (const rawRole of roles) {
    const canonicalRole = normalizeRole(rawRole);
    const roleEntries = permissionsMatrix[canonicalRole];
    if (!roleEntries) {
      continue;
    }

    const actionEntry = roleEntries[action];
    if (actionEntry && actionEntry.decision === "ALLOW") {
      return true;
    }
  }

  return false;
}

/**
 * Inverse helper: returns true if user cannot perform `action`.
 */
export function cannotUser(
  roles: string[] | null | undefined,
  action: ActionId
): boolean {
  return !canUser(roles, action);
}
