"use client";

import { useEffect } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { SignedOutCard } from "@/components/auth/signed-out-card";

export function SignedOutContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const workspaceParam = searchParams.get("workspace") || "Fintech Alpha";
  const reasonParam = searchParams.get("reason");
  const redirectParam = searchParams.get("redirect");
  const isSessionExpired = reasonParam === "session_expired" || reasonParam === "expired";

  // When reloading the signed-out page (F5 / reload), redirect to /login
  useEffect(() => {
    if (typeof window !== "undefined") {
      const navEntries = performance.getEntriesByType("navigation");
      const isReload =
        navEntries.length > 0 &&
        (navEntries[0] as PerformanceNavigationTiming).type === "reload";

      if (isReload) {
        window.sessionStorage.removeItem("user_signed_out");
        router.replace("/login");
      }
    }
  }, [router]);

  return (
    <SignedOutCard
      variant={isSessionExpired ? "session-expired" : "signed-out"}
      redirectUrl={redirectParam || undefined}
      workspaceName={workspaceParam}
      onSignInAgain={() => {
        if (typeof window !== "undefined") {
          window.sessionStorage.removeItem("user_signed_out");
          if (isSessionExpired && redirectParam) {
            window.sessionStorage.setItem("manta_auth_return_to", redirectParam);
          }
        }
        const target = isSessionExpired
          ? (redirectParam
              ? `/login?reason=session_expired&redirect=${encodeURIComponent(redirectParam)}`
              : "/login?reason=session_expired")
          : (redirectParam
              ? `/login?redirect=${encodeURIComponent(redirectParam)}`
              : "/login");
        router.push(target);
      }}
      onDifferentOrganization={() => {
        if (typeof window !== "undefined") {
          window.sessionStorage.removeItem("user_signed_out");
        }
        router.push("/login");
      }}
    />
  );
}
