"use client";

import React, { useEffect, type ReactNode } from "react";
import { useRouter, usePathname } from "next/navigation";
import { useAuth } from "@/providers/auth-provider";
import { getIsLoggingOut } from "@/lib/auth";

interface ProtectedRouteProps {
  children: ReactNode;
}

export function ProtectedRoute({ children }: ProtectedRouteProps) {
  const { isAuthenticated, isLoading } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (!isLoading && !isAuthenticated) {
      if (getIsLoggingOut()) {
        router.replace("/signed-out");
        return;
      }

      const target = pathname ? `/login?redirect=${encodeURIComponent(pathname)}` : "/login";
      router.replace(target);
    }
  }, [isLoading, isAuthenticated, router, pathname]);

  if (isLoading) {
    return (
      <main
        data-testid="protected-route-loading"
        className="flex min-h-screen flex-col items-center justify-center bg-slate-50 text-slate-600"
      >
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-blue-600 border-t-transparent" />
        <p className="mt-2 text-xs font-medium">Verifying authentication session...</p>
      </main>
    );
  }

  if (!isAuthenticated) {
    return null;
  }

  return <>{children}</>;
}
