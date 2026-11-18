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
      label: "Backlog delta",
      value: variance,
      badge: isHealthy ? "Nominal" : isCritical ? "Critical" : "At risk",
      note: isHealthy
        ? "Current pace within ±8 SP tolerance."
        : "Remaining work compared with ideal trajectory.",
      tone: varianceColor,
      badgeTone: isHealthy
        ? "text-[#43e0ae]"
        : isCritical
          ? "text-[#ff7b83]"
          : "text-[#f6c453]",
      icon: <VarianceIcon className="size-3.5" />,
    },
    {
      label: "Blocked issues",
      value: String(blocked),
      badge: blocked > 2 ? "Needs attention" : "Nominal",
      note: "Issues requiring attention.",
      tone: blocked > 2 ? "text-[#f6c453]" : "text-[#e4eaf3]",
      badgeTone: blocked > 2 ? "text-[#f6c453]" : "text-[#43e0ae]",
      icon: <ShieldAlert className="size-3" />,
    },
    {
      label: "Estimated sprint completion",
      value: completion,
      badge: completionNote,
      note: "Based on the current sprint progress.",
      tone: isHealthy ? "text-[#43e0ae]" : "text-[#f6c453]",
      badgeTone: isHealthy ? "text-[#43e0ae]" : "text-[#f6c453]",
      icon: <CalendarDays className="size-3" />,
    },
  ];

  return (
    <div className="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-3">
      {metrics.map((metric) => (
        <div
          key={metric.label}
          className="min-w-0 rounded-md border border-[#1c2533] bg-[#151c28] px-4 py-3.5"
        >
          <div className="mb-2 flex min-h-[24px] items-start gap-1.5 text-[#84918e]">
            {metric.icon}
            <span className="min-h-[24px] font-mono text-[10px] font-medium uppercase leading-3 tracking-[0.12em]">
              {metric.label}
            </span>
          </div>

          <div className="flex min-h-[30px] flex-nowrap items-baseline gap-2">
            <span
              className={`text-[24px] font-semibold leading-none tracking-tight ${metric.tone}`}
            >
              {metric.value}
            </span>

            <span
              className={`text-[11px] font-mono font-medium ${metric.badgeTone}`}
            >
              {metric.badge}
            </span>
          </div>

          <p className="mt-2 min-h-[36px] text-xs leading-relaxed text-[#9aa5a8]">
            {metric.note}
          </p>
        </div>
      ))}
    </div>
  );
}
