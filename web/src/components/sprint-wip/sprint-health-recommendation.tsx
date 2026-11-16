
"use client";

import {
  ArrowRight,
  Flame,
  Lightbulb,
  ShieldCheck,
} from "lucide-react";

import type { SprintWipStatus } from "@/types/sprint-wip";

interface SprintHealthRecommendationProps {
  status: SprintWipStatus;
  recommendation: string;
  recommendationDetail: string;
}

export function SprintHealthRecommendation({
  status,
  recommendation,
  recommendationDetail,
}: SprintHealthRecommendationProps) {
  const config = {
    healthy: {
      container: "border-[#194d43] bg-[#102923]",
      iconContainer: "bg-[#174d40] text-[#4be0b2]",
      icon: ShieldCheck,
      badge: "HEALTHY",
      badgeStyle: "border-[#286b59] bg-[#174437] text-[#4be0b2]",
    },
    warning: {
      container: "border-[#70551e] bg-[#2b2519]",
      iconContainer: "bg-[#51401f] text-[#f6c453]",
      icon: Lightbulb,
      badge: "AT RISK",
      badgeStyle: "border-[#70551e] bg-[#42341c] text-[#f6c453]",
    },
    critical: {
      container: "border-[#71333a] bg-[#2d1d26]",
      iconContainer: "bg-[#552a35] text-[#ff858c]",
      icon: Flame,
      badge: "CRITICAL",
      badgeStyle: "border-[#71333a] bg-[#45232e] text-[#ff858c]",
    },
  }[status];

  const Icon = config.icon;

  return (
    <section
      className={`flex flex-col gap-4 rounded-[10px] border px-5 py-4 md:flex-row md:items-center md:justify-between ${config.container}`}
    >
      <div className="flex min-w-0 items-start gap-3.5">
        <div
          className={`mt-0.5 flex size-11 shrink-0 items-center justify-center rounded-lg ${config.iconContainer}`}
        >
          <Icon className="size-5" />
        </div>

        <div className="min-w-0">
          <div className="mb-1.5 flex flex-wrap items-center gap-2.5">
            <p className="text-sm font-semibold text-[#e3eaf4]">
              {recommendation}
            </p>

            <span
              className={`rounded border px-2 py-1 font-mono text-[9px] font-bold tracking-wider ${config.badgeStyle}`}
            >
              {config.badge}
            </span>
          </div>

          <p className="text-xs leading-relaxed text-[#aab7c9]">
            {recommendationDetail}
          </p>
        </div>
      </div>

      <div className="flex shrink-0 items-center gap-2.5">
        <span className="rounded border border-[#344155] bg-[#172131] px-2.5 py-1.5 font-mono text-[9px] text-[#8290a6]">
          PREVIEW ACTION
        </span>

        <button
          type="button"
          disabled
          className="inline-flex h-10 cursor-not-allowed items-center gap-2 rounded-md bg-[#00c995]/70 px-4 text-xs font-bold text-[#071b17] opacity-70"
        >
          View sprint insights
          <ArrowRight className="size-4" />
        </button>
      </div>
    </section>
  );
}
