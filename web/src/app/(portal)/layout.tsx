"use client";

import { useEffect, Suspense } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/providers/auth-provider";
import { getIsLoggingOut } from "@/lib/auth";
import { PortalLayout } from "@/components/layout/portal-layout";

function PortalAuthGuard({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading } = useAuth();
  const router = useRouter();

  const isDemo =
    typeof window !== "undefined" &&
    (window.location.search.includes("state=") ||
      window.location.search.includes("demo=true") ||
      window.sessionStorage.getItem("manta_demo") === "true");

  useEffect(() => {
    if (!isLoading && !isAuthenticated && !isDemo) {
      if (getIsLoggingOut()) {
        router.replace("/signed-out");
        return;
      }

      router.replace("/login");
    }
  }, [isLoading, isAuthenticated, isDemo, router]);

  if ((!isLoading && !isAuthenticated && !isDemo) || getIsLoggingOut()) {
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
