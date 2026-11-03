"use client";

import React from "react";
import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { SecurityLockMintIcon, ArrowRightMintIcon } from "@/components/icons/auth-icons";

export interface SignedOutCardProps {
  workspaceName?: string;
  onSignInAgain?: () => void;
  onDifferentOrganization?: () => void;
}

export function SignedOutCard({
  workspaceName = "Fintech Alpha",
  onSignInAgain,
  onDifferentOrganization,
}: SignedOutCardProps) {
  const router = useRouter();

  const handleSignInAgain = () => {
    if (typeof window !== "undefined") {
      window.sessionStorage.removeItem("user_signed_out");
    }
    if (onSignInAgain) {
      onSignInAgain();
    } else {
      router.push("/login");
    }
  };

  const handleDifferentOrg = (e: React.MouseEvent<HTMLAnchorElement>) => {
    if (typeof window !== "undefined") {
      window.sessionStorage.removeItem("user_signed_out");
    }
    if (onDifferentOrganization) {
      e.preventDefault();
      onDifferentOrganization();
    }
  };

  return (
    <section
      aria-labelledby="signed-out-heading"
      className="relative flex flex-col w-full max-w-[420px] rounded-[12px] border border-[#262A35] bg-[#171B26] p-5 sm:p-7 shadow-[0px_2px_8px_-2px_rgba(0,0,0,0.3),0px_8px_32px_-4px_rgba(0,0,0,0.5)] isolate select-none transition-all"
      style={{
        fontFamily: "'Plus Jakarta Sans', system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
      }}
    >
      {/* Subtle Ambient Glow at top of card matching Figma node 491:768 */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -top-12 left-1/2 -translate-x-1/2 w-[192px] h-[96px] rounded-full bg-[rgba(0,245,160,0.05)] blur-[64px]"
      />

      {/* ModalHeader matching Figma node 491:769 */}
      <header className="relative z-10 flex flex-col items-center w-full pb-5 border-b border-[#262A35]">
        {/* Branded Logo Mark with purple ambient drop shadow matching Figma 491:770 */}
        <div className="relative mb-3 flex h-12 w-12 items-center justify-center">
          <Image
            src="/logo.png"
            alt="M.A.N.T.A. Platform Logo"
            width={48}
            height={48}
            priority
            className="w-12 h-12 object-contain drop-shadow-[0px_4px_12px_rgba(110,65,226,0.35)]"
          />
        </div>

        {/* Security Specification Tag matching Figma 491:773 */}
        <div className="mb-2 flex items-center justify-center">
          <div
            role="status"
            aria-label="Session status: Session Terminated"
            data-testid="session-terminated-badge"
            className="inline-flex items-center gap-1.5 px-2 py-[2px] rounded bg-[#0A0E18] border border-[rgba(0,245,160,0.35)]"
          >
            <span
              aria-hidden="true"
              className="w-1.5 h-1.5 rounded-full bg-[#00F5A0] shrink-0"
            />
            <span className="font-mono text-[11px] font-medium leading-[16.5px] tracking-[0.55px] text-[#6EE7B7]">
              SESSION TERMINATED
            </span>
          </div>
        </div>

        {/* Title matching Figma 491:778 */}
        <h1
          id="signed-out-heading"
          className="text-[24px] font-bold tracking-[-0.475px] text-[#DFE2F1] text-center leading-8"
        >
          You Have Been Signed Out
        </h1>

        {/* Subtitle matching Figma 491:781 */}
        <p className="text-[12px] font-normal text-[#B9CBBD] text-center max-w-[320px] leading-[14px] mt-1.5">
          Your session was safely closed and local authentication tokens were cleared from this browser.
        </p>
      </header>

      {/* CardBody matching Figma node 491:782 */}
      <div className="relative z-10 flex flex-col gap-4 mt-5">
        {/* Security Architecture Callout Box matching Figma 491:783 */}
        <aside
          role="note"
          aria-label="Workspace disconnection notice"
          className="flex items-start gap-3 p-3.5 rounded bg-[#1C1F2A] border border-[#262A35]"
        >
          <div className="pt-0.5 shrink-0">
            <SecurityLockMintIcon className="w-4 h-4 text-[#00F5A0]" />
          </div>
          <div className="flex-1">
            <p className="text-[12px] font-normal leading-[16.5px] text-[#B9CBBD] text-left">
              Disconnected from workspace{" "}
              <span className="font-medium text-[#DFE2F1]">{workspaceName}</span>. Sign in again
              to access organization projects and telemetry.
            </p>
          </div>
        </aside>

        {/* Submit Button CTA matching Figma 491:791 */}
        <Link
          href="/login"
          data-testid="sign-in-again-button"
          onClick={(e) => {
            if (typeof window !== "undefined") {
              window.sessionStorage.removeItem("user_signed_out");
            }
            if (onSignInAgain) {
              e.preventDefault();
              onSignInAgain();
            }
          }}
          className="w-full h-[44px] min-h-[44px] rounded-[12px] bg-[#00E599] hover:bg-[#00F5A0] active:scale-[0.99] text-black font-semibold text-[14px] leading-5 flex items-center justify-center gap-2 px-4 transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00E599] focus-visible:ring-offset-2 focus-visible:ring-offset-[#171B26]"
        >
          <span>Sign In Again</span>
          <ArrowRightMintIcon className="w-4 h-4 text-[#006B43]" />
        </Link>

        {/* Action Link matching Figma 491:796 */}
        <div className="flex items-center justify-center pt-0.5">
          <Link
            href="/login"
            data-testid="different-org-link"
            onClick={handleDifferentOrg}
            className="text-[12px] font-medium leading-[16.5px] text-[#00C2CB] hover:underline hover:text-[#00F5A0] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[#00C2CB] rounded-sm py-2 px-2 inline-flex items-center justify-center min-h-[36px] transition-colors"
          >
            Sign in to a different organization →
          </Link>
        </div>
      </div>
    </section>
  );
}
