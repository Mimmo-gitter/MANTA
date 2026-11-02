"use client";

import { useEffect } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { SignedOutCard } from "@/components/auth/signed-out-card";

export function SignedOutContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const workspaceParam = searchParams.get("workspace") || "Fintech Alpha";

  // When reloading the signed-out page (F5 / reload), redirect to /login
  useEffect(() => {
    if (typeof window !== "undefined") {
      const navEntries = performance.getEntriesByType("navigation");
      const isReload =
        navEntries.length > 0 &&
        (navEntries[0] as PerformanceNavigationTiming).type === "reload";

      if (isReload) {
        router.replace("/login");
      }
    }
  }, [router]);

  return <SignedOutCard workspaceName={workspaceParam} />;
}
