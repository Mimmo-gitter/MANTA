"use client";

import { useCallback, useMemo } from "react";
import { useAuth } from "@/providers/auth-provider";
import {
  canUser,
  cannotUser,
  hasRole as checkHasRole,
  type ActionId,
  type Role,
} from "@/lib/permissions";

export interface UsePermissionsResult {
  /**
   * Returns true if current user's role(s) are permitted to perform `action`
   * according to the authoritative Role-Permission Matrix.
   */
  can: (action: ActionId) => boolean;

  /**
   * Inverse of `can`: returns true if current user lacks permission for `action`.
   */
  cannot: (action: ActionId) => boolean;

  /**
   * The list of raw roles associated with the authenticated user.
   */
  roles: string[];

  /**
   * Helper to test if user has a specific role (handling aliases).
   */
  hasRole: (targetRole: Role) => boolean;

  /**
   * Convenience flag: true if user has TENANT_ADMIN role.
   */
  isTenantAdmin: boolean;
}

/**
 * Primary React hook for role-based authorization in the UI.
 * Connects to active AuthProvider identity and evaluates permissions against
 * the shared M.A.N.T.A. Role-Permission Matrix.
 */
export function usePermissions(): UsePermissionsResult {
  const { backendUser } = useAuth();
  const roles = useMemo(() => backendUser?.roles ?? [], [backendUser?.roles]);

  const can = useCallback(
    (action: ActionId) => canUser(roles, action),
    [roles]
  );

  const cannot = useCallback(
    (action: ActionId) => cannotUser(roles, action),
    [roles]
  );

  const hasRole = useCallback(
    (targetRole: Role) => checkHasRole(roles, targetRole),
    [roles]
  );

  const isTenantAdmin = useMemo(
    () => checkHasRole(roles, "TENANT_ADMIN"),
    [roles]
  );

  return {
    can,
    cannot,
    roles,
    hasRole,
    isTenantAdmin,
  };
}
