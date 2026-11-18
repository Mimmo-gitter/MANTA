"use client";

import React from "react";
import Image from "next/image";
import Link from "next/link";
import { ArrowRightMintIcon, SecurityLockMintIcon } from "@/components/icons/auth-icons";

interface RegisterSuccessCardProps {
  organizationName: string;
  slug: string;
  adminName: string;
  adminEmail: string;
}

export function RegisterSuccessCard({
  organizationName,
  slug,
  adminName,
  adminEmail,
}: RegisterSuccessCardProps) {
  const loginUrl = `/login?workspace=${encodeURIComponent(slug)}&email=${encodeURIComponent(
    adminEmail
  )}&registered=true`;

  return (
    <section
      role="status"
      aria-labelledby="register-success-heading"
      data-testid="register-success-card"
      className="relative flex flex-col items-center w-full max-w-[440px] rounded-[12px] border border-[#262A35] bg-[#171B26] p-6 sm:p-8 shadow-[0px_8px_32px_-4px_rgba(0,0,0,0.5),0px_2px_8px_-2px_rgba(0,0,0,0.3)] isolate text-white select-none transition-all"
      style={{
        fontFamily: "'Plus Jakarta Sans', system-ui, -apple-system, sans-serif",
      }}
    >
      {/* Header */}
      <header className="flex flex-col items-center w-full mb-5">
        <div className="relative mb-3 flex h-[48px] w-[48px] items-center justify-center">
          <Image
            src="/logo.png"
            alt="M.A.N.T.A. Platform Logo"
            width={48}
            height={48}
            priority
            className="w-[48px] h-[48px] object-contain drop-shadow-[0px_4px_12px_rgba(0,229,153,0.3)]"
          />
        </div>

        {/* Status Pill (Figma 491:1156) */}
        <div
          data-testid="success-status-pill"
          className="flex items-center gap-1.5 px-3 py-1 rounded-full border border-[#00E599]/40 bg-[#00E599]/15 text-[#00E599] text-[11px] font-semibold tracking-wider uppercase mb-3 shadow-[0px_0px_12px_rgba(0,229,153,0.15)]"
        >
          <span className="w-1.5 h-1.5 rounded-full bg-[#00E599]" />
          <span>PROVISIONING COMPLETE</span>
        </div>

        <h1
          id="register-success-heading"
          className="text-[22px] font-bold tracking-tight text-center text-[#DFE2F1]"
        >
          {organizationName} Workspace Ready
        </h1>

        <p className="text-[13px] text-[#94A3B8] text-center mt-1">
          Your multi-tenant isolated environment has been provisioned successfully.
        </p>
      </header>

      {/* Technical Isolation Callout Box (Figma 491:1156) */}
      <div className="w-full flex flex-col gap-2 p-3.5 rounded-[8px] border border-[#00E599]/25 bg-[#0A0E18] mb-5">
        <div className="flex items-center justify-between text-[11px] font-mono text-[#94A3B8] pb-1.5 border-b border-[#262A35]">
          <span className="flex items-center gap-1 text-[#00E599]">
            <SecurityLockMintIcon className="w-3.5 h-3.5 shrink-0" />
            <span>RLS: ENFORCED</span>
          </span>
          <span className="text-[#94A3B8]/70">pg_isolation_v2</span>
        </div>

        <div className="flex flex-col gap-1 text-[12px] font-mono">
          <div className="flex justify-between items-center text-[#DFE2F1]">
            <span className="text-[#94A3B8]">TENANT SCHEMA</span>
            <span className="text-[#00E599] font-medium truncate max-w-[200px]" title={`${slug}_schema`}>
              {slug}_schema
            </span>
          </div>
          <div className="flex justify-between items-center text-[#DFE2F1]">
            <span className="text-[#94A3B8]">WORKSPACE URL</span>
            <span className="text-white font-medium truncate max-w-[200px]" title={`manta.ai/app/${slug}`}>
              manta.ai/app/{slug}
            </span>
          </div>
        </div>
      </div>

      {/* Checkpoints list */}
      <div className="w-full flex flex-col gap-2.5 mb-6 text-[13px] text-[#DFE2F1]">
        <div className="flex items-start gap-2.5">
          <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-[#00E599]/15 text-[#00E599] font-bold text-xs mt-0.5">
            ✓
          </div>
          <div className="leading-snug">
            <span className="text-white font-medium">Organization registered: </span>
            <span className="text-[#94A3B8]">{organizationName}</span>
          </div>
        </div>

        <div className="flex items-start gap-2.5">
          <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-[#00E599]/15 text-[#00E599] font-bold text-xs mt-0.5">
            ✓
          </div>
          <div className="leading-snug">
            <span className="text-white font-medium">Tenant Administrator: </span>
            <span className="text-[#94A3B8]">{adminName} ({adminEmail})</span>
          </div>
        </div>

        <div className="flex items-start gap-2.5">
          <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-[#00E599]/15 text-[#00E599] font-bold text-xs mt-0.5">
            ✓
          </div>
          <div className="leading-snug">
            <span className="text-white font-medium">Workspace isolation: </span>
            <span className="text-[#94A3B8]">PostgreSQL Row-Level Security active</span>
          </div>
        </div>
      </div>

      {/* Action Button */}
      <div className="w-full">
        <Link
          href={loginUrl}
          data-testid="launch-dashboard-button"
          className="flex h-[44px] min-h-[44px] w-full items-center justify-center gap-2 rounded-[12px] bg-[#00E599] font-semibold text-[14px] leading-5 text-black transition-all hover:bg-[#00f5a0] active:scale-[0.99] shadow-[0px_2px_10px_rgba(0,245,160,0.15)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00E599] focus-visible:ring-offset-2 focus-visible:ring-offset-[#171B26]"
        >
          <span>Launch Workspace Dashboard</span>
          <ArrowRightMintIcon className="w-4 h-4 shrink-0" />
        </Link>
      </div>
    </section>
  );
}
