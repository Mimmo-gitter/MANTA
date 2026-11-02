"use client";

import { useState } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { SignInCard } from "@/components/auth/sign-in-card";
import { SignedOutCard } from "@/components/auth/signed-out-card";

export function LoginContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const statusParam = searchParams.get("status");
  const signedOutParam = searchParams.get("signed_out");
  const isSignedOutInitial = statusParam === "signed-out" || signedOutParam === "true";

  const [showSignedOut, setShowSignedOut] = useState(isSignedOutInitial);

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
          setShowSignedOut(false);
          router.replace("/login");
        }}
        onDifferentOrganization={() => {
          setShowSignedOut(false);
          router.replace("/login");
        }}
      />
    );
  }

  return <SignInCard initialError={initialError} />;
}
