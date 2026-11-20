
"use client";

import { useEffect, Suspense } from "react";
import { useRouter, usePathname } from "next/navigation";
import { useAuth } from "@/providers/auth-provider";
import { usePermissions } from "@/hooks/use-permissions";
import { getIsLoggingOut } from "@/lib/auth";
import { canAccessPortalRoute } from "@/config/navigation";
import { PortalLayout } from "@/components/layout/portal-layout";

function PortalAuthGuard({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading } = useAuth();
  const { can } = usePermissions();

  const router = useRouter();
  const pathname = usePathname();

  const isUnauthorizedAdminAccess = !canAccessPortalRoute(pathname, can);

  useEffect(() => {
    if (isLoading) return;

    if (!isAuthenticated) {
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
    isUnauthorizedAdminAccess,
    pathname,
    router,
  ]);

  if (isLoading) {
    return null;
  }

  if (!isAuthenticated || getIsLoggingOut()) {
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
