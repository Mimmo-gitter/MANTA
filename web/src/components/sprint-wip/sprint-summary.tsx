
"use client";

import {
  CalendarDays,
  ShieldAlert,
  TrendingUp,
  TrendingDown,
  Minus,
} from "lucide-react";

interface SprintSummaryProps {
  variance: string;
  blocked: number;
  completion: string;
  completionNote: string;
  status: "healthy" | "warning" | "critical";
}

export function SprintSummary({
  variance,
  blocked,
  completion,
  completionNote,
  status,
}: SprintSummaryProps) {
  const isHealthy = status === "healthy";
  const isCritical = status === "critical";

  const varianceIcon = isHealthy
    ? TrendingDown
    : isCritical
      ? TrendingUp
      : Minus;

  const VarianceIcon = varianceIcon;

  const varianceColor = isHealthy
    ? "text-[#43e0ae]"
    : isCritical
      ? "text-[#ff7b83]"
      : "text-[#f6c453]";

  const metrics = [
    {
      label: "Sprint variance",
      value: variance,
      note: isHealthy
        ? "Ahead of ideal trajectory"
        : "Remaining work vs. ideal",
      tone: varianceColor,
      icon: <VarianceIcon className="size-4" />,
    },
    {
      label: "Blocked issues",
      value: `${blocked} issues`,
      note: "Need attention from team",
      tone:
        blocked > 2 ? "text-[#f6c453]" : "text-[#e4eaf3]",
      icon: <ShieldAlert className="size-4" />,
    },
    {
      label: "Expected completion",
      value: completion,
      note: completionNote,
      tone: isHealthy
        ? "text-[#43e0ae]"
        : "text-[#f6c453]",
      icon: <CalendarDays className="size-4" />,
    },
  ];

  return (
    <div className="mt-4 grid grid-cols-1 divide-y divide-[#242d3d] overflow-hidden rounded-lg border border-[#242d3d] bg-[#111824] sm:grid-cols-3 sm:divide-x sm:divide-y-0">
      {metrics.map((metric) => (
        <div
          key={metric.label}
          className="min-w-0 px-5 py-4"
        >
          <div className="mb-3 flex items-center gap-2 text-[#94a1b5]">
            {metric.icon}
            <span className="font-mono text-[11px] font-semibold uppercase tracking-[0.1em]">
              {metric.label}
            </span>
          </div>

          <div
            className={`text-[26px] font-semibold leading-tight tracking-tight ${metric.tone}`}
          >
            {metric.value}
          </div>

          <p className="mt-2 text-xs leading-relaxed text-[#94a1b5]">
            {metric.note}
          </p>
        </div>
      ))}
    </div>
  );
}
