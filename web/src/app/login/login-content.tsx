"use client";

import { useState, useEffect } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { SignInCard } from "@/components/auth/sign-in-card";
import { SignedOutCard } from "@/components/auth/signed-out-card";
import { useAuth } from "@/providers/auth-provider";
import { getIsLoggingOut } from "@/lib/auth";

export function LoginContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { isAuthenticated, isLoading } = useAuth();
  const statusParam = searchParams.get("status");
  const signedOutParam = searchParams.get("signed_out");
  const reasonParam = searchParams.get("reason");
  const redirectParam = searchParams.get("redirect");
  const isSessionExpired = reasonParam === "session_expired";
  const isLoggingOut = getIsLoggingOut();
  const isSignedOutInitial =
    statusParam === "signed-out" ||
    signedOutParam === "true" ||
    isLoggingOut ||
    isSessionExpired;

  const [showSignedOut, setShowSignedOut] = useState(isSignedOutInitial);

  // Persist redirect target into sessionStorage for callback flow
  useEffect(() => {
    if (redirectParam && typeof window !== "undefined") {
      try {
        window.sessionStorage.setItem("manta_auth_return_to", redirectParam);
      } catch {
        // ignore
      }
    }
    if (isSessionExpired && typeof window !== "undefined") {
      try {
        window.sessionStorage.removeItem("manta_session_expired");
      } catch {
        // ignore
      }
    }
  }, [redirectParam, isSessionExpired]);

  // If user is in logout transition and lands on /login, smoothly route to /signed-out
  useEffect(() => {
    if (isLoggingOut && !statusParam && !signedOutParam) {
      router.replace("/signed-out");
    }
  }, [isLoggingOut, statusParam, signedOutParam, router]);

  // If already authenticated and not explicitly viewing the signed-out screen, redirect to main screen
  useEffect(() => {
    if (!isLoading && isAuthenticated && !isSignedOutInitial) {
      router.replace("/");
    }
  }, [isLoading, isAuthenticated, isSignedOutInitial, router]);

  useEffect(() => {
    if (typeof window !== "undefined" && isSignedOutInitial) {
      window.sessionStorage.setItem("user_signed_out", "true");
    }
  }, [isSignedOutInitial]);

  const errorParam = searchParams.get("error");
  const descriptionParam = searchParams.get("error_description");

  const initialError = errorParam
    ? `Authentication failed\n${
        descriptionParam ||
        "Invalid credentials or SSO sign-in was cancelled. Verify your email and password."
      }`
    : null;

  if (showSignedOut) {
    return (
      <SignedOutCard
        variant={isSessionExpired ? "session-expired" : "signed-out"}
        redirectUrl={redirectParam || undefined}
        workspaceName={searchParams.get("workspace") || "Fintech Alpha"}
        onSignInAgain={() => {
          if (typeof window !== "undefined") {
            window.sessionStorage.removeItem("user_signed_out");
            if (redirectParam) {
              window.sessionStorage.setItem("manta_auth_return_to", redirectParam);
            }
          }
          setShowSignedOut(false);
          const target = redirectParam
            ? `/login?redirect=${encodeURIComponent(redirectParam)}`
            : "/login";
          router.replace(target);
        }}
        onDifferentOrganization={() => {
          if (typeof window !== "undefined") {
            window.sessionStorage.removeItem("user_signed_out");
          }
          setShowSignedOut(false);
          router.replace("/login");
        }}
      />
    );
  }

  const emailParam = searchParams.get("email");
  const workspaceParam = searchParams.get("workspace");
  const registeredParam = searchParams.get("registered");

  return (
    <SignInCard
      initialError={initialError}
      defaultEmail={emailParam || ""}
      workspace={workspaceParam}
      isRegistered={registeredParam === "true"}
      sessionExpired={isSessionExpired}
    />
  );
}
