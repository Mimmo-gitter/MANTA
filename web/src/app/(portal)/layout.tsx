
"use client";

import { useEffect, Suspense } from "react";
import { useRouter, usePathname } from "next/navigation";
import { useAuth } from "@/providers/auth-provider";
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

  const isTenantAdmin =
    backendUser?.roles.includes("TENANT_ADMIN") ?? false;

  useEffect(() => {
    if (isLoading) return;

    if (!isAuthenticated && !isDemo) {
      if (getIsLoggingOut()) {
        router.replace("/signed-out");
        return;
      }

      router.replace("/login");
      return;
    }

    if (isAdminRoute && !isTenantAdmin) {
      router.replace("/");
    }
  }, [
    isLoading,
    isAuthenticated,
    isDemo,
    isAdminRoute,
    isTenantAdmin,
    router,
  ]);

  if (isLoading) {
    return null;
  }

  if ((!isAuthenticated && !isDemo) || getIsLoggingOut()) {
    return null;
  }

  if (isAdminRoute && !isTenantAdmin) {
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
