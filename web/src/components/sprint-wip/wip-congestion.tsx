
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
  const width = Math.min(100, ratio * 100);

  const color =
    overLimit || item.tone === "red"
      ? "#fb7185"
      : item.tone === "green"
        ? "#37d9a5"
        : item.tone === "cyan"
          ? "#31c9cf"
          : "#7ba7ff";

  return (
    <div
      className={`rounded-lg border px-3 py-3 ${
        overLimit
          ? "border-[#71333a] bg-[#2b1e29]"
          : "border-[#252f40] bg-[#111925]"
      }`}
    >
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="text-sm font-medium text-[#dce5f0]">
          {item.name}
        </span>

        <span
          className={`font-mono text-xs font-semibold ${
            overLimit ? "text-[#ff7b83]" : "text-[#9aa8bd]"
          }`}
        >
          {item.count} / {item.limit}
        </span>
      </div>

      <div className="h-2 overflow-hidden rounded-full bg-[#263143]">
        <div
          className="h-full rounded-full transition-all"
          style={{
            width: `${width}%`,
            backgroundColor: color,
          }}
        />
      </div>

      <div className="mt-2 flex justify-between text-[11px] text-[#94a1b5]">
        <span>
          {overLimit
            ? `${item.count - item.limit} over limit`
            : `${Math.max(0, item.limit - item.count)} slots available`}
        </span>

        <span>{Math.round(ratio * 100)}%</span>
      </div>
    </div>
  );
}

export function WipCongestion({
  items,
  status,
}: WipCongestionProps) {
  const isCritical = status === "critical";
  const isHealthy = status === "healthy";

  const insight = isCritical
    ? "A workflow stage has exceeded its configured WIP limit. Consider redistributing assignments before pulling more work into the queue."
    : isHealthy
      ? "Work is flowing within configured limits. Keep an eye on review capacity as the sprint approaches completion."
      : "Some workflow stages are approaching their configured capacity. Prioritize finishing active work before starting additional issues.";

  return (
    <section className="min-w-0 rounded-[10px] border border-[#202738] bg-[#151b27] p-4">
      <div className="mb-1 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-[#e3eaf4]">
          <Gauge className="size-5 text-[#31c9cf]" />
          <h2 className="text-base font-semibold">
            WIP Congestion
          </h2>
        </div>

        <span className="rounded border border-[#145b4b] bg-[#0d332d] px-2 py-1 font-mono text-[10px] font-bold text-[#42e6b2]">
          STATUS LIMITS
        </span>
      </div>

      <p className="mb-4 text-xs text-[#94a1b5]">
        Active assignments across workflow stages.
      </p>

      <div className="space-y-2.5">
        {items.map((item) => (
          <WipRow key={item.name} item={item} />
        ))}
      </div>

      <div
        className={`mt-4 rounded-md border p-3 ${
          isCritical
            ? "border-[#71333a] bg-[#321e27]"
            : "border-[#263448] bg-[#101824]"
        }`}
      >
        <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-[#dce5f0]">
          {isCritical ? (
            <AlertTriangle className="size-4 text-[#fb7185]" />
          ) : (
            <Info
              className={`size-4 ${
                isHealthy ? "text-[#31c9cf]" : "text-[#f6c453]"
              }`}
            />
          )}

          WIP insight summary
        </div>

        <p className="text-xs leading-relaxed text-[#aab7c9]">
          {insight}
        </p>
      </div>
    </section>
  );
}
