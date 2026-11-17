"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Loader2,
  RefreshCw,
  ShieldAlert,
} from "lucide-react";
import { isApiHttpError } from "@/lib/api-client";
import { acceptInvitation } from "@/lib/members-api";

type AcceptStatus = "missing_token" | "loading" | "success" | "error";

// Track in-flight or auto-triggered tokens across StrictMode remounts so
// one-time activation tokens are not submitted twice automatically.
const autoStartedTokens = new Set<string>();

interface ErrorMeta {
  title: string;
  description: string;
  retryable: boolean;
}

function describeAcceptError(err: unknown): ErrorMeta {
  if (isApiHttpError(err)) {
    switch (err.status) {
      case 404:
        return {
          title: "Invitation not found",
          description:
            err.message ||
            "This invitation link is invalid or does not exist.",
          retryable: false,
        };
      case 409:
        return {
          title: "Invitation already used",
          description:
            err.message ||
            "This invitation was already accepted or the email is already registered.",
          retryable: false,
        };
      case 410:
        return {
          title: "Invitation expired",
          description:
            err.message ||
            "This invitation link has expired. Ask your Tenant Administrator to resend an invite.",
          retryable: false,
        };
      case 400:
        return {
          title: "Invalid invitation token",
          description: err.message || "The provided invitation token is invalid.",
          retryable: false,
        };
      default:
        return {
          title: `Activation failed (HTTP ${err.status})`,
          description:
            err.message ||
            "We could not complete account activation due to a server error.",
          retryable: err.status >= 500,
        };
    }
  }

  return {
    title: "Unable to activate account",
    description:
      err instanceof Error
        ? err.message
        : "A network error occurred while contacting the activation service.",
    retryable: true,
  };
}

