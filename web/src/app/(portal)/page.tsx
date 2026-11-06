/* eslint-disable @next/next/no-img-element */
"use client";

import { useState, useEffect, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { useShellState, type ShellState } from "@/components/layout/shell-context";
import { cn } from "@/lib/utils";

function OverviewContent() {
  const searchParams = useSearchParams();
  const stateParam = searchParams.get("state") as ShellState | null;
  const { shellState: activeState, setShellState: setActiveState } = useShellState();

  const [errorTooltipOpen, setErrorTooltipOpen] = useState(false);
  const [isRetrying, setIsRetrying] = useState(false);
  const [createProjectSuccess, setCreateProjectSuccess] = useState(false);

  // Sync state if URL query param changes (?state=default|empty|error|loading)
  useEffect(() => {
    if (
      stateParam &&
      ["default", "empty", "error", "loading"].includes(stateParam)
    ) {
      setActiveState(stateParam);
    }
  }, [stateParam, setActiveState]);

  const handleRetry = () => {
    setIsRetrying(true);
    setTimeout(() => {
      setIsRetrying(false);
      setActiveState("default");
      setErrorTooltipOpen(false);
    }, 500);
  };

  return (
    <div className="relative min-h-[calc(100vh-48px)] w-full">
      {/* ------------------------------------------------------------- */}
      {/* 1. DEFAULT STATE (Figma 325:2340, 338:12871, 340:13023)       */}
      {/* Clean #0f131d / #0a0d14 Workspace Canvas 1-to-1 with Figma     */}
      {/* ------------------------------------------------------------- */}
      {activeState === "default" && (
        <section
          data-testid="overview-default-state"
          aria-label="Workspace Canvas"
          className="min-h-[calc(100vh-48px)] w-full"
        >
          <h1 className="sr-only">Engineering Overview</h1>
        </section>
      )}

      {/* ------------------------------------------------------------- */}
      {/* 2. EMPTY STATE (Figma 335:7565 - 1-to-1)                      */}
      {/* ------------------------------------------------------------- */}
      {activeState === "empty" && (
        <section
          data-testid="overview-empty-state"
          aria-label="Empty State: No projects created"
          className="flex min-h-[calc(100vh-48px)] w-full items-center justify-center p-[24px]"
        >
          <div className="flex flex-col items-center max-w-[448px] w-full">
            {/* Centered 48x48 Icon Box (Figma 335:7712) */}
            <div className="pb-[20px] flex flex-col items-start">
              <div className="bg-[#161b26] border border-[#1f2433] border-solid flex items-center justify-center p-px rounded-[12px] shadow-[0px_1px_2px_0px_rgba(0,0,0,0.05)] size-[48px]">
                <img
                  src="/icons/shell/16065.svg"
                  alt=""
                  aria-hidden="true"
                  className="size-[20px]"
                />
              </div>
            </div>

            {/* Heading 2 (Figma 335:7718) */}
            <div className="pb-[6px] w-full text-center">
              <h2 className="font-semibold text-[#e2e8f0] text-[16px] leading-[24px] tracking-[-0.4px]">
                No projects yet
              </h2>
            </div>

            {/* Description (Figma 335:7720) */}
            <div className="pb-[24px] max-w-[340px] w-full text-center">
              <p className="font-normal text-[#7c839b] text-[12px] leading-[19.5px]">
                Create a project or import an existing repository to get started.
              </p>
            </div>

            {/* Primary Button "+ Create Project" (Figma 335:7722) */}
            <button
              type="button"
              data-testid="create-project-button"
              onClick={() => {
                setCreateProjectSuccess(true);
                setTimeout(() => setCreateProjectSuccess(false), 3000);
              }}
              className="bg-[#00e599] flex items-center justify-center px-[16px] py-[8px] rounded-[6px] shadow-[0px_0px_16px_0px_rgba(0,229,153,0.2)] font-semibold text-[#0f131d] text-[12px] leading-[16px] text-center hover:brightness-105 transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00e599]"
            >
              + Create Project
            </button>

            {createProjectSuccess && (
              <p
                data-testid="create-project-feedback"
                role="status"
                className="pt-[8px] text-[11px] font-medium text-[#00e599]"
              >
                Project template initialized successfully!
              </p>
            )}

            {/* Secondary Link (Figma 335:7725) */}
            <div className="pt-[12px] flex flex-col items-start">
              <Link
                href="/integrations"
                className="font-medium text-[#7c839b] text-[11px] leading-[16.5px] text-center underline decoration-[rgba(124,131,155,0.4)] underline-offset-4 hover:text-[#e2e8f0] transition-colors"
              >
                or import existing repositories
              </Link>
            </div>
          </div>
        </section>
      )}

      {/* ------------------------------------------------------------- */}
      {/* 3. ERROR STATE & HOVER POPOVER (Figma 336:8004 & 336:8559)    */}
      {/* ------------------------------------------------------------- */}
      {activeState === "error" && (
        <section
          data-testid="overview-error-state"
          aria-label="Error State: Unable to load overview"
          className="flex min-h-[calc(100vh-48px)] w-full items-center justify-center p-[24px]"
        >
          {/* Card Container: 512px (Figma 336:8171 / 336:8732) */}
          <div className="bg-[#161b26] border border-[#1f2433] border-solid flex flex-col items-center max-w-[512px] w-full p-[33px] relative rounded-[12px] shadow-[0px_20px_25px_-5px_rgba(0,0,0,0.4),0px_8px_10px_-6px_rgba(0,0,0,0.4)]">
            {/* Red Alert Circle Icon (Figma 336:8174) */}
            <div className="pb-[20px] flex flex-col items-start">
              <div className="bg-[rgba(239,68,68,0.1)] border border-[rgba(239,68,68,0.2)] border-solid flex items-center justify-center p-px rounded-full shadow-[0px_0px_20px_0px_rgba(239,68,68,0.15)] size-[48px]">
                <img
                  src="/icons/shell/a1d53.svg"
                  alt=""
                  aria-hidden="true"
                  className="size-[24px]"
                />
              </div>
            </div>

            {/* Heading (Figma 336:8178) */}
            <div className="pb-[8px] w-full text-center">
              <h1 className="font-semibold text-[#e2e8f0] text-[18px] leading-[28px] tracking-[-0.45px]">
                Unable to load overview
              </h1>
            </div>

            {/* Description (Figma 336:8180) */}
            <div className="pb-[24px] max-w-[384px] w-full text-center">
              <p className="font-normal text-[#7c839b] text-[14px] leading-[22.75px]">
                We couldn’t load your project metrics. Please try again.
              </p>
            </div>

            {/* Action Buttons Row (Figma 336:8183) */}
            <div className="flex gap-[12px] items-center justify-center w-full">
              {/* Retry Button (Figma 336:8184) */}
              <button
                type="button"
                data-testid="error-retry-button"
                onClick={handleRetry}
                disabled={isRetrying}
                className="bg-[#1f2433] border border-[rgba(124,131,155,0.2)] border-solid flex gap-[8px] items-center justify-center px-[17px] py-[9px] rounded-[8px] shadow-[0px_1px_2px_0px_rgba(0,0,0,0.05)] hover:border-[rgba(124,131,155,0.4)] transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[#00e599]"
              >
                <img
                  src="/icons/shell/91fa3.svg"
                  alt=""
                  aria-hidden="true"
                  className={cn("size-[14px] shrink-0", isRetrying && "animate-spin")}
                />
                <span className="font-medium text-[#e2e8f0] text-[12px] leading-[16px] text-center">
                  {isRetrying ? "Retrying..." : "Retry"}
                </span>
              </button>

              {/* Error Details Button & Popover (Figma 336:8189 & 336:8751) */}
              <div className="group relative flex flex-col items-start">
                <button
                  type="button"
                  data-testid="error-details-button"
                  aria-describedby="error-details-tooltip"
                  aria-expanded={errorTooltipOpen}
                  onClick={() => setErrorTooltipOpen((prev) => !prev)}
                  onMouseEnter={() => setErrorTooltipOpen(true)}
                  onMouseLeave={() => setErrorTooltipOpen(false)}
                  onFocus={() => setErrorTooltipOpen(true)}
                  onBlur={() => setErrorTooltipOpen(false)}
                  className={cn(
                    "border border-solid flex gap-[6px] items-center px-[13px] py-[9px] rounded-[8px] transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[#00e599]",
                    errorTooltipOpen
                      ? "bg-[rgba(31,36,51,0.8)] border-[#1f2433] text-[#e2e8f0]"
                      : "border-transparent text-[#7c839b] hover:bg-[rgba(31,36,51,0.8)] hover:border-[#1f2433] hover:text-[#e2e8f0]"
                  )}
                >
                  <img
                    src={
                      errorTooltipOpen
                        ? "/icons/shell/52480.svg"
                        : "/icons/shell/7dae7.svg"
                    }
                    alt=""
                    aria-hidden="true"
                    className="size-[14px] shrink-0"
                  />
                  <span className="font-medium text-[12px] leading-[16px] text-center whitespace-nowrap">
                    Error details
                  </span>
                </button>

                {/* Error Details Hover Popover (Figma 336:8756 - 288px) */}
                <div
                  id="error-details-tooltip"
                  role="tooltip"
                  data-testid="error-details-tooltip"
                  className={cn(
                    "absolute left-1/2 top-[calc(100%+8px)] -translate-x-1/2 w-[288px] bg-[#0f131d] border border-[#1f2433] border-solid rounded-[8px] p-[13px] shadow-[0px_20px_25px_-5px_rgba(0,0,0,0.6),0px_8px_10px_-6px_rgba(0,0,0,0.6)] z-50 text-left",
                    errorTooltipOpen
                      ? "block"
                      : "hidden group-hover:block group-focus-within:block"
                  )}
                >
                  <div
                    className="absolute -top-[5px] left-1/2 -translate-x-1/2 bg-[#0f131d] border-[#1f2433] border-l border-solid border-t rotate-45 size-[8px]"
                    aria-hidden="true"
                  />
                  <p className="relative z-10 font-normal text-[#e2e8f0] text-[12px] leading-[19.5px]">
                    The overview metrics could not be loaded. Retry the request.
                    If the problem persists, contact your administrator.
                  </p>
                </div>
              </div>
            </div>
          </div>
        </section>
      )}

      {/* ------------------------------------------------------------- */}
      {/* 4. LOADING SKELETON STATE (Figma 337:11533 - 1-to-1)          */}
      {/* ------------------------------------------------------------- */}
      {activeState === "loading" && (
        <section
          data-testid="overview-loading-state"
          aria-label="Loading Overview Telemetry"
          aria-busy="true"
          className="max-w-[1600px] w-full mx-auto flex flex-col gap-[24px] p-[24px] animate-pulse"
        >
          {/* Header Row Skeleton (Figma 337:11697) */}
          <div className="flex flex-wrap items-center justify-between gap-4 w-full">
            <div className="flex flex-col gap-[8px] items-start">
              <div className="bg-[#161b26] border border-[#1f2433] border-solid h-[24px] rounded-[6px] w-[192px]" />
              <div className="bg-[rgba(22,27,38,0.6)] h-[14px] rounded-[4px] w-[320px] max-w-full" />
            </div>
            <div className="flex gap-[8px] items-center">
              <div className="bg-[#161b26] border border-[#1f2433] border-solid h-[32px] rounded-[6px] w-[112px]" />
              <div className="bg-[rgba(0,229,153,0.1)] border border-[rgba(0,229,153,0.2)] border-solid h-[32px] rounded-[6px] w-[128px]" />
            </div>
          </div>

          {/* 3 Metric Cards Skeleton (Figma 337:11705) */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-[16px] w-full">
            {[1, 2, 3].map((idx) => (
              <div
                key={idx}
                className="bg-[#161b26] border border-[#1f2433] border-solid flex flex-col gap-[16px] items-start p-[21px] rounded-[12px]"
              >
                <div className="flex items-center justify-between w-full">
                  <div className="bg-[#1f2433] h-[14px] rounded-[4px] w-[96px]" />
                  <div className="bg-[rgba(31,36,51,0.6)] rounded-[8px] size-[32px]" />
                </div>
                <div className="flex flex-col gap-[8px] items-start w-full">
                  <div className="bg-[#1f2433] h-[28px] rounded-[6px] w-[64px]" />
                  <div className="bg-[rgba(31,36,51,0.5)] h-[12px] rounded-[4px] w-[128px]" />
                </div>
                <div className="border-t border-[rgba(31,36,51,0.6)] border-solid pt-[9px] flex flex-col items-start w-full">
                  <div className="bg-[rgba(31,36,51,0.4)] h-[10px] rounded-[4px] w-full" />
                </div>
              </div>
            ))}
          </div>

          {/* Table Card Skeleton (Figma 337:11733) */}
          <div className="bg-[#161b26] border border-[#1f2433] border-solid flex flex-col items-start p-px rounded-[12px] w-full overflow-hidden">
            {/* Table Header */}
            <div className="border-b border-[#1f2433] border-solid px-[20px] py-[16px] flex items-center justify-between w-full">
              <div className="flex gap-[12px] items-center">
                <div className="bg-[#1f2433] h-[16px] rounded-[4px] w-[144px]" />
                <div className="bg-[rgba(31,36,51,0.6)] h-[20px] rounded-full w-[48px]" />
              </div>
              <div className="bg-[#0f131d] border border-[#1f2433] border-solid h-[28px] rounded-[6px] w-[160px]" />
            </div>

            {/* Row 1 (Figma 337:11740) */}
            <div className="border-b border-[rgba(31,36,51,0.6)] border-solid p-[20px] flex items-center justify-between w-full">
              <div className="flex gap-[16px] items-center">
                <div className="bg-[#1f2433] rounded-[8px] size-[40px] shrink-0" />
                <div className="flex flex-col gap-[8px] items-start">
                  <div className="bg-[#1f2433] h-[16px] rounded-[4px] w-[176px]" />
                  <div className="bg-[rgba(31,36,51,0.5)] h-[12px] rounded-[4px] w-[256px]" />
                </div>
              </div>
              <div className="hidden sm:flex gap-[24px] items-center">
                <div className="flex flex-col gap-[6px] items-end">
                  <div className="bg-[#1f2433] h-[14px] rounded-[4px] w-[80px]" />
                  <div className="bg-[rgba(31,36,51,0.5)] h-[10px] rounded-[4px] w-[56px]" />
                </div>
                <div className="bg-[rgba(31,36,51,0.6)] h-[24px] rounded-[4px] w-[64px]" />
                <div className="bg-[rgba(31,36,51,0.6)] rounded-[6px] size-[28px]" />
              </div>
            </div>

            {/* Row 2 (Figma 337:11752) */}
            <div className="border-b border-[rgba(31,36,51,0.6)] border-solid opacity-75 p-[20px] flex items-center justify-between w-full">
              <div className="flex gap-[16px] items-center">
                <div className="bg-[#1f2433] rounded-[8px] size-[40px] shrink-0" />
                <div className="flex flex-col gap-[8px] items-start">
                  <div className="bg-[#1f2433] h-[16px] rounded-[4px] w-[144px]" />
                  <div className="bg-[rgba(31,36,51,0.5)] h-[12px] rounded-[4px] w-[208px]" />
                </div>
              </div>
              <div className="hidden sm:flex gap-[24px] items-center">
                <div className="flex flex-col gap-[6px] items-end">
                  <div className="bg-[#1f2433] h-[14px] rounded-[4px] w-[80px]" />
                  <div className="bg-[rgba(31,36,51,0.5)] h-[10px] rounded-[4px] w-[56px]" />
                </div>
                <div className="bg-[rgba(31,36,51,0.6)] h-[24px] rounded-[4px] w-[64px]" />
                <div className="bg-[rgba(31,36,51,0.6)] rounded-[6px] size-[28px]" />
              </div>
            </div>

            {/* Row 3 (Figma 337:11764) */}
            <div className="opacity-50 p-[20px] flex items-center justify-between w-full">
              <div className="flex gap-[16px] items-center">
                <div className="bg-[#1f2433] rounded-[8px] size-[40px] shrink-0" />
                <div className="flex flex-col gap-[8px] items-start">
                  <div className="bg-[#1f2433] h-[16px] rounded-[4px] w-[160px]" />
                  <div className="bg-[rgba(31,36,51,0.5)] h-[12px] rounded-[4px] w-[224px]" />
                </div>
              </div>
              <div className="hidden sm:flex gap-[24px] items-center">
                <div className="flex flex-col gap-[6px] items-end">
                  <div className="bg-[#1f2433] h-[14px] rounded-[4px] w-[80px]" />
                  <div className="bg-[rgba(31,36,51,0.5)] h-[10px] rounded-[4px] w-[56px]" />
                </div>
                <div className="bg-[rgba(31,36,51,0.6)] h-[24px] rounded-[4px] w-[64px]" />
                <div className="bg-[rgba(31,36,51,0.6)] rounded-[6px] size-[28px]" />
              </div>
            </div>
          </div>
        </section>
      )}

      {/* Compact Floating State Switcher for QA & Figma Frame Switching */}
      <div
        data-testid="overview-state-bar"
        role="radiogroup"
        aria-label="Overview Viewport State Selector"
        className="fixed bottom-3 right-3 z-40 flex items-center gap-[2px] rounded-[8px] border border-[#1f2433] bg-[#161b26]/95 p-[3px] shadow-lg backdrop-blur-sm"
      >
        {(
          [
            { id: "default", label: "Default" },
            { id: "empty", label: "Empty" },
            { id: "error", label: "Error" },
            { id: "loading", label: "Skeleton" },
          ] as const
        ).map((item) => (
          <button
            key={item.id}
            type="button"
            role="radio"
            aria-checked={activeState === item.id}
            data-testid={`state-toggle-${item.id}`}
            onClick={() => setActiveState(item.id)}
            className={cn(
              "rounded-[5px] px-[8px] py-[4px] font-mono text-[10px] leading-[14px] transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[#00e599]",
              activeState === item.id
                ? "bg-[rgba(0,229,153,0.15)] text-[#00e599] font-semibold border border-[rgba(0,229,153,0.3)]"
                : "text-[#7c839b] hover:text-[#e2e8f0]"
            )}
          >
            {item.label}
          </button>
        ))}
      </div>
    </div>
  );
}

export default function OverviewPage() {
  return (
    <Suspense fallback={<div className="min-h-[calc(100vh-48px)] w-full bg-[#0f131d]" />}>
      <OverviewContent />
    </Suspense>
  );
}
