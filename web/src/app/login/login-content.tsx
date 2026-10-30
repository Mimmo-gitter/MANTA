"use client";

import { useSearchParams } from "next/navigation";
import { SignInCard } from "@/components/auth/sign-in-card";

export function LoginContent() {
  const searchParams = useSearchParams();
  const errorParam = searchParams.get("error");

  const initialError = errorParam
    ? "Authentication failed\nInvalid credentials or SSO sign-in was cancelled. Verify your email and password."
    : null;

  return <SignInCard initialError={initialError} />;
}