export function AcceptInviteContent() {
  const searchParams = useSearchParams();
  const rawToken = searchParams.get("token")?.trim() ?? "";

  const [status, setStatus] = useState<AcceptStatus>(
    rawToken ? "loading" : "missing_token",
  );
  const [errorMeta, setErrorMeta] = useState<ErrorMeta | null>(null);

  const runAccept = useCallback(async (token: string) => {
    setStatus("loading");
    setErrorMeta(null);
    try {
      await acceptInvitation(token);
      setStatus("success");
    } catch (err) {
      setErrorMeta(describeAcceptError(err));
      setStatus("error");
    }
  }, []);

  useEffect(() => {
    if (!rawToken) {
      setStatus("missing_token");
      return;
    }

    if (autoStartedTokens.has(rawToken)) {
      return;
    }
    autoStartedTokens.add(rawToken);
    void runAccept(rawToken);
  }, [rawToken, runAccept]);

  return (
    <main className="flex min-h-screen w-full items-center justify-center bg-[#070a11] px-4 py-10 text-[#f8fafc]">
      <div
        data-testid="accept-invite-card"
        className="w-full max-w-[460px] rounded-[16px] border border-[#262d3d] bg-[#131722] p-7 shadow-[0_24px_60px_rgba(0,0,0,0.6)]"
      >
        <div className="mb-5 flex items-center justify-between border-b border-[#1e2636] pb-4">
          <div className="flex items-center gap-2.5">
            <span className="inline-flex size-2.5 rounded-full bg-[#00e599] shadow-[0_0_10px_#00e599]" />
            <span className="font-mono text-[11px] font-semibold uppercase tracking-[0.08em] text-[#94a3b8]">
              MANTA · Workspace Onboarding
            </span>
          </div>
        </div>

        {status === "loading" && (
          <section
            data-testid="accept-invite-loading"
            aria-busy="true"
            className="flex flex-col items-center py-6 text-center"
          >
            <div className="mb-4 flex size-12 items-center justify-center rounded-full border border-[#00e599]/30 bg-[#00e599]/10 text-[#00e599]">
              <Loader2 className="size-6 animate-spin" aria-hidden="true" />
            </div>
            <h1 className="text-[18px] font-bold tracking-[-0.02em] text-[#f8fafc]">
              Activating your workspace account
            </h1>
            <p className="mt-2 max-w-[340px] text-[13px] leading-[20px] text-[#94a3b8]">
              Verifying your one-time invitation token and provisioning workspace membership...
            </p>
          </section>
        )}

        {status === "success" && (
          <section
            data-testid="accept-invite-success"
            role="status"
            className="flex flex-col items-center py-4 text-center"
          >
            <div className="mb-4 flex size-12 items-center justify-center rounded-full border border-[#00e599]/35 bg-[#00e599]/15 text-[#00e599]">
              <CheckCircle2 className="size-6" aria-hidden="true" />
            </div>
            <h1 className="text-[19px] font-bold tracking-[-0.02em] text-[#f8fafc]">
              Account activated
            </h1>
            <p className="mt-2 text-[13px] leading-[20px] text-[#94a3b8]">
              Your workspace membership is now active. Sign in to the MANTA portal to access your organization.
            </p>
            <Link
              href="/login"
              data-testid="accept-invite-signin-link"
              className="mt-6 inline-flex h-[42px] w-full items-center justify-center gap-2 rounded-[10px] bg-[#00e599] px-5 text-[13px] font-bold text-[#05080f] transition-colors hover:bg-[#2df0b0]"
            >
              <span>Sign In to Portal</span>
              <ArrowRight className="size-4" aria-hidden="true" />
            </Link>
          </section>
        )}

        {status === "missing_token" && (
          <section
            data-testid="accept-invite-missing-token"
            role="alert"
            className="flex flex-col items-center py-4 text-center"
          >
            <div className="mb-4 flex size-12 items-center justify-center rounded-full border border-[#f59e0b]/35 bg-[#f59e0b]/15 text-[#fbbf24]">
              <ShieldAlert className="size-6" aria-hidden="true" />
            </div>
            <h1 className="text-[18px] font-bold tracking-[-0.02em] text-[#f8fafc]">
              Missing invitation token
            </h1>
            <p className="mt-2 text-[13px] leading-[20px] text-[#94a3b8]">
              Open the full activation link from your invitation email (including the{" "}
              <code className="rounded bg-[#0a0e18] px-1.5 py-0.5 font-mono text-[11px] text-[#e2e8f0]">
                ?token=...
              </code>{" "}
              parameter).
            </p>
            <Link
              href="/login"
              className="mt-6 inline-flex h-[40px] w-full items-center justify-center rounded-[10px] border border-[#262d3d] bg-[#0a0e18] px-4 text-[13px] font-medium text-[#e2e8f0] transition-colors hover:border-[#00e599]/50 hover:text-[#00e599]"
            >
              Back to Sign In
            </Link>
          </section>
        )}

        {status === "error" && errorMeta && (
          <section
            data-testid="accept-invite-error"
            role="alert"
            className="flex flex-col items-center py-4 text-center"
          >
            <div className="mb-4 flex size-12 items-center justify-center rounded-full border border-[#ef4444]/35 bg-[#ef4444]/15 text-[#f87171]">
              <AlertTriangle className="size-6" aria-hidden="true" />
            </div>
            <h1 className="text-[18px] font-bold tracking-[-0.02em] text-[#f8fafc]">
              {errorMeta.title}
            </h1>
            <p className="mt-2 text-[13px] leading-[20px] text-[#cbd5e1]">
              {errorMeta.description}
            </p>

            <div className="mt-6 flex w-full flex-col gap-2.5 sm:flex-row">
              {errorMeta.retryable && rawToken && (
                <button
                  type="button"
                  data-testid="accept-invite-retry-btn"
                  onClick={() => void runAccept(rawToken)}
                  className="inline-flex h-[40px] flex-1 items-center justify-center gap-2 rounded-[10px] bg-[#00e599] px-4 text-[13px] font-bold text-[#05080f] transition-colors hover:bg-[#2df0b0] cursor-pointer"
                >
                  <RefreshCw className="size-3.5" aria-hidden="true" />
                  <span>Retry</span>
                </button>
              )}
              <Link
                href="/login"
                className="inline-flex h-[40px] flex-1 items-center justify-center rounded-[10px] border border-[#262d3d] bg-[#0a0e18] px-4 text-[13px] font-medium text-[#e2e8f0] transition-colors hover:border-[#64748b]"
              >
                Go to Sign In
              </Link>
            </div>
          </section>
        )}
      </div>
    </main>
  );
}
