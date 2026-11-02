"use client";

import { useSearchParams } from "next/navigation";
import { SignedOutCard } from "@/components/auth/signed-out-card";

export function SignedOutContent() {
  const searchParams = useSearchParams();
  const workspaceParam = searchParams.get("workspace") || "Fintech Alpha";

  return <SignedOutCard workspaceName={workspaceParam} />;
}
