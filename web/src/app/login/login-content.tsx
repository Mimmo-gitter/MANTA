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
  const isLoggingOut = getIsLoggingOut();
  const isSignedOutInitial =
    statusParam === "signed-out" ||
    signedOutParam === "true" ||
    isLoggingOut;

  const [showSignedOut, setShowSignedOut] = useState(isSignedOutInitial);

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
        workspaceName={searchParams.get("workspace") || "Fintech Alpha"}
        onSignInAgain={() => {
          if (typeof window !== "undefined") {
            window.sessionStorage.removeItem("user_signed_out");
          }
          setShowSignedOut(false);
          router.replace("/login");
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

  return <SignInCard initialError={initialError} />;
}
