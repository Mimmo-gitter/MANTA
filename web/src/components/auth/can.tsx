"use client";

import React from "react";
import { usePermissions } from "@/hooks/use-permissions";
import type { ActionId } from "@/lib/permissions";

export interface CanProps {
  /**
   * The matrix action identifier to evaluate (e.g. "TRIGGER_REFACTOR_ANALYSIS").
   */
  do: ActionId;

  /**
   * Optional fallback content to render when user lacks permission.
   * Defaults to `null` (element is not rendered in the DOM).
   */
  fallback?: React.ReactNode;

  /**
   * Content to render when permission is granted.
   * Can be standard React nodes or a render function `(allowed: boolean) => React.ReactNode`.
   */
  children: React.ReactNode | ((allowed: boolean) => React.ReactNode);

  /**
   * If true, always invokes children function with `allowed: boolean`
   * instead of rendering `fallback`. Useful for rendering disabled buttons with tooltips.
   */
  passThrough?: boolean;
}

/**
 * Declarative component for permission-gated UI elements.
 * Renders `children` only when current user's role has `ALLOW` in the matrix for `do`.
 * By default, unpermitted controls are completely removed from the DOM.
 */
export function Can({
  do: action,
  fallback = null,
  children,
  passThrough = false,
}: CanProps) {
  const { can } = usePermissions();
  const allowed = can(action);

  if (typeof children === "function") {
    if (allowed || passThrough) {
      return <>{children(allowed)}</>;
    }
    return <>{fallback}</>;
  }

  if (allowed) {
    return <>{children}</>;
  }

  return <>{fallback}</>;
}
