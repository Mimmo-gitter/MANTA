"use client";

import { useSearchParams } from "next/navigation";
import { SignInCard } from "@/components/auth/sign-in-card";

export function LoginContent() {
  const searchParams = useSearchParams();
  const errorParam = searchParams.get("error");
  const descriptionParam = searchParams.get("error_description");

  const initialError = errorParam
    ? `Authentication failed\n${
        descriptionParam ||
        "Invalid credentials or SSO sign-in was cancelled. Verify your email and password."
      }`
    : null;

  return <SignInCard initialError={initialError} />;
}
