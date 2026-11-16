
"use client";

import { Gauge, Info, AlertTriangle } from "lucide-react";
import type { WipItem, SprintWipStatus } from "@/types/sprint-wip";

interface WipCongestionProps {
  items: WipItem[];
  status: SprintWipStatus;
}

function WipRow({ item }: { item: WipItem }) {
  const ratio = item.limit > 0 ? item.count / item.limit : 0;
  const overLimit = ratio > 1;
  const progress = Math.min(100, ratio * 100);

  const color =
    overLimit || item.tone === "red"
      ? "#fb7185"
      : item.tone === "green"
        ? "#37d9a5"
        : item.tone === "cyan"
          ? "#43d6df"
          : "#9bd8e9";

  return (
    <div
      className={`rounded-lg border px-3 py-3 ${
        overLimit
          ? "border-[#71333a] bg-[#20212d]"
          : "border-[#252f40] bg-[#191e2a]"
      }`}
    >
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="text-xs font-semibold text-[#dce5f0]">
          {item.name}
        </span>

        <span className="font-mono text-xs text-[#dce5f0]">
          {item.count} / {item.limit}
        </span>
      </div>

      <div className="h-[7px] w-full overflow-hidden rounded-full bg-[#343844]">
        <div
          className="h-full rounded-full transition-all duration-300"
          style={{
            width: `${progress}%`,
            backgroundColor: color,
          }}
        />
      </div>

      <div className="mt-2 flex justify-between gap-2 text-[10px]">
        <span className={overLimit ? "text-[#fb7185]" : "text-[#42e6b2]"}>
          {overLimit
            ? `${item.count - item.limit} over limit`
            : `${Math.round(ratio * 100)}% capacity`}
        </span>

        <span className="text-[#879487]">
          {overLimit
            ? "Limit exceeded"
            : `${Math.max(0, item.limit - item.count)} Slots Open`}
        </span>
      </div>
    </div>
  );
}

export function WipCongestion({ items, status }: WipCongestionProps) {
  const isCritical = status === "critical";
  const isHealthy = status === "healthy";
  const isWarning = status === "warning";

  const hasExceededLimit = items.some(
    (item) => item.limit > 0 && item.count > item.limit,
  );

  const isOverLimit = isCritical || hasExceededLimit;

  const diagnosticMessage = isOverLimit
    ? "One or more workflow stages have exceeded their configured WIP limits. Consider redistributing assignments to reduce bottlenecks."
    : isWarning
      ? "Kanban WIP limits are fully respected across active lanes. The current schedule lag is caused by story size underestimation and scope inflation rather than execution stalls or review bottlenecks."
      : isHealthy
        ? "Kanban WIP limits are strictly respected across all active lanes. Cycle times are optimal with no bottleneck stages detected."
        : "Some workflow stages are approaching their configured capacity. Prioritize finishing active work before starting additional issues.";

  return (
    <section className="min-w-0 rounded-[10px] border border-[#202738] bg-[#191e2a] p-4">
      <div className="mb-1 flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2 text-[#e3eaf4]">
          <Gauge className="size-5 shrink-0 text-[#31c9cf]" />
          <h2 className="text-base font-semibold">WIP Congestion</h2>
        </div>

        <span
          className={`shrink-0 rounded-sm border px-2 py-1 text-center font-mono text-[10px] font-bold leading-tight tracking-wider ${
            isOverLimit
              ? "border-[#71333a] bg-[#321e27] text-[#fb7185]"
              : isHealthy
                ? "border-[#145b4b] bg-[#0d332d] text-[#42e6b2]"
                : "border-[#66502b] bg-[#30291d] text-[#f6c453]"
          }`}
        >
          {isOverLimit ? (
            <>
              LIMIT
              <br />
              EXCEEDED
            </>
          ) : isHealthy ? (
            <>
              WITHIN
              <br />
              LIMITS
            </>
          ) : (
            <>
              AT
              <br />
              RISK
            </>
          )}
        </span>
      </div>

      <p className="mb-4 text-xs text-[#a0adbd]">
        Active stage limits &amp; pipeline flow.
      </p>

      <div className="space-y-2.5">
        {items.map((item) => (
          <WipRow key={item.name} item={item} />
        ))}
      </div>

      {/* Diagnostic Summary */}
      <div
        className={`mt-4 rounded-md p-3 ${
          isOverLimit
            ? "border border-[#71333a] bg-[#171923]"
            : "border border-transparent bg-[#0d121c]"
        }`}
      >
        <div className="mb-2 flex items-center gap-2 text-[10px] font-semibold tracking-wider text-[#dce5f0]">
          {isOverLimit ? (
            <AlertTriangle className="size-3.5 text-[#fb7185]" />
          ) : (
            <Info className="size-3.5 text-[#31c9cf]" />
          )}

          DIAGNOSTIC SUMMARY
        </div>

        <p
          className={`text-[11px] leading-relaxed ${
            isOverLimit ? "text-[#aab7c9]" : "text-[#aeb9b5]"
          }`}
        >
          {diagnosticMessage}
        </p>
      </div>
    </section>
  );
}
