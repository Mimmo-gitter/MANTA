
"use client";

import { useEffect, Suspense } from "react";
import { useRouter, usePathname } from "next/navigation";
import { useAuth } from "@/providers/auth-provider";
import { usePermissions } from "@/hooks/use-permissions";
import { getIsLoggingOut } from "@/lib/auth";
import { PortalLayout } from "@/components/layout/portal-layout";

function PortalAuthGuard({ children }: { children: React.ReactNode }) {
  const {
    isAuthenticated,
    isLoading,
    backendUser,
  } = useAuth();

  const router = useRouter();
  const pathname = usePathname();

  const isDemo =
    typeof window !== "undefined" &&
    (window.location.search.includes("state=") ||
      window.location.search.includes("demo=true") ||
      window.sessionStorage.getItem("manta_demo") === "true");

  const adminRoutes = [
    "/workspace-settings",
    "/integrations",
    "/access-roles",
    "/privacy-erasure",
    "/adr-governance",
  ];

  const isAdminRoute = adminRoutes.some(
    (route) => pathname === route || pathname.startsWith(`${route}/`)
  );

  const { can } = usePermissions();

  const canAccessRoute = (route: string) => {
    if (
      route === "/workspace-settings" ||
      route.startsWith("/workspace-settings/") ||
      route === "/access-roles" ||
      route.startsWith("/access-roles/") ||
      route === "/privacy-erasure" ||
      route.startsWith("/privacy-erasure/")
    ) {
      return can("MANAGE_TENANT_USERS_AND_ROLES");
    }
    if (route === "/integrations" || route.startsWith("/integrations/")) {
      return can("MANAGE_WEBHOOK_INTEGRATIONS");
    }
    if (route === "/adr-governance" || route.startsWith("/adr-governance/")) {
      return can("PUBLISH_GLOBAL_ADR");
    }
    return true;
  };

  const isUnauthorizedAdminAccess = isAdminRoute && !canAccessRoute(pathname);

  useEffect(() => {
    if (isLoading) return;

    if (!isAuthenticated && !isDemo) {
      if (getIsLoggingOut()) {
        router.replace("/signed-out");
        return;
      }

      const isExpired =
        typeof window !== "undefined" &&
        window.sessionStorage.getItem("manta_session_expired") === "true";
      const reasonParam = isExpired ? "reason=session_expired" : "";
      const redirectParam =
        pathname && pathname !== "/"
          ? `redirect=${encodeURIComponent(pathname)}`
          : "";
      const query = [reasonParam, redirectParam].filter(Boolean).join("&");
      const target = query ? `/login?${query}` : "/login";

      router.replace(target);
      return;
    }

    if (isUnauthorizedAdminAccess) {
      router.replace("/");
    }
  }, [
    isLoading,
    isAuthenticated,
    isDemo,
    isUnauthorizedAdminAccess,
    pathname,
    router,
  ]);

  if (isLoading) {
    return null;
  }

  if ((!isAuthenticated && !isDemo) || getIsLoggingOut()) {
    return null;
  }

  if (isUnauthorizedAdminAccess) {
    return null;
  }

  return <PortalLayout>{children}</PortalLayout>;
}

export default function PortalRouteLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <Suspense fallback={null}>
      <PortalAuthGuard>{children}</PortalAuthGuard>
    </Suspense>
  );
}
